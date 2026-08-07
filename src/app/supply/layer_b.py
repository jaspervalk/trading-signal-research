"""Layer B: join Layer A results to the constraint registry, and score.

Layer A (`app.supply.metrics` / `app.supply.screen`) finds businesses with
margin headroom against their own history. Layer B answers the next
question: is that headroom being withheld by a supply constraint someone can
name, date, and defend? The join key is `ConstraintExposure.ticker` against
a ticker's Layer A `SupplyMetrics`.

**The cut.** This module iterates constraints' `exposures` lists, not the
Layer A universe — a ticker Layer A scored but that carries no exposure to
any constraint in `configs/supply_constraints.yaml` never produces a
`LayerBResult` at all. That absence *is* the cut the brief asks for, not a
bug to work around by inventing an exposure.

**Every component travels with the score.** The brief is explicit that a
score without visible inputs is a black box, so `LayerBResult` carries every
factor of the product — `earnings_torque`, `deficit_pct`,
`expansion_lead_factor`, `revenue_exposure_pct`, `confidence_weight`,
`coverage_multiplier` — as fields, not folded away after multiplying.

**Stale constraints are included, not dropped.** `Constraint.is_stale`
(`app.supply.constraints`) is carried onto every `LayerBResult` the
constraint produces so a reader can discount or exclude it downstream, but
this module never drops a row for staleness alone — Layer C's inflection
triggers are where a stale thesis gets acted on, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.supply.constraints import Constraint, ConstraintExposure
from app.supply.metrics import SupplyMetrics

# high=1.0, medium=0.6, low=0.3 — fixed by the brief's score formula.
CONFIDENCE_WEIGHTS: dict[str, float] = {"high": 1.0, "medium": 0.6, "low": 0.3}

# Reference points fixed by the brief's score formula. Unlike Layer A's
# `configs/supply_screen.yaml` thresholds, these are not tunable config —
# the formula itself, verbatim from the brief, IS the spec.
DEFICIT_PCT_REFERENCE = 0.05
EXPANSION_LEAD_REFERENCE_MONTHS = 18.0
EXPANSION_LEAD_CAP = 2.0


@dataclass(frozen=True)
class LayerBResult:
    """One (ticker, constraint) score, with every input that produced it.

    `score` is `None` when Layer A has no usable `earnings_torque` for
    `ticker` (never screened, or insufficient history) — `reason` explains
    why, mirroring the "recorded, not dropped" discipline
    `app.supply.screen.ScreenResult` already uses for Layer A failures.
    """

    ticker: str
    constraint_id: str
    market: str
    score: Optional[float]
    reason: Optional[str]

    # Every factor of the score formula, visible on its own. deficit_pct /
    # expansion_lead_months / expansion_lead_factor are Optional because a
    # constraint may document real capacity destruction with no credible
    # PROJECTED deficit (see app.supply.constraints.Constraint) — that
    # constraint can never produce a score (see `_score_one`), but it still
    # produces a `LayerBResult` with `score=None` and a `reason`, same
    # "recorded, not dropped" discipline as everywhere else in Layer A/B.
    earnings_torque: Optional[float]
    deficit_pct: Optional[float]
    expansion_lead_months: Optional[float]
    expansion_lead_factor: Optional[float]  # min(expansion_lead_months / 18.0, 2.0)
    revenue_exposure_pct: float
    confidence: str
    confidence_weight: float
    coverage_multiplier: float

    # Context carried through for the reader, not part of the product.
    is_pure_play: bool
    is_stale: bool
    deficit_source: str
    exposure_source: str


def _score_one(
    ticker: str,
    constraint: Constraint,
    exposure: ConstraintExposure,
    metrics: Optional[SupplyMetrics],
) -> LayerBResult:
    confidence_weight = CONFIDENCE_WEIGHTS[constraint.confidence]
    expansion_lead_factor: Optional[float] = (
        min(
            constraint.expansion_lead_months / EXPANSION_LEAD_REFERENCE_MONTHS,
            EXPANSION_LEAD_CAP,
        )
        if constraint.expansion_lead_months is not None
        else None
    )

    earnings_torque = metrics.earnings_torque if metrics is not None else None
    # Missing Layer A coverage is neutral, same discipline as
    # `coverage_multiplier`'s own "unknown -> 1.0" rule in app.supply.metrics.
    coverage_multiplier = metrics.coverage_multiplier if metrics is not None else 1.0

    score: Optional[float]
    reason: Optional[str]
    if earnings_torque is None:
        score = None
        reason = (
            "no earnings_torque from Layer A ("
            + ("ticker not screened" if metrics is None else "insufficient history")
            + ")"
        )
    elif constraint.deficit_pct is None or expansion_lead_factor is None:
        # Documented capacity destruction with no credible PROJECTED deficit
        # is a legitimate, honest state (see Constraint docstring) — it just
        # can't feed a score that multiplies by a deficit_pct that doesn't
        # exist. Recorded, not dropped, same as every other unscoreable case.
        score = None
        reason = (
            "constraint has no credible deficit_pct / expansion_lead_months — "
            "documented capacity destruction only, no projected deficit "
            "(see capacity_history / counter_evidence)"
        )
    else:
        score = (
            earnings_torque
            * (constraint.deficit_pct / DEFICIT_PCT_REFERENCE)
            * expansion_lead_factor
            * exposure.revenue_exposure_pct
            * confidence_weight
            * coverage_multiplier
        )
        reason = None

    return LayerBResult(
        ticker=ticker,
        constraint_id=constraint.id,
        market=constraint.market,
        score=score,
        reason=reason,
        earnings_torque=earnings_torque,
        deficit_pct=constraint.deficit_pct,
        expansion_lead_months=constraint.expansion_lead_months,
        expansion_lead_factor=expansion_lead_factor,
        revenue_exposure_pct=exposure.revenue_exposure_pct,
        confidence=constraint.confidence,
        confidence_weight=confidence_weight,
        coverage_multiplier=coverage_multiplier,
        is_pure_play=exposure.is_pure_play,
        is_stale=constraint.is_stale,
        deficit_source=constraint.deficit_source,
        exposure_source=exposure.exposure_source,
    )


def run_layer_b(
    constraints: list[Constraint],
    metrics_by_ticker: dict[str, SupplyMetrics],
) -> list[LayerBResult]:
    """Join every constraint's exposures to Layer A metrics and score them.

    `metrics_by_ticker` is keyed by uppercase ticker — build it from
    `app.supply.screen.run_supply_screen`'s output, e.g.
    `{r.ticker.upper(): r.metrics for r in results if r.metrics is not None}`.

    Returns one `LayerBResult` per (constraint, exposure) pair, in
    `constraints` order and each constraint's `exposures` order. A ticker
    with no exposure to any constraint never appears — see module docstring
    "The cut." A ticker WITH exposure but no Layer A metrics still appears,
    with `score=None` and a `reason`, same "recorded, not dropped"
    discipline as Layer A's own `ScreenResult`.
    """
    results: list[LayerBResult] = []
    for constraint in constraints:
        for exposure in constraint.exposures:
            ticker = exposure.ticker.upper()
            metrics = metrics_by_ticker.get(ticker)
            results.append(_score_one(ticker, constraint, exposure, metrics))
    return results


__all__ = [
    "CONFIDENCE_WEIGHTS",
    "DEFICIT_PCT_REFERENCE",
    "EXPANSION_LEAD_CAP",
    "EXPANSION_LEAD_REFERENCE_MONTHS",
    "LayerBResult",
    "run_layer_b",
]
