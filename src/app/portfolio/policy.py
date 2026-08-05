"""Target weights, factor concentration and rebalancing bands.

The calculation is the product; the charts are a view over it. Everything here
is arithmetic over the trade ledger plus a quote, with no judgement and no
recommendation: a band status says a position is outside its band, never that
it should be sold.

Two decisions worth knowing before reading the code.

**Weights are computed in one base currency.** The ledger deliberately refuses
to sum mixed currencies, because a total that adds dollars to euros is a wrong
number wearing a right one's clothes. Weights need a single denominator, so
they run on `market_value_eur`. When the euro value of any held position is
unavailable, every weight is withheld rather than computed over a subset.

**Factors group by shared drawdown driver, not by sector or revenue.** See the
long comment at the top of `configs/portfolio_policy.yaml`; the choice moves
the headline number by more than twenty points.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from app.config import REPO_ROOT
from app.portfolio.schema import PortfolioView

CASH_TICKER = "CASH"

BandStatus = Literal["in_band", "trim", "add", "no_target"]
TriggerStatus = Literal["ok", "watch", "fired"]
UntargetedStatus = Literal["outside_target", "no_thesis", "exit", "ignore"]


class Trigger(BaseModel):
    """One manually-maintained invalidation condition.

    The app renders these and never evaluates them: a condition like "GPU
    depreciation term changed" has no data feed, and a status the machine
    guessed would be worse than one you set yourself.
    """

    ticker: str
    status: TriggerStatus = "ok"
    condition: str
    next_report: str | None = None


class Policy(BaseModel):
    """Parsed `portfolio_policy.yaml`."""

    base_currency: str = "EUR"
    target_weights: dict[str, float] = {}
    untargeted_status: dict[str, UntargetedStatus] = {}
    factors: dict[str, list[str]] = {}
    ai_factors: list[str] = []
    ai_target_max: float = 0.65
    band_absolute_pp: float = 0.05
    band_relative: float = 0.25
    monthly_trade_budget: int = 5
    triggers: list[Trigger] = []

    def factor_of(self, ticker: str) -> str:
        for name, members in self.factors.items():
            if ticker in members:
                return name
        return "UNMAPPED"


@dataclass
class PositionPolicy:
    """One position measured against its target."""

    ticker: str
    factor: str
    value_base: float
    weight: float
    target: float | None
    status: UntargetedStatus | None
    band_low: float | None
    band_high: float | None
    band_status: BandStatus

    @property
    def deviation_pp(self) -> float | None:
        """Deviation in percentage points; positive means overweight."""
        if self.target is None:
            return None
        return (self.weight - self.target) * 100


@dataclass
class FactorSlice:
    name: str
    value_base: float
    weight: float


@dataclass
class PolicyView:
    """Everything the monitoring page needs, or an explicit reason it is absent."""

    available: bool
    reason: str | None
    base_currency: str
    total_base: float
    positions: list[PositionPolicy] = field(default_factory=list)
    factors: list[FactorSlice] = field(default_factory=list)
    ai_weight: float = 0.0
    ai_target_max: float = 0.65
    missing_targets: list[str] = field(default_factory=list)

    @property
    def ai_excess_pp(self) -> float:
        """Percentage points above the concentration ceiling. Negative is fine."""
        return (self.ai_weight - self.ai_target_max) * 100

    @property
    def most_underweight(self) -> list[PositionPolicy]:
        """Buy order, most underweight first. Excludes anything without a target."""
        scored = [p for p in self.positions if p.deviation_pp is not None]
        return sorted(scored, key=lambda p: p.deviation_pp or 0.0)


@lru_cache(maxsize=1)
def load_policy(path: str | None = None) -> Policy:
    """Read the policy file. Cached; call `load_policy.cache_clear()` in tests."""
    target = Path(path) if path else REPO_ROOT / "configs" / "portfolio_policy.yaml"
    if not target.exists():
        return Policy()
    return Policy.model_validate(yaml.safe_load(target.read_text()) or {})


def bands_for(target: float, policy: Policy) -> tuple[float, float]:
    """The 5/25 band around `target`, taking whichever rule binds tighter.

    Worked example from the policy file: a 6% target gives an absolute band of
    1%-11% and a relative band of 4.5%-7.5%. The relative one is tighter, so it
    wins, and a position at 10.8% is flagged for trimming.
    """
    absolute = (target - policy.band_absolute_pp, target + policy.band_absolute_pp)
    relative = (target * (1 - policy.band_relative), target * (1 + policy.band_relative))
    return (max(absolute[0], relative[0]), min(absolute[1], relative[1]))


def build_policy_view(
    view: PortfolioView,
    *,
    policy: Policy | None = None,
    cash_base: float = 0.0,
) -> PolicyView:
    """Measure `view` against the policy, in the policy's base currency.

    `cash_base` is the uninvested balance, which carries a real target and would
    otherwise make every other weight read high.
    """
    policy = policy or load_policy()

    priced = [p for p in view.open_positions if p.market_value_eur is not None]
    unpriced = [p for p in view.open_positions if p.market_value_eur is None]
    if unpriced:
        return PolicyView(
            available=False,
            reason=(
                f"No {policy.base_currency} value for "
                f"{', '.join(sorted(p.ticker for p in unpriced))}. Weights need one "
                "denominator, so none are shown."
            ),
            base_currency=policy.base_currency,
            total_base=0.0,
        )

    total = sum(p.market_value_eur or 0.0 for p in priced) + cash_base
    if total <= 0:
        return PolicyView(
            available=False,
            reason="Portfolio has no value to weight.",
            base_currency=policy.base_currency,
            total_base=0.0,
        )

    rows: list[PositionPolicy] = []
    for p in priced:
        rows.append(_measure(p.ticker, p.market_value_eur or 0.0, total, policy))
    if cash_base > 0:
        rows.append(_measure(CASH_TICKER, cash_base, total, policy))

    # Targets you hold nothing of are still deviations, and they are the whole
    # buy order while the portfolio is being migrated toward the policy.
    held = {r.ticker for r in rows}
    missing = [t for t in policy.target_weights if t not in held]
    for ticker in missing:
        rows.append(_measure(ticker, 0.0, total, policy))

    by_factor: dict[str, float] = {}
    for r in rows:
        by_factor[r.factor] = by_factor.get(r.factor, 0.0) + r.value_base
    factors = [
        FactorSlice(name=name, value_base=value, weight=value / total)
        for name, value in sorted(by_factor.items(), key=lambda kv: -kv[1])
        if value > 0
    ]
    ai_weight = sum(f.weight for f in factors if f.name in policy.ai_factors)

    return PolicyView(
        available=True,
        reason=None,
        base_currency=policy.base_currency,
        total_base=total,
        positions=sorted(rows, key=lambda r: -r.value_base),
        factors=factors,
        ai_weight=ai_weight,
        ai_target_max=policy.ai_target_max,
        missing_targets=missing,
    )


def _measure(ticker: str, value: float, total: float, policy: Policy) -> PositionPolicy:
    weight = value / total
    target = policy.target_weights.get(ticker)
    status = policy.untargeted_status.get(ticker)

    if target is None:
        return PositionPolicy(
            ticker=ticker,
            factor=policy.factor_of(ticker),
            value_base=value,
            weight=weight,
            target=None,
            status=status,
            band_low=None,
            band_high=None,
            band_status="no_target",
        )

    low, high = bands_for(target, policy)
    if weight > high:
        band = "trim"
    elif weight < low:
        band = "add"
    else:
        band = "in_band"

    return PositionPolicy(
        ticker=ticker,
        factor=policy.factor_of(ticker),
        value_base=value,
        weight=weight,
        target=target,
        status=status,
        band_low=low,
        band_high=high,
        band_status=band,  # type: ignore[arg-type]
    )


__all__ = [
    "CASH_TICKER",
    "FactorSlice",
    "PolicyView",
    "Policy",
    "PositionPolicy",
    "Trigger",
    "bands_for",
    "build_policy_view",
    "load_policy",
]
