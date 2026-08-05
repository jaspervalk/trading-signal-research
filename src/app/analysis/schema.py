"""Pydantic schema for the ticker research view.

The shape returned by `GET /tickers/{ticker}/research` and by the
`tsr research <TICKER>` CLI. Every panel here corresponds to a section
of the user-facing decision-support output.

Design decisions:
- Every numeric field is `float | None` so missing data degrades gracefully.
- Every classification field is a string enum (not a Python `Enum`) so the
  shape is JSON-stable and frontend-agnostic.
- Status / setup / style enums are documented inline next to the field.
- The view embeds *summaries* of transcript signals, not the raw rows —
  the existing `/tickers/{t}/signals` / `/claims` / `/coverage` endpoints
  remain the source of truth for the raw evidence.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# String enum constants (kept as module-level strings for JSON stability).

SETUP_TYPES = (
    "strong_uptrend",
    "uptrend_pullback",
    "breakout_candidate",
    "extended_momentum",
    "range_bound",
    "downtrend",
    "high_volatility_unstable",
    "low_liquidity",
    "insufficient_data",
    "unclear",
)

DECISION_SUPPORT_STATUSES = (
    "research_candidate",
    "watch",
    "wait_for_setup",
    "skip_for_now",
    "extended_risk",
    "insufficient_data",
)

STYLE_TYPES = (
    "momentum_breakout",
    "trend_pullback",
    "mean_reversion",
    "base_breakout",
    "relative_strength_leader",
    "not_suitable_now",
)

FIT_LEVELS = ("high", "medium", "low")
CONFIDENCE_LEVELS = ("low", "medium", "high")
DATA_SUFFICIENCY = ("sufficient", "partial", "insufficient")
MA_ALIGNMENT = ("bullish_stack", "bearish_stack", "mixed", "insufficient")
COVERAGE_STATUS = ("fresh", "stale", "historical_only", "absent")
TRANSCRIPT_AGREEMENT = ("confirms", "contradicts", "irrelevant", "unknown")

# Implied action labels — see ADR 0008 for derivation rules and rationale.
# Derived from DecisionSupportStatus + setup + entry-zone availability;
# never emitted independently. The underlying rubric remains authoritative.
ACTION_LABELS = (
    "BUY",          # research_candidate, high conf, entry zone available
    "ACCUMULATE",   # research_candidate, medium conf OR no clean entry zone
    "HOLD",         # constructive but no fresh trigger
    "WAIT",         # no setup yet
    "REDUCE",       # extended_risk — chase risk too high
    "AVOID",        # skip_for_now (downtrend / low liquidity / unstable vol)
    "N/A",          # insufficient_data
)


# ---------------------------------------------------------------------------
# Panels


class IdentityCoverage(BaseModel):
    """Section 1 of the spec — what is this ticker, and what do we know about it?"""

    ticker: str
    name: str | None = None
    asset_type: str | None = None  # e.g., 'EQUITY' | 'ETF' | 'INDEX'
    exchange: str | None = None
    sector: str | None = None
    in_universe: bool
    has_transcript_signals: bool
    has_extracted_calls: bool
    has_extracted_claims: bool
    n_bars_loaded: int
    enough_history_for_full_analysis: bool
    data_freshness_days: int | None = None
    missing_data_warnings: list[str] = Field(default_factory=list)


class MarketSnapshotPanel(BaseModel):
    """Section 2 — current market context derived from price/volume."""

    as_of: datetime
    last_close: float | None = None
    last_bar_at: datetime | None = None
    daily_volume: int | None = None
    avg_volume_20d: float | None = None
    avg_volume_63d: float | None = None
    dollar_volume: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    pct_off_52w_high: float | None = None  # negative or zero, e.g. -0.12 = 12% below
    pct_off_52w_low: float | None = None  # positive or zero
    return_1d: float | None = None
    return_5d: float | None = None
    return_21d: float | None = None
    return_63d: float | None = None
    return_126d: float | None = None
    return_252d: float | None = None
    gap_from_prev_close: float | None = None
    spy_return_5d: float | None = None
    spy_return_21d: float | None = None
    spy_return_63d: float | None = None
    excess_return_5d: float | None = None
    excess_return_21d: float | None = None
    excess_return_63d: float | None = None


class ValuationPanel(BaseModel):
    """Fundamental / valuation context (yfinance.info-derived).

    Decision-support context, never a trigger. Per Q3 of the multi-lens
    discussion, valuation does NOT feed into R/R math; it is shown as a
    separate ribbon and is one input to the Fundamental lens of the
    multi-lens analyst panel.

    All fields nullable so missing yfinance data degrades gracefully.
    """

    # Headline valuation
    market_cap: float | None = None  # USD
    forward_pe: float | None = None
    trailing_pe: float | None = None
    peg_ratio: float | None = None
    price_to_sales_ttm: float | None = None
    price_to_book: float | None = None
    enterprise_to_ebitda: float | None = None

    # Growth (yfinance returns these as fractions: 0.18 = 18% YoY)
    earnings_growth_forward: float | None = None
    revenue_growth_yoy: float | None = None
    profit_margins: float | None = None

    # Float / shorts (liquidity & crowding inputs)
    float_shares: float | None = None
    shares_outstanding: float | None = None
    short_pct_of_float: float | None = None
    held_pct_institutions: float | None = None

    # Risk normalisation
    beta: float | None = None
    dividend_yield: float | None = None

    # Catalyst proximity
    days_to_next_earnings: int | None = None
    next_earnings_date: datetime | None = None

    # Sector context (carry-over from IdentityCoverage so the panel is
    # self-contained when shown in isolation).
    sector: str | None = None
    industry: str | None = None

    # Age of the yfinance metadata this panel was built from. None when
    # metadata was unavailable. Consumers (and any LLM contract) must be
    # able to tell a 10-minute-old multiple from a 10-day-old one.
    fetched_at: datetime | None = None


class IndicatorPanel(BaseModel):
    """Section 3 (part 1) — moving averages, momentum, volatility, volume, RS."""

    sma_20: float | None = None
    sma_50: float | None = None
    sma_150: float | None = None
    sma_200: float | None = None
    ema_8: float | None = None
    ema_21: float | None = None
    dist_to_sma_20_pct: float | None = None
    dist_to_sma_50_pct: float | None = None
    dist_to_sma_150_pct: float | None = None
    dist_to_sma_200_pct: float | None = None
    dist_to_ema_8_pct: float | None = None
    dist_to_ema_21_pct: float | None = None
    sma_50_slope_21d_pct: float | None = None
    sma_200_slope_63d_pct: float | None = None
    ma_alignment: str = "insufficient"  # one of MA_ALIGNMENT
    rsi_14: float | None = None
    atr_14: float | None = None
    atr_14_pct: float | None = None
    realized_vol_21d_annualized: float | None = None
    volume_ratio_20: float | None = None
    relative_strength_vs_spy_63d: float | None = None
    relative_strength_vs_spy_126d: float | None = None


class LevelsPanel(BaseModel):
    """Section 3 (part 2) — swing-based support/resistance and consolidation."""

    swing_highs: list[float] = Field(default_factory=list)
    swing_lows: list[float] = Field(default_factory=list)
    nearest_resistance: float | None = None
    nearest_support: float | None = None
    nearest_resistance_distance_pct: float | None = None
    nearest_support_distance_pct: float | None = None
    recent_high_63d: float | None = None
    recent_low_63d: float | None = None
    pullback_pct_from_recent_high: float | None = None  # positive = how far back from high
    consolidation_range_pct: float | None = None  # last 20 bars range / midprice
    is_in_tight_range: bool = False
    breakout_distance_pct: float | None = None  # signed: + above recent high, - below
    base_low: float | None = None
    base_high: float | None = None


class SetupClassification(BaseModel):
    """Section 4 — rule-based setup classification."""

    setup_type: str  # one of SETUP_TYPES
    confidence: str  # one of CONFIDENCE_LEVELS
    reasons: list[str] = Field(default_factory=list)
    counterarguments: list[str] = Field(default_factory=list)
    supporting_metrics: dict[str, Any] = Field(default_factory=dict)
    data_sufficiency: str = "sufficient"  # one of DATA_SUFFICIENCY


class StyleFitItem(BaseModel):
    """A single style with fit level + explanation."""

    style: str  # one of STYLE_TYPES
    fit_level: str  # one of FIT_LEVELS
    reasons: list[str] = Field(default_factory=list)
    relevant_levels: dict[str, float | None] = Field(default_factory=dict)
    invalidation_conditions: list[str] = Field(default_factory=list)
    what_would_improve: list[str] = Field(default_factory=list)
    what_would_weaken: list[str] = Field(default_factory=list)


class StyleFitPanel(BaseModel):
    """Section 6 — style fit across the trading style catalog."""

    items: list[StyleFitItem] = Field(default_factory=list)
    primary_style: str | None = None  # the highest-fit style, or None if all 'low'


class DecisionRubricEntry(BaseModel):
    """One row of the transparent decision-support rubric."""

    name: str
    value: float | str | None = None
    threshold: float | str | None = None
    passed: bool | None = None
    weight: str = "medium"  # 'low' | 'medium' | 'high' — weight of this signal in the rubric
    note: str | None = None


class DecisionSupportStatus(BaseModel):
    """Section 5 — overall decision-support status with full rubric."""

    status: str  # one of DECISION_SUPPORT_STATUSES
    confidence: str  # one of CONFIDENCE_LEVELS
    summary: str
    rubric: list[DecisionRubricEntry] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class ActionSignal(BaseModel):
    """Implied buy/hold/sell label derived from `DecisionSupportStatus`.

    See ADR 0008. The label is a *view* over the underlying rubric — never a
    standalone signal. `derivation` is the human-readable mapping that
    produced it; `rubric_pass_rate` is `passed/total` from the upstream
    `DecisionSupportStatus.rubric` (None when no rubric was scored).
    """

    label: str  # one of ACTION_LABELS
    confidence: str  # one of CONFIDENCE_LEVELS
    derivation: str  # short explanation: which inputs drove this label
    rubric_pass_rate: float | None = None
    notes: list[str] = Field(default_factory=list)


class EntryZoneCandidate(BaseModel):
    """Section 7 — research-zone candidates with ATR-anchored invalidation."""

    available: bool
    reason_unavailable: str | None = None
    setup_trigger_level: float | None = None
    candidate_research_zone_low: float | None = None
    candidate_research_zone_high: float | None = None
    invalidation_reference: float | None = None
    risk_reference_pct: float | None = None  # |entry - invalidation| / entry
    risk_reference_atrs: float | None = None  # |entry - invalidation| / atr_14
    nearest_resistance: float | None = None
    risk_reward_estimate: float | None = None  # (resistance - entry) / (entry - invalidation)
    method_notes: list[str] = Field(default_factory=list)
    language_disclaimer: str = (
        "Research zones are decision-support context — not entry recommendations."
    )


class TranscriptContext(BaseModel):
    """Section 8 — what transcript signals exist and how they relate to the setup."""

    has_data: bool
    n_signals: int
    n_calls: int
    n_claims: int
    most_recent_mention_at: datetime | None = None
    days_since_most_recent: int | None = None
    coverage_status: str = "absent"  # one of COVERAGE_STATUS
    n_distinct_creators: int = 0
    n_calibrated_creators: int = 0
    net_polarity_30d: float | None = None
    credibility_weighted_polarity_30d: float | None = None
    sample_size_caveat: str | None = None
    summary: str = ""
    confirms_or_contradicts: str = "unknown"  # one of TRANSCRIPT_AGREEMENT
    notes: list[str] = Field(default_factory=list)


class TickerResearchView(BaseModel):
    """The whole research-view object returned by the API + CLI."""

    ticker: str
    as_of: datetime
    identity: IdentityCoverage
    market: MarketSnapshotPanel
    valuation: ValuationPanel = Field(default_factory=lambda: ValuationPanel())
    indicators: IndicatorPanel
    levels: LevelsPanel
    setup: SetupClassification
    style_fit: StyleFitPanel
    status: DecisionSupportStatus
    action: ActionSignal
    entry_zone: EntryZoneCandidate
    transcript: TranscriptContext
    methodology_links: list[str] = Field(
        default_factory=lambda: [
            "ADR-0005 · Product pivot to decision-support",
            "ADR-0006 · Claims and ticker signals",
            "ADR-0003 · Backtest assumptions",
        ]
    )
    disclaimer: str = (
        "Research output. Decision support, not investment advice. "
        "The user evaluates and pulls the trigger; this system does not "
        "execute orders."
    )
