"""Layer A screening metrics for the supply-constraint screen.

Eight metrics, computed against a `MarginHistory` (Task 2) plus a handful of
point-in-time fundamentals (revenue, market cap, PP&E, cash, operating cash
flow, analyst coverage). Every formula is fixed by the task brief — this
module implements them exactly and does not "improve" them. Thresholds live
in `configs/supply_screen.yaml`, loaded via `load_thresholds()`, never
hardcoded here.

Three correctness rules worth restating because they are easy to get quietly
wrong:

**Thin history is refused, not guessed.** A percentile computed over fewer
than `Thresholds.min_quarters_history` (24, six years, in
`configs/supply_screen.yaml`) quarters is meaningless — see
`app.supply.fundamentals` for why (SanDisk's 2025 spin-out has 12 quarters
of EDGAR history and would otherwise be percentiled against its own
two-year window). Below the floor, `compute_metrics` sets every
history-dependent field to `None` and flags `sufficient_history=False`;
`passes()` surfaces that as an explicit, singular reason rather than
silently comparing `None` against a threshold. The YAML value is
authoritative — `compute_metrics` reads the floor from the `Thresholds`
object passed to it. `MIN_QUARTERS` is only a documented fallback for
callers that don't have a `Thresholds` yet.

**Survivability is infinite for a cash-generative company, not zero.** Burn
is the mean of NEGATIVE quarterly operating cash flows. If a company has no
negative quarters, it is not burning cash and "quarters until it runs out of
cash" is undefined in the sense that matters here — represented as `None`.
`None` survives Pydantic/JSON serialisation cleanly (as `null`), unlike
`float("inf")`, which is not valid JSON. `passes()` treats `None` as always
clearing the survivability threshold.

**Missing analyst coverage does not fail the screen.** The `analyst_count`
criterion exists to surface UNDER-covered names; a ticker yfinance reports
no `numberOfAnalystOpinions` for is either genuinely uncovered (the most
interesting case for this screen) or has missing data, and Layer A is a
coarse net that Layer B refines. Excluding it would drop exactly the names
this screen hunts for. A missing count is recorded on `SupplyMetrics.caveats`
so a reader can see the criterion was unverifiable, but it never fails
`passes()`.

**Analyst coverage is a ranking input, not a gate.** It used to fail
`passes()` outright above `analyst_count_max`, which excluded businesses
that qualified on every fundamental criterion for a non-fundamental reason
(Westlake/WLK: 11.5th percentile, 22.6pp headroom, 0.27 torque, 25 quarters
of runway — rejected purely because 15 analysts cover it). Coverage is a
DISCOVERY signal — thin coverage means fewer people are looking, which is
what makes a name interesting, not what makes the business good — so it is
now folded into `coverage_multiplier` and consumed by Layer B's score
(`app.supply.layer_b`) instead of gating Layer A. `analyst_count` itself is
still computed and reported for transparency.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

from app.supply.fundamentals import MarginHistory

REPO_ROOT = Path(__file__).resolve().parents[3]

# Six years of quarterly filings — the documented DEFAULT floor below which a
# percentile or a stdev over the history is statistically meaningless. This
# is only used when `compute_metrics` is called without a `Thresholds`
# object; whenever one is supplied, `thresholds.min_quarters_history` is the
# authoritative gate (so editing configs/supply_screen.yaml actually changes
# behaviour, not just a message string).
MIN_QUARTERS = 24

# DEFAULT coverage reference point for `coverage_multiplier`, used only when
# `compute_metrics` is called without a `Thresholds` object. Whenever one is
# supplied, `thresholds.analyst_count_max` is authoritative — same fallback
# pattern as `MIN_QUARTERS` above.
ANALYST_COUNT_REFERENCE = 12


def coverage_multiplier(analyst_count: int | None, reference: float) -> float:
    """Ranking multiplier that rewards thin analyst coverage.

    `1.0` (neutral — never reward missing data) when `analyst_count` is
    unknown. Otherwise `min(2.0, reference / max(analyst_count, 1))`, floored
    at `0.5`: `analyst_count == reference` -> 1.0, half the reference -> 2.0
    (capped), double the reference -> 0.5 (floored, not lower).
    """
    if analyst_count is None:
        return 1.0
    return min(2.0, max(0.5, reference / max(analyst_count, 1)))


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

    # Ranking input for Layer B, not a gate: min(2.0, 12.0 / max(analyst_count, 1)),
    # floored at 0.5. 1.0 (neutral) when analyst_count is unknown — missing data
    # is never rewarded. See module docstring "Analyst coverage is a ranking
    # input, not a gate."
    coverage_multiplier: float = 1.0

    # Non-fatal notes: a criterion that couldn't be evaluated (e.g. missing
    # analyst coverage) rather than one that failed. `passes()` never fails
    # on account of a caveat alone.
    caveats: list[str] = Field(default_factory=list)


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
    # No longer a rejection threshold (see module docstring). This is now the
    # coverage REFERENCE POINT for `coverage_multiplier`: analyst_count ==
    # analyst_count_max yields a multiplier of 1.0 (neutral); fewer analysts
    # scales the multiplier up (thin coverage rewarded), more scales it down.
    analyst_count_max: int
    min_quarters_history: int
    # Which universe the screen runs over. Deliberately not the S&P list:
    # a screen built to find thin analyst coverage cannot be satisfied by
    # the most-covered companies in existence. See the YAML for the note.
    universe_path: str = "configs/supply_universe.csv"


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
    thresholds: Thresholds | None = None,
) -> SupplyMetrics:
    """Compute the eight Layer A metrics.

    `margin_history` supplies the historical quarterly GM distribution.
    `ttm_gross_margin` / `ttm_revenue` are the current trailing-twelve-month
    figures being scored against that distribution — deliberately separate
    from `margin_history.gross_margins`, which is the per-quarter series,
    not a trailing-twelve-month figure.

    `thresholds`, if given, supplies the history-sufficiency floor
    (`thresholds.min_quarters_history`) — pass the same `Thresholds` you'll
    later hand to `passes()` so the two agree. Falls back to the module
    default `MIN_QUARTERS` when omitted.
    """
    quarters = margin_history.quarters
    min_quarters = thresholds.min_quarters_history if thresholds is not None else MIN_QUARTERS
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

    caveats: list[str] = []
    if analyst_count is None:
        caveats.append(
            "analyst_count unavailable — criterion unverifiable, not evaluated "
            "(uncovered or missing data)"
        )

    reference = (
        thresholds.analyst_count_max if thresholds is not None else ANALYST_COUNT_REFERENCE
    )

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
        coverage_multiplier=coverage_multiplier(analyst_count, reference),
        caveats=caveats,
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
    # analyst_count is no longer a gate (see module docstring "Analyst
    # coverage is a ranking input, not a gate") — it's reported and caveated
    # on SupplyMetrics but never fails passes(). Layer B's coverage_multiplier
    # is where it affects ranking.

    return (len(reasons) == 0, reasons)


__all__ = [
    "ANALYST_COUNT_REFERENCE",
    "MIN_QUARTERS",
    "SupplyMetrics",
    "Thresholds",
    "compute_metrics",
    "coverage_multiplier",
    "load_thresholds",
    "passes",
]
