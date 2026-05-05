"""ORM entities. Phase 1a starts with the source/document/segment chain.

Later phases extend with ExtractedCall, MarketSnapshot, OutcomeWindow, CreatorScorecard, etc.
Each new entity should land alongside the phase that uses it, not preemptively.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

if TYPE_CHECKING:
    pass


class Base(DeclarativeBase):
    pass


class Creator(Base):
    """A trader/commentator identity, source-agnostic."""

    __tablename__ = "creators"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), unique=True)
    primary_source_type: Mapped[str] = mapped_column(String(32), default="youtube")
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    channels: Mapped[list[SourceChannel]] = relationship(
        back_populates="creator", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Creator {self.id} {self.display_name!r}>"


class SourceChannel(Base):
    """A channel/server within a specific source platform.

    The (source_type, external_id) pair is the natural key. New source types
    (e.g. 'discord') plug in here without schema changes downstream.
    """

    __tablename__ = "source_channels"
    __table_args__ = (
        UniqueConstraint("source_type", "external_id", name="uq_source_channel_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(32))  # 'youtube' | 'discord' (future)
    external_id: Mapped[str] = mapped_column(String(128))  # YouTube channel ID, etc.
    handle: Mapped[str | None] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    creator: Mapped[Creator] = relationship(back_populates="channels")
    documents: Mapped[list[Document]] = relationship(
        back_populates="source_channel", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<SourceChannel {self.source_type}:{self.external_id}>"


class Document(Base):
    """A unit of content. For YouTube: a single video.

    Source-agnostic: external_id + source_type uniquely identify the artifact.
    """

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("source_type", "external_id", name="uq_document_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_channel_id: Mapped[int] = mapped_column(
        ForeignKey("source_channels.id"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    url: Mapped[str | None] = mapped_column(String(512))
    raw_text_path: Mapped[str | None] = mapped_column(String(512))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    source_channel: Mapped[SourceChannel] = relationship(back_populates="documents")
    segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Document {self.source_type}:{self.external_id} {self.title!r}>"


class TranscriptSegment(Base):
    """A timestamped chunk of transcript text within a Document."""

    __tablename__ = "transcript_segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    start_seconds: Mapped[float] = mapped_column()
    end_seconds: Mapped[float] = mapped_column()
    text: Mapped[str] = mapped_column(Text)
    speaker_label: Mapped[str | None] = mapped_column(String(64))

    document: Mapped[Document] = relationship(back_populates="segments")

    def __repr__(self) -> str:
        return f"<Segment doc={self.document_id} {self.start_seconds:.0f}-{self.end_seconds:.0f}s>"


# --- Phase 2: extraction --------------------------------------------------


# Status values for ExtractedCall.status
CALL_STATUS_PENDING = "pending"           # extracted, awaiting validation
CALL_STATUS_ACCEPTED = "accepted"         # passed validator + confidence
CALL_STATUS_PENDING_REVIEW = "pending_review"   # below confidence threshold, manual check
CALL_STATUS_REJECTED = "rejected"         # validator hard-fail


# Direction / entry_type / timeframe enums (kept as strings to keep the schema
# easy to evolve and to match the LLM output format).
DIRECTION_LONG = "long"
DIRECTION_SHORT = "short"
DIRECTION_UNSPECIFIED = "unspecified"

ENTRY_MARKET = "market"
ENTRY_LIMIT = "limit"
ENTRY_TRIGGER_ABOVE = "trigger_above"
ENTRY_TRIGGER_BELOW = "trigger_below"
ENTRY_UNSPECIFIED = "unspecified"

TIMEFRAME_DAY = "day"
TIMEFRAME_SWING = "swing"
TIMEFRAME_POSITION = "position"
TIMEFRAME_UNSPECIFIED = "unspecified"


class ExtractedCall(Base):
    """A structured trade call extracted from a Document via the hybrid pipeline.

    Provenance: links back to the source `Document` and (when known) the
    primary `TranscriptSegment` whose text triggered the extraction. The
    surrounding context window (start/end seconds) is stored so that auditors
    can replay the LLM input without re-walking the segment table.
    """

    __tablename__ = "extracted_calls"
    __table_args__ = (
        Index("ix_calls_doc_ticker", "document_id", "ticker"),
        Index("ix_calls_status_conf", "status", "final_confidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    primary_segment_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcript_segments.id")
    )

    # --- the call itself
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    direction: Mapped[str] = mapped_column(String(16), default=DIRECTION_UNSPECIFIED)
    entry_type: Mapped[str] = mapped_column(String(32), default=ENTRY_UNSPECIFIED)
    entry_price: Mapped[float | None] = mapped_column(Float)
    target_price: Mapped[float | None] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float)
    timeframe: Mapped[str] = mapped_column(String(16), default=TIMEFRAME_UNSPECIFIED)

    # --- evidence + reasoning (so audits don't need the LLM re-run)
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    context_text: Mapped[str | None] = mapped_column(Text)
    context_start_seconds: Mapped[float | None] = mapped_column(Float)
    context_end_seconds: Mapped[float | None] = mapped_column(Float)

    # --- provenance + confidence
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )
    extractor_version: Mapped[str] = mapped_column(String(32))
    llm_confidence: Mapped[float | None] = mapped_column(Float)
    rule_confidence: Mapped[float | None] = mapped_column(Float)
    final_confidence: Mapped[float] = mapped_column(Float, default=0.0, index=True)

    status: Mapped[str] = mapped_column(String(32), default=CALL_STATUS_PENDING, index=True)
    validator_notes: Mapped[str | None] = mapped_column(Text)

    # Phase 6: human review state. Set via the dashboard.
    # 'unreviewed' (default) | 'confirmed' | 'rejected' | 'flagged'
    manual_status: Mapped[str] = mapped_column(
        String(32), default="unreviewed", index=True
    )
    manual_notes: Mapped[str | None] = mapped_column(Text)
    manual_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship()

    def __repr__(self) -> str:
        return (
            f"<ExtractedCall {self.id} {self.ticker} {self.direction} "
            f"conf={self.final_confidence:.2f} status={self.status}>"
        )


# --- Phase 3: market data + backtest --------------------------------------


# OutcomeWindow.status values
OUTCOME_STATUS_EVALUATED = "evaluated"
OUTCOME_STATUS_NOT_TRIGGERED = "not_triggered"
OUTCOME_STATUS_DATA_MISSING = "data_missing"
OUTCOME_STATUS_FAILED = "failed"


class MarketSnapshot(Base):
    """Price + technical context for a ticker at a specific instant.

    Computed once per (call.ticker, call.posted_at) so feature extraction for
    the ranking model can read these directly without re-fetching prices.
    """

    __tablename__ = "market_snapshots"
    __table_args__ = (
        UniqueConstraint("ticker", "snapshot_at", name="uq_snapshot_ticker_at"),
        Index("ix_snapshot_ticker_at", "ticker", "snapshot_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16))
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Most-recent regular-session close at-or-before snapshot_at.
    price: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(Integer)

    # Technicals (all computed using only data with bar_time <= snapshot_at)
    atr_14: Mapped[float | None] = mapped_column(Float)
    return_5d: Mapped[float | None] = mapped_column(Float)
    return_21d: Mapped[float | None] = mapped_column(Float)
    return_63d: Mapped[float | None] = mapped_column(Float)
    dist_to_ma20: Mapped[float | None] = mapped_column(Float)  # (price - MA20) / MA20
    dist_to_ma50: Mapped[float | None] = mapped_column(Float)
    dist_to_ma200: Mapped[float | None] = mapped_column(Float)
    rsi_14: Mapped[float | None] = mapped_column(Float)
    volume_ratio_20: Mapped[float | None] = mapped_column(Float)  # vol / 20d avg

    # Market regime context (SPY-based) — same fields a model can read later.
    spy_return_21d: Mapped[float | None] = mapped_column(Float)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    def __repr__(self) -> str:
        return f"<MarketSnapshot {self.ticker} @ {self.snapshot_at} price={self.price}>"


class OutcomeWindow(Base):
    """Backtest result for a single (call, horizon) pair.

    Stores both the assumptions used and the resulting metrics so any
    downstream change in assumptions can be detected (and recomputed) without
    losing history.
    """

    __tablename__ = "outcome_windows"
    __table_args__ = (
        UniqueConstraint(
            "call_id", "horizon", "evaluator_version",
            name="uq_outcome_call_horizon_evaluator",
        ),
        Index("ix_outcome_call_horizon", "call_id", "horizon"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("extracted_calls.id"), index=True)
    horizon: Mapped[str] = mapped_column(String(8))  # '1d' | '3d' | '5d' | '21d'

    # Window bounds (UTC). The activation may have happened later than
    # window_start_at if the trigger fired late.
    window_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Activation
    activated: Mapped[bool] = mapped_column(default=False)
    activation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_fill_price: Mapped[float | None] = mapped_column(Float)

    # Outcome — only meaningful when activated=True.
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[float | None] = mapped_column(Float)
    gross_return_pct: Mapped[float | None] = mapped_column(Float)  # pre-cost
    return_pct: Mapped[float | None] = mapped_column(Float)        # post-cost
    mfe: Mapped[float | None] = mapped_column(Float)               # max favourable excursion
    mae: Mapped[float | None] = mapped_column(Float)               # max adverse excursion
    hit_target: Mapped[bool | None] = mapped_column()
    hit_stop: Mapped[bool | None] = mapped_column()

    # Benchmark (SPY over the same wall-clock window).
    benchmark_return_pct: Mapped[float | None] = mapped_column(Float)
    excess_return_pct: Mapped[float | None] = mapped_column(Float)

    # Provenance
    assumptions_cost_bps: Mapped[int] = mapped_column(Integer, default=10)
    status: Mapped[str] = mapped_column(String(32), default=OUTCOME_STATUS_NOT_TRIGGERED)
    notes: Mapped[str | None] = mapped_column(Text)
    evaluator_version: Mapped[str] = mapped_column(String(32))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    call: Mapped[ExtractedCall] = relationship()

    def __repr__(self) -> str:
        ret = f"{self.return_pct:+.2%}" if self.return_pct is not None else "—"
        return (
            f"<OutcomeWindow call={self.call_id} {self.horizon} "
            f"act={self.activated} ret={ret} status={self.status}>"
        )


# --- Phase 4: creator scorecards ------------------------------------------

# CreatorScorecard window labels
SCORECARD_WINDOW_ALL = "all"
SCORECARD_WINDOW_365D = "365d"
SCORECARD_WINDOW_90D = "90d"


class CreatorScorecard(Base):
    """Multi-metric creator profile, computed per (creator, window, horizon).

    Stored, not derived, so we can track how scorecards change over time
    (recency drift) and so the dashboard reads from one place.
    """

    __tablename__ = "creator_scorecards"
    __table_args__ = (
        UniqueConstraint(
            "creator_id", "window_label", "horizon", "evaluator_version",
            name="uq_scorecard_creator_window_horizon_evaluator",
        ),
        Index("ix_scorecard_creator_window", "creator_id", "window_label"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"), index=True)
    window_label: Mapped[str] = mapped_column(String(16))   # 'all' | '365d' | '90d'
    horizon: Mapped[str] = mapped_column(String(8))         # '1d' | '3d' | '5d' | '21d'

    # Counts (always show alongside any rate metric)
    n_calls: Mapped[int] = mapped_column(Integer, default=0)
    n_activated: Mapped[int] = mapped_column(Integer, default=0)
    n_unique_tickers: Mapped[int] = mapped_column(Integer, default=0)

    # Activation
    activation_rate: Mapped[float | None] = mapped_column(Float)

    # Hit rate + 95% Wilson interval (conditional on activation)
    hit_rate: Mapped[float | None] = mapped_column(Float)
    hit_rate_lower_ci: Mapped[float | None] = mapped_column(Float)
    hit_rate_upper_ci: Mapped[float | None] = mapped_column(Float)

    # Returns
    mean_return: Mapped[float | None] = mapped_column(Float)        # conditional
    median_return: Mapped[float | None] = mapped_column(Float)
    std_return: Mapped[float | None] = mapped_column(Float)
    expectancy_unconditional: Mapped[float | None] = mapped_column(Float)
    sharpe_like: Mapped[float | None] = mapped_column(Float)        # mean / std

    # Drawdown proxy
    mean_mae: Mapped[float | None] = mapped_column(Float)
    worst_mae: Mapped[float | None] = mapped_column(Float)

    # Benchmark-relative
    mean_excess_return: Mapped[float | None] = mapped_column(Float)
    median_excess_return: Mapped[float | None] = mapped_column(Float)
    excess_hit_rate: Mapped[float | None] = mapped_column(Float)

    evaluator_version: Mapped[str] = mapped_column(String(32))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    creator: Mapped[Creator] = relationship()

    def __repr__(self) -> str:
        return (
            f"<CreatorScorecard creator={self.creator_id} {self.window_label}/{self.horizon} "
            f"N={self.n_activated}/{self.n_calls} hit={self.hit_rate} mean_excess={self.mean_excess_return}>"
        )


# --- Phase 2 extension (per ADR 0006): claims ------------------------------


# Claim type taxonomy (per ADR 0006). Orthogonal to claim_class.
CLAIM_TYPE_CATALYST = "catalyst"               # specific positive thesis
CLAIM_TYPE_RISK = "risk"                       # concrete downside thesis
CLAIM_TYPE_EARNINGS_VIEW = "earnings_view"     # directional view on a print
CLAIM_TYPE_MACRO_THEME = "macro_theme"         # broad regime claim; ticker may be null
CLAIM_TYPE_SECTOR_VIEW = "sector_view"         # sector/industry-level directional view
CLAIM_TYPE_FACTUAL_ASSERTION = "factual_assertion"  # checkable factual statement
CLAIM_TYPE_OPINION = "opinion"                 # non-falsifiable preference
CLAIM_TYPE_SPECULATION = "speculation"         # directional guess without evidence
CLAIM_TYPE_HYPE = "hype"                       # promotional/emotional, no thesis

# Polarity
CLAIM_POLARITY_BULLISH = "bullish"
CLAIM_POLARITY_BEARISH = "bearish"
CLAIM_POLARITY_NEUTRAL = "neutral"
CLAIM_POLARITY_MIXED = "mixed"

# Claim class — orthogonal to claim_type, drives credibility weighting.
CLAIM_CLASS_FACTUAL = "factual"
CLAIM_CLASS_OPINION = "opinion"
CLAIM_CLASS_SPECULATION = "speculation"
CLAIM_CLASS_HYPE = "hype"

# Status mirrors ExtractedCall.
CLAIM_STATUS_PENDING = "pending"
CLAIM_STATUS_ACCEPTED = "accepted"
CLAIM_STATUS_PENDING_REVIEW = "pending_review"
CLAIM_STATUS_REJECTED = "rejected"

# Resolution outcomes (populated only when the claim is verifiable).
CLAIM_RESOLUTION_CORRECT = "correct"
CLAIM_RESOLUTION_WRONG = "wrong"
CLAIM_RESOLUTION_PARTIAL = "partial"
CLAIM_RESOLUTION_UNVERIFIABLE = "unverifiable"


class Claim(Base):
    """A non-trade-call claim extracted from a Document via the hybrid pipeline.

    Sibling of ExtractedCall: a single source segment can produce a call AND/OR
    several claims. Claims may be ticker-less (sector/macro). Every claim
    carries a verbatim evidence_quote and an explicit claim_class.

    See ADR 0006 for the schema rationale and the resolution model.
    """

    __tablename__ = "claims"
    __table_args__ = (
        Index("ix_claims_doc_ticker", "document_id", "ticker"),
        Index("ix_claims_status_conf", "status", "final_confidence"),
        Index("ix_claims_type_polarity", "claim_type", "polarity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    primary_segment_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcript_segments.id")
    )

    # The claim itself
    claim_type: Mapped[str] = mapped_column(String(32), index=True)
    ticker: Mapped[str | None] = mapped_column(String(16))
    sector: Mapped[str | None] = mapped_column(String(64))
    polarity: Mapped[str] = mapped_column(String(16))
    claim_class: Mapped[str] = mapped_column(String(16))

    # Text + evidence
    summary: Mapped[str] = mapped_column(Text)
    evidence_quote: Mapped[str] = mapped_column(Text)
    context_text: Mapped[str | None] = mapped_column(Text)
    context_start_seconds: Mapped[float | None] = mapped_column(Float)
    context_end_seconds: Mapped[float | None] = mapped_column(Float)

    # Resolution (populated by a separate pipeline; deferred per ADR 0006)
    resolution_target_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_outcome: Mapped[str | None] = mapped_column(String(32))
    resolution_notes: Mapped[str | None] = mapped_column(Text)

    # Provenance + confidence
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )
    extractor_version: Mapped[str] = mapped_column(String(32))
    llm_confidence: Mapped[float | None] = mapped_column(Float)
    rule_confidence: Mapped[float | None] = mapped_column(Float)
    final_confidence: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    status: Mapped[str] = mapped_column(
        String(32), default=CLAIM_STATUS_PENDING, index=True
    )
    validator_notes: Mapped[str | None] = mapped_column(Text)

    # Manual review (mirrors ExtractedCall)
    manual_status: Mapped[str] = mapped_column(
        String(32), default="unreviewed", index=True
    )
    manual_notes: Mapped[str | None] = mapped_column(Text)
    manual_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    document: Mapped[Document] = relationship()

    def __repr__(self) -> str:
        return (
            f"<Claim {self.id} {self.claim_type} ticker={self.ticker} "
            f"polarity={self.polarity} class={self.claim_class} "
            f"conf={self.final_confidence:.2f} status={self.status}>"
        )


# --- Slice D (per ADR 0006): TickerSignal aggregation ---------------------


# Signal type taxonomy (per ADR 0006). A row's signal_type determines which
# input rows feed the aggregate.
SIGNAL_TYPE_TRADE_CALLS = "trade_calls"            # ExtractedCall only
SIGNAL_TYPE_CLAIMS_ALL = "claims_all"              # all accepted Claim rows
SIGNAL_TYPE_CLAIMS_FACTUAL = "claims_factual"      # claim_class='factual' only
SIGNAL_TYPE_CREATOR_CONSENSUS = "creator_consensus"  # high-credibility creators only

# Window sizes. These align with backtest horizons but live separately so
# signal windows can evolve without touching backtest assumptions.
SIGNAL_WINDOW_1D = "1d"
SIGNAL_WINDOW_7D = "7d"
SIGNAL_WINDOW_30D = "30d"


class TickerSignal(Base):
    """Pre-aggregated signal features for one (ticker, window_end, window_size, signal_type).

    Read primitive for the dashboard's ticker page and feature shape for ML
    later. Idempotent: a single row per natural key; recomputation upserts.

    See ADR 0006 §"TickerSignal (new aggregation entity)" for the schema and
    §"Aggregation policy" for the recompute contract.
    """

    __tablename__ = "ticker_signals"
    __table_args__ = (
        UniqueConstraint(
            "ticker", "window_end", "window_size", "signal_type",
            name="uq_ticker_signal_natural_key",
        ),
        Index("ix_ticker_signal_lookup", "ticker", "window_end"),
        Index("ix_ticker_signal_type_window", "signal_type", "window_size"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_size: Mapped[str] = mapped_column(String(8))   # '1d' | '7d' | '30d'
    signal_type: Mapped[str] = mapped_column(String(32))

    # Counts
    n_mentions: Mapped[int] = mapped_column(Integer, default=0)
    n_distinct_creators: Mapped[int] = mapped_column(Integer, default=0)
    n_documents: Mapped[int] = mapped_column(Integer, default=0)

    # Polarity (computed only for signal types where it's meaningful)
    net_polarity: Mapped[float | None] = mapped_column(Float)
    credibility_weighted_polarity: Mapped[float | None] = mapped_column(Float)

    # Trade-call specifics (signal_type='trade_calls' only)
    avg_entry_distance_pct: Mapped[float | None] = mapped_column(Float)
    avg_target_distance_pct: Mapped[float | None] = mapped_column(Float)
    avg_stop_distance_pct: Mapped[float | None] = mapped_column(Float)

    # Claim-class mix (signal_type='claims_*' only)
    n_factual: Mapped[int] = mapped_column(Integer, default=0)
    n_opinion: Mapped[int] = mapped_column(Integer, default=0)
    n_speculation: Mapped[int] = mapped_column(Integer, default=0)
    n_hype: Mapped[int] = mapped_column(Integer, default=0)

    # Provenance
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )
    aggregator_version: Mapped[str] = mapped_column(String(32))

    def __repr__(self) -> str:
        return (
            f"<TickerSignal {self.ticker} {self.signal_type} "
            f"{self.window_size}@{self.window_end:%Y-%m-%d} "
            f"n={self.n_mentions} pol={self.net_polarity}>"
        )


# --- Phase 6: research workbench entities ---------------------------------

# Polymorphic entity_type for Annotation / Tag / Watchlist
ENTITY_CALL = "call"
ENTITY_CREATOR = "creator"
ENTITY_TICKER = "ticker"
ENTITY_DOCUMENT = "document"
ENTITY_SEGMENT = "segment"

# Manual review status values
MANUAL_STATUS_UNREVIEWED = "unreviewed"
MANUAL_STATUS_CONFIRMED = "confirmed"
MANUAL_STATUS_REJECTED = "rejected"
MANUAL_STATUS_FLAGGED = "flagged"


class Annotation(Base):
    """Free-text note attached to any entity in the system.

    Polymorphic by `(entity_type, entity_id)` so a single annotation table
    serves notes on calls, creators, tickers, etc.
    """

    __tablename__ = "annotations"
    __table_args__ = (
        Index("ix_annotation_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))  # int for most, str for ticker
    body: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    def __repr__(self) -> str:
        preview = (self.body or "")[:40]
        return f"<Annotation #{self.id} {self.entity_type}:{self.entity_id} {preview!r}>"


class Tag(Base):
    """Categorical label attached to an entity.

    Examples: 'watch', 'false_positive', 'gold_example', 'investigate', 'golden_call'.
    Multiple tags per entity allowed; (entity_type, entity_id, label) is unique.
    """

    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "label", name="uq_tag_entity_label"),
        Index("ix_tag_entity", "entity_type", "entity_id"),
        Index("ix_tag_label", "label"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    def __repr__(self) -> str:
        return f"<Tag {self.entity_type}:{self.entity_id} {self.label!r}>"


class Watchlist(Base):
    """User-pinned entities for quick access in the dashboard.

    Single-user for V1; if multi-user comes later we add a `user_id` column.
    """

    __tablename__ = "watchlist"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_watchlist_entity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(256))
    pinned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    def __repr__(self) -> str:
        return f"<Watchlist {self.entity_type}:{self.entity_id}>"


class GoldLabel(Base):
    """A hand-labeled extraction example used to evaluate the extractor.

    Migrated from `data/gold/extraction_gold.jsonl` so the dashboard can
    browse/edit. The JSONL stays as an import/export format.
    """

    __tablename__ = "gold_labels"
    __table_args__ = (
        UniqueConstraint("source_key", name="uq_gold_source_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable key: either the JSONL `id` or `call:<call_id>` when promoted from a real call.
    source_key: Mapped[str] = mapped_column(String(64))

    # The text shown to the LLM; may differ from any single TranscriptSegment.
    source_text: Mapped[str] = mapped_column(Text)

    # Optional pointer back to the originating call/document (when promoted).
    origin_call_id: Mapped[int | None] = mapped_column(ForeignKey("extracted_calls.id"))
    origin_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))

    # Expected extraction. None / empty means "no call expected from this segment".
    expected_is_call: Mapped[bool] = mapped_column(default=False)
    expected_ticker: Mapped[str | None] = mapped_column(String(16))
    expected_direction: Mapped[str | None] = mapped_column(String(16))
    expected_entry_type: Mapped[str | None] = mapped_column(String(32))
    expected_entry_price: Mapped[float | None] = mapped_column(Float)
    expected_target_price: Mapped[float | None] = mapped_column(Float)
    expected_stop_price: Mapped[float | None] = mapped_column(Float)
    expected_timeframe: Mapped[str | None] = mapped_column(String(16))

    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    def __repr__(self) -> str:
        return f"<GoldLabel {self.source_key} call={self.expected_is_call} ticker={self.expected_ticker}>"
