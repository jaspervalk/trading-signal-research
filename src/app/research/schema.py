"""Pydantic schemas for the entry/exit research feature.

All numeric levels are anchored to deterministic candidates (`exits.py`,
`entry.py`); the LLM picks among them by reference (`entry_kind` + integer
indices into the candidate lists), and the system resolves picks verbatim.
The LLM never invents a level and no longer scales them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Vocabulary kept small and string-typed so the JSON shape is frontend-agnostic.
PLAN_MODES = ("quick", "deep")
CONFIDENCE = ("low", "medium", "high")
TIMEFRAMES = ("1-3d", "5-15d", "2-6w")

# Multi-lens analyst panel (see docs/entry-exit-research-plan.md §multi-lens).
# Each lens independently assesses the setup; the union is what drives
# higher-conviction reads. In Quick mode all four come from a single LLM
# call (1 lens-block in the same prompt). In Deep mode (Phase 2) each is
# replaced by a dedicated agent with its own context.
LENS_NAMES = (
    "quantitative",     # statistical edge, mean reversion, momentum, factor exposure
    "fundamental",      # forward earnings, sector comps, growth, valuation
    "sentiment_macro",  # creator claims, flow / positioning, macro regime, catalysts
    "contrarian_risk",  # what could go wrong, crowded trade, downside scenarios
)

# Default trim allocation used when computing the blended Plan R/R from
# (primary, runner). 1/3 trim at primary, 2/3 ride the runner. See Q3 of the
# multi-lens memo for why R/R stays purely technical.
DEFAULT_PRIMARY_FRACTION = 1.0 / 3.0


class ZoneBand(BaseModel):
    """A price band. `low <= high`. The `method` records how it was computed
    (e.g. "63-bar high + 0.5×ATR") and `rationale` is the LLM's short reason
    for picking this band over alternatives.
    """

    low: float
    high: float
    method: str
    rationale: str = ""


class CandidateLevels(BaseModel):
    """Deterministic level candidates fed into the LLM. The LLM picks among
    them by reference (`entry_kind` + integer indices); the system resolves
    picks verbatim. The LLM cannot invent or scale a level.

    Populated by `research.context.gather()` from the existing analysis
    pipeline (entry.py + new exits.py).
    """

    breakout_entry: ZoneBand | None = None
    pullback_entry: ZoneBand | None = None
    primary_exit_candidates: list[ZoneBand] = Field(default_factory=list)
    runner_exit_candidates: list[ZoneBand] = Field(default_factory=list)
    invalidation_candidates: list[float] = Field(default_factory=list)
    last_close: float | None = None
    atr_14: float | None = None


class AgentNote(BaseModel):
    """One line of reasoning from an analyst agent (Phase 2). Phase 1 emits
    a single `AgentNote` from the Quick call so the audit trail is uniform.
    """

    agent: str  # "quick" | "technical" | "sentiment" | "news" | "judge"
    confidence: str  # one of CONFIDENCE
    bull_points: list[str] = Field(default_factory=list)
    bear_points: list[str] = Field(default_factory=list)
    note: str = ""


class LensView(BaseModel):
    """One analyst-lens read on the trade setup.

    Each lens is independent and conviction-rated. Frontend renders these
    as a 4-panel grid below the trade ticket — the goal is to make
    competing perspectives legible side-by-side rather than collapse them
    into one verdict. See docs/entry-exit-research-plan.md.
    """

    name: str  # one of LENS_NAMES
    conviction: str  # one of CONFIDENCE
    summary: str = ""  # one-line headline read
    points: list[str] = Field(default_factory=list)  # 2-4 supporting bullets
    direction: str = "neutral"  # "bullish" | "bearish" | "neutral" — vote on the trade
    # Cross-lens debate (Phase 2). Populated only when round 2 ran AND
    # the analyst chose to revise. Absence means "round 2 didn't apply
    # or analyst declined to revise"; original summary / points stand.
    revised_summary: str | None = None
    revised_points: list[str] = Field(default_factory=list)
    responded_to: list[str] = Field(default_factory=list)  # lens names this analyst engaged with


@dataclass
class Picks:
    """The LLM's categorical picks, used to mark the chosen combo.

    Cross-cutting type: emitted by `quick.py` (and the deep-mode judge in
    `judge.py`) and consumed by `rr_distribution.compute_rr_distribution`.
    Lives in `schema.py` alongside `RRCombo` / `RRDistribution`; re-exported
    by `rr_distribution.py` for back-compat with existing imports.
    """

    entry_kind: str  # "breakout" | "pullback"
    primary_index: int
    runner_index: int | None
    invalidation_index: int


class RRCombo(BaseModel):
    """One R/R combination across the candidate-level grid.

    A single `EntryExitPlan` may have ~10-30 of these. The LLM-chosen
    combo is marked `is_chosen=True`; all others are alternates the user
    can compare against to gauge the plan's sensitivity to level picks.
    """

    entry_kind: str  # "breakout" | "pullback"
    primary_index: int
    runner_index: int | None = None
    invalidation_index: int

    # Human-readable labels (derived from each ZoneBand.method / candidate).
    # Keeps the frontend from having to re-resolve indices into the candidate list.
    entry_label: str
    primary_label: str
    runner_label: str | None = None
    invalidation_label: str

    rr_primary: float
    rr_runner: float | None = None
    rr_blended: float

    is_chosen: bool = False


class RRDistribution(BaseModel):
    """The R/R distribution across all candidate combinations.

    `min_rr` / `max_rr` define the honest range of R/Rs supported by the
    deterministic candidates; `median_rr` is the center of mass. The chosen
    combo (LLM's pick) is one entry inside `combos` with `is_chosen=True`.
    """

    min_rr: float
    median_rr: float
    max_rr: float
    n_combos: int
    combos: list[RRCombo] = Field(default_factory=list)


def blended_risk_reward(
    *,
    rr_primary: float | None,
    rr_runner: float | None,
    primary_fraction: float = DEFAULT_PRIMARY_FRACTION,
) -> float | None:
    """Weighted average R/R across primary + runner exits.

    Default allocation: 1/3 trims at the first take-profit, 2/3 holds for
    the runner. If the runner R/R is None, falls back to the primary R/R.
    Returns None if neither slice has a defined R/R.
    """
    if rr_primary is None and rr_runner is None:
        return None
    if rr_runner is None:
        return rr_primary
    if rr_primary is None:
        return rr_runner
    runner_fraction = 1.0 - primary_fraction
    return round(rr_primary * primary_fraction + rr_runner * runner_fraction, 2)


class EntryExitPlan(BaseModel):
    """Structured entry/pullback/exit/stop plan for a single ticker.

    Returned by both `quick.run()` and `deep.run()`. Numeric fields are
    deterministic candidates resolved verbatim from the LLM's categorical
    picks (`entry_kind` + integer indices); qualitative fields are
    LLM-driven.
    """

    ticker: str
    as_of: datetime

    # Numeric levels — resolved verbatim from the LLM's categorical picks
    # against the deterministic candidates (no scaling, no clamping).
    entry_zone: ZoneBand
    pullback_entry_zone: ZoneBand | None = None
    exit_zone_primary: ZoneBand
    exit_zone_runner: ZoneBand | None = None
    invalidation: float
    risk_reward_primary: float
    risk_reward_runner: float | None = None
    # Blended Plan R/R — weighted average across primary + runner with the
    # default trim allocation. THIS is the headline R/R for sizing decisions.
    plan_r_r_blended: float | None = None
    # The R/R range supported by the candidate-level grid. The headline
    # `plan_r_r_blended` is one entry within `r_r_distribution.combos`
    # marked `is_chosen=True`. Kept None for legacy plans (pre-2026-05-08
    # variance fix); always populated for new runs.
    r_r_distribution: RRDistribution | None = None

    # Qualitative — LLM-driven.
    confidence: str  # one of CONFIDENCE; bounded by upstream rubric
    timeframe: str  # one of TIMEFRAMES
    bull_case: list[str] = Field(default_factory=list)
    bear_case: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)

    # Multi-lens analyst panel — independent reads, side-by-side. Empty list
    # in legacy plans (pre-2026-05-08); always populated for new runs.
    lenses: list[LensView] = Field(default_factory=list)

    # Audit trail.
    mode: str  # one of PLAN_MODES
    cost_usd: float
    duration_ms: int
    sources_used: list[str] = Field(default_factory=list)
    agent_trace: list[AgentNote] = Field(default_factory=list)
    disclaimer: str = (
        "Research output. Decision support, not investment advice. "
        "The user evaluates and pulls the trigger; this system does not execute orders."
    )


__all__ = [
    "AgentNote",
    "CandidateLevels",
    "CONFIDENCE",
    "DEFAULT_PRIMARY_FRACTION",
    "EntryExitPlan",
    "LENS_NAMES",
    "LensView",
    "PLAN_MODES",
    "Picks",
    "RRCombo",
    "RRDistribution",
    "TIMEFRAMES",
    "ZoneBand",
    "blended_risk_reward",
]
