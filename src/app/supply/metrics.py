"""Layer A screening metrics for the supply-constraint screen.

Eight metrics, computed against a `MarginHistory` (Task 2) plus a handful of
point-in-time fundamentals (revenue, market cap, PP&E, cash, operating cash
flow, analyst coverage). Every formula is fixed by the task brief — this
module implements them exactly and does not "improve" them. Thresholds live
in `configs/supply_screen.yaml`, loaded via `load_thresholds()`, never
hardcoded here.

Two correctness rules worth restating because they are easy to get quietly
wrong:

**Thin history is refused, not guessed.** A percentile computed over fewer
than `MIN_QUARTERS` (24, six years) quarters is meaningless — see
`app.supply.fundamentals` for why (SanDisk's 2025 spin-out has 12 quarters
of EDGAR history and would otherwise be percentiled against its own
two-year window). Below the floor, `compute_metrics` sets every
history-dependent field to `None` and flags `sufficient_history=False`;
`passes()` surfaces that as an explicit, singular reason rather than
silently comparing `None` against a threshold.

**Survivability is infinite for a cash-generative company, not zero.** Burn
is the mean of NEGATIVE quarterly operating cash flows. If a company has no
negative quarters, it is not burning cash and "quarters until it runs out of
cash" is undefined in the sense that matters here — represented as `None`.
`None` survives Pydantic/JSON serialisation cleanly (as `null`), unlike
`float("inf")`, which is not valid JSON. `passes()` treats `None` as always
clearing the survivability threshold.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from app.supply.fundamentals import MarginHistory

REPO_ROOT = Path(__file__).resolve().parents[3]

# Six years of quarterly filings. Below this, a percentile or a stdev over
# the history is statistically meaningless — refuse rather than guess.
MIN_QUARTERS = 24


class SupplyMetrics(BaseModel):
    """The eight Layer A screening metrics for one ticker at one point in time.

    History-dependent fields (`gm_percentile`, `margin_headroom_pp`,
    `earnings_torque`, `gm_volatility_pp`) are `None` when
    `sufficient_history` is `False` — never a silently-computed number over
    a too-short window.
    """

    quarters_of_history: int
    sufficient_history: bool

    gm_percentile: Optional[float] = None  # share of historical quarterly GMs below current TTM GM
    margin_headroom_pp: Optional[float] = None  # max(historical GM) - current TTM GM, in pp
    earnings_torque: Optional[float] = None  # margin_headroom_fraction * ttm_revenue / market_cap
    gm_volatility_pp: Optional[float] = None  # population stdev of historical GMs, in pp

    capital_intensity: float  # ppe / ttm_revenue

    # Quarters of cash runway. None means infinite (cash-generative company,
    # i.e. no negative quarterly operating cash flow to compute a burn rate from).
    survivability_quarters: Optional[float] = None

    analyst_count: Optional[int] = None  # yfinance numberOfAnalystOpinions


class Thresholds(BaseModel):
    """Pre-registered pass/fail thresholds. Loaded from
    `configs/supply_screen.yaml` — never construct this from hardcoded
    values in application code (only tests do that, deliberately, to prove
    `passes()` is threshold-agnostic)."""

    gm_percentile_max: float
    margin_headroom_min_pp: float
    earnings_torque_min: float
    gm_volatility_min_pp: float
    capital_intensity_min: float
    survivability_quarters_min: float
    analyst_count_max: int
    min_quarters_history: int


def load_thresholds(path: Path | None = None) -> Thresholds:
    """Load `Thresholds` from `configs/supply_screen.yaml` (or `path`)."""
    thresholds_path = path or (REPO_ROOT / "configs" / "supply_screen.yaml")
    with Path(thresholds_path).open() as f:
        data = yaml.safe_load(f) or {}
    return Thresholds(**data)


def compute_metrics(
    margin_history: MarginHistory,
    *,
    ttm_gross_margin: float,
    ttm_revenue: float,
    market_cap: float,
    ppe: float,
    cash: float,
    ttm_operating_cash_flow: float,
    quarterly_operating_cash_flows: list[float],
    analyst_count: int | None = None,
    min_quarters: int = MIN_QUARTERS,
) -> SupplyMetrics:
    """Compute the eight Layer A metrics.

    `margin_history` supplies the historical quarterly GM distribution.
    `ttm_gross_margin` / `ttm_revenue` are the current trailing-twelve-month
    figures being scored against that distribution — deliberately separate
    from `margin_history.gross_margins`, which is the per-quarter series,
    not a trailing-twelve-month figure.
    """
    quarters = margin_history.quarters
    sufficient = quarters >= min_quarters

    gm_percentile: float | None = None
    margin_headroom_pp: float | None = None
    earnings_torque: float | None = None
    gm_volatility_pp: float | None = None

    if sufficient:
        history = margin_history.gross_margins
        gm_percentile = sum(1 for gm in history if gm < ttm_gross_margin) / len(history)
        margin_headroom_pp = (max(history) - ttm_gross_margin) * 100
        earnings_torque = (margin_headroom_pp / 100) * ttm_revenue / market_cap
        gm_volatility_pp = statistics.pstdev(history) * 100

    capital_intensity = ppe / ttm_revenue

    negative_quarters = [ocf for ocf in quarterly_operating_cash_flows if ocf < 0]
    survivability_quarters: float | None
    if not negative_quarters:
        # Not burning cash -> infinite runway.
        survivability_quarters = None
    else:
        burn = abs(statistics.mean(negative_quarters))
        survivability_quarters = (cash + max(ttm_operating_cash_flow, 0)) / burn

    return SupplyMetrics(
        quarters_of_history=quarters,
        sufficient_history=sufficient,
        gm_percentile=gm_percentile,
        margin_headroom_pp=margin_headroom_pp,
        earnings_torque=earnings_torque,
        gm_volatility_pp=gm_volatility_pp,
        capital_intensity=capital_intensity,
        survivability_quarters=survivability_quarters,
        analyst_count=analyst_count,
    )


def passes(metrics: SupplyMetrics, thresholds: Thresholds) -> tuple[bool, list[str]]:
    """Check `metrics` against `thresholds`, returning every failing reason.

    A near-miss list is only useful if every failing criterion is visible,
    not just the first one hit — so this never short-circuits (except for
    the insufficient-history case, which makes every other comparison
    meaningless and is reported on its own).
    """
    if not metrics.sufficient_history:
        return False, [
            f"insufficient_history: {metrics.quarters_of_history} quarters "
            f"(need >= {thresholds.min_quarters_history})"
        ]

    reasons: list[str] = []

    if metrics.gm_percentile is None or metrics.gm_percentile > thresholds.gm_percentile_max:
        reasons.append(
            f"gm_percentile={metrics.gm_percentile} > {thresholds.gm_percentile_max}"
        )
    if (
        metrics.margin_headroom_pp is None
        or metrics.margin_headroom_pp < thresholds.margin_headroom_min_pp
    ):
        reasons.append(
            f"margin_headroom_pp={metrics.margin_headroom_pp} < {thresholds.margin_headroom_min_pp}"
        )
    if metrics.earnings_torque is None or metrics.earnings_torque < thresholds.earnings_torque_min:
        reasons.append(
            f"earnings_torque={metrics.earnings_torque} < {thresholds.earnings_torque_min}"
        )
    if (
        metrics.gm_volatility_pp is None
        or metrics.gm_volatility_pp < thresholds.gm_volatility_min_pp
    ):
        reasons.append(
            f"gm_volatility_pp={metrics.gm_volatility_pp} < {thresholds.gm_volatility_min_pp}"
        )
    if metrics.capital_intensity < thresholds.capital_intensity_min:
        reasons.append(
            f"capital_intensity={metrics.capital_intensity} < {thresholds.capital_intensity_min}"
        )
    # None means infinite runway -> always clears this threshold.
    if (
        metrics.survivability_quarters is not None
        and metrics.survivability_quarters < thresholds.survivability_quarters_min
    ):
        reasons.append(
            f"survivability_quarters={metrics.survivability_quarters} < {thresholds.survivability_quarters_min}"
        )
    if metrics.analyst_count is None or metrics.analyst_count > thresholds.analyst_count_max:
        reasons.append(
            f"analyst_count={metrics.analyst_count} > {thresholds.analyst_count_max}"
        )

    return (len(reasons) == 0, reasons)


__all__ = [
    "MIN_QUARTERS",
    "SupplyMetrics",
    "Thresholds",
    "compute_metrics",
    "load_thresholds",
    "passes",
]
