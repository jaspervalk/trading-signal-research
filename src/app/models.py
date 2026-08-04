"""ORM entities. Phase 1a starts with the source/document/segment chain.

Later phases extend with ExtractedCall, OutcomeWindow, CreatorScorecard, etc.
Each new entity should land alongside the phase that uses it, not preemptively.
"""

from __future__ import annotations

from datetime import datetime, timezone
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


# --- Phase F: Strategy walk-forward results (ADR 0007) --------------------

# Persisted output of one `run_walkforward` invocation. Idempotent on
# (strategy_name, strategy_version, config_hash); a re-run with the same
# inputs replaces the row in place. Trades, equity curve, and sanity-check
# results are stored as JSON blobs to keep the schema simple — they're for
# review-replay, not for joins. The dashboard's "Backtest explorer" panel
# (planned in docs/architecture.md) reads from these rows.

class WalkForwardResultRow(Base):
    __tablename__ = "walkforward_results"
    __table_args__ = (
        UniqueConstraint(
            "strategy_name", "strategy_version", "config_hash",
            name="uq_walkforward_natural_key",
        ),
        Index("ix_walkforward_strategy", "strategy_name", "strategy_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(32))
    config_hash: Mapped[str] = mapped_column(String(32))

    # Wall-clock window
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rebalance_mode: Mapped[str] = mapped_column(String(16))
    cost_bps: Mapped[int] = mapped_column(Integer, default=10)
    benchmark_ticker: Mapped[str] = mapped_column(String(16), default="SPY")

    # Headline metrics (so the dashboard can sort + filter without parsing JSON)
    n_rebalances: Mapped[int] = mapped_column(Integer, default=0)
    n_trades: Mapped[int] = mapped_column(Integer, default=0)
    underpowered: Mapped[bool] = mapped_column(default=False)

    cagr: Mapped[float | None] = mapped_column(Float)
    sharpe: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    annualized_vol: Mapped[float | None] = mapped_column(Float)
    hit_rate: Mapped[float | None] = mapped_column(Float)
    hit_rate_lower_ci: Mapped[float | None] = mapped_column(Float)
    hit_rate_upper_ci: Mapped[float | None] = mapped_column(Float)
    benchmark_cagr: Mapped[float | None] = mapped_column(Float)
    excess_cagr: Mapped[float | None] = mapped_column(Float)
    avg_holding_days: Mapped[float | None] = mapped_column(Float)
    win_loss_ratio: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)

    # Detail blobs (JSON text)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    trades_json: Mapped[str] = mapped_column(Text, default="[]")
    equity_curve_json: Mapped[str] = mapped_column(Text, default="[]")
    sanity_checks_json: Mapped[str] = mapped_column(Text, default="[]")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    config_json: Mapped[str] = mapped_column(Text, default="{}")

    # Calibration (filled later by F-9 calibration computation if applicable)
    brier_score: Mapped[float | None] = mapped_column(Float)
    reliability_json: Mapped[str | None] = mapped_column(Text)

    # Provenance
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    def __repr__(self) -> str:
        return (
            f"<WalkForwardResultRow {self.strategy_name}@{self.strategy_version} "
            f"cagr={self.cagr} sharpe={self.sharpe} "
            f"n_trades={self.n_trades} underpowered={self.underpowered}>"
        )


# --- Phase G: Research snapshots --------------------------------------------
#
# A `ResearchSnapshot` is a tiny row written every time the `/research` API
# (or `tsr research` CLI) runs for a ticker. Captures the *headline* of the
# `TickerResearchView` — status, setup, key levels, transcript signal —
# plus a timestamp. Lets us answer "how did AAPL's setup evolve over the
# last 30 days?" without re-running history.
#
# Storage cost is small: ~30 numeric/string columns, no JSON blobs. Roughly
# 1KB per row. At one snapshot per ticker per dashboard view, this stays
# under a few MB indefinitely.
#
# NOT a replacement for the strategy-level walk-forward harness. Snapshots
# are personal-research metadata; walk-forward is statistical evaluation.

class ResearchSnapshot(Base):
    __tablename__ = "research_snapshots"
    __table_args__ = (
        Index("ix_research_snapshot_ticker_time", "ticker", "as_of"),
        Index("ix_research_snapshot_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    ticker: Mapped[str] = mapped_column(String(16))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Headline classification (mirrors src/app/analysis/schema.py enums)
    status: Mapped[str] = mapped_column(String(32))
    status_confidence: Mapped[str] = mapped_column(String(8))
    setup_type: Mapped[str] = mapped_column(String(32))
    setup_confidence: Mapped[str] = mapped_column(String(8))
    primary_style: Mapped[str | None] = mapped_column(String(40))

    # Headline market context
    last_close: Mapped[float | None] = mapped_column(Float)
    pct_off_52w_high: Mapped[float | None] = mapped_column(Float)
    return_5d: Mapped[float | None] = mapped_column(Float)
    return_21d: Mapped[float | None] = mapped_column(Float)
    return_63d: Mapped[float | None] = mapped_column(Float)

    # Headline indicators
    ma_alignment: Mapped[str] = mapped_column(String(20))
    rsi_14: Mapped[float | None] = mapped_column(Float)
    atr_14_pct: Mapped[float | None] = mapped_column(Float)
    sma_50_slope_21d_pct: Mapped[float | None] = mapped_column(Float)
    relative_strength_vs_spy_63d: Mapped[float | None] = mapped_column(Float)

    # Levels we care about for entry-zone tracking
    nearest_support: Mapped[float | None] = mapped_column(Float)
    nearest_resistance: Mapped[float | None] = mapped_column(Float)
    pullback_pct_from_recent_high: Mapped[float | None] = mapped_column(Float)
    breakout_distance_pct: Mapped[float | None] = mapped_column(Float)

    # Entry zone (when available)
    entry_zone_available: Mapped[bool] = mapped_column(default=False)
    entry_trigger: Mapped[float | None] = mapped_column(Float)
    entry_zone_low: Mapped[float | None] = mapped_column(Float)
    entry_zone_high: Mapped[float | None] = mapped_column(Float)
    invalidation_reference: Mapped[float | None] = mapped_column(Float)
    risk_reward_estimate: Mapped[float | None] = mapped_column(Float)

    # Transcript-side context
    transcript_n_calls: Mapped[int] = mapped_column(Integer, default=0)
    transcript_n_claims: Mapped[int] = mapped_column(Integer, default=0)
    transcript_n_creators: Mapped[int] = mapped_column(Integer, default=0)
    transcript_polarity_30d: Mapped[float | None] = mapped_column(Float)
    transcript_coverage_status: Mapped[str | None] = mapped_column(String(20))
    transcript_confirms: Mapped[str | None] = mapped_column(String(16))

    # Provenance — snapshots are append-only; we keep all of them for a
    # ticker so the dashboard can render an evolution timeline.
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    def __repr__(self) -> str:
        return (
            f"<ResearchSnapshot {self.ticker} {self.status}/{self.setup_type} "
            f"at={self.as_of:%Y-%m-%d}>"
        )


class ResearchPlan(Base):
    """Cached entry/exit research plan (see docs/entry-exit-research-plan.md).

    One row per (ticker, mode, day). The full plan is stored as JSON Text so
    the schema can evolve without migration churn — querying never goes
    inside the JSON, only by the indexed fields.
    """

    __tablename__ = "research_plans"
    __table_args__ = (
        Index("ix_research_plan_ticker_mode_day", "ticker", "mode", "day_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    mode: Mapped[str] = mapped_column(String(8))  # 'quick' | 'deep'
    day_key: Mapped[str] = mapped_column(String(10))  # 'YYYY-MM-DD' UTC

    # Headline fields, denormalised for cheap UI listing without parsing JSON.
    confidence: Mapped[str] = mapped_column(String(8))
    timeframe: Mapped[str] = mapped_column(String(8))
    cost_usd: Mapped[float] = mapped_column(Float)
    duration_ms: Mapped[int] = mapped_column(Integer)

    # The full EntryExitPlan as JSON. SQLite doesn't have a native JSON type;
    # Text + json.loads in the read path is plenty for this volume.
    plan_json: Mapped[str] = mapped_column(Text)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    def __repr__(self) -> str:
        return (
            f"<ResearchPlan {self.ticker} {self.mode} day={self.day_key} "
            f"conf={self.confidence} ${self.cost_usd:.4f}>"
        )


# --- Phase 1 (lens accuracy infra): per-lens snapshots + outcomes ----------


class LensSnapshot(Base):
    """One row per (Deep|Quick run × lens). Captures the lens's direction
    call at decision time so realised outcomes can be attributed back to
    individual lenses (Phase 1 lens accuracy scorecards).
    """

    __tablename__ = "lens_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "ticker", "as_of", "mode", "lens_name",
            name="uq_lens_snapshot_run",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_plans.id", ondelete="SET NULL"),
        index=True,
    )
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    mode: Mapped[str] = mapped_column(String(8))  # 'quick' | 'deep'
    lens_name: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # 'bullish' | 'bearish' | 'neutral'
    conviction: Mapped[str] = mapped_column(String(8))  # 'low' | 'medium' | 'high'
    summary: Mapped[str] = mapped_column(Text)
    points_json: Mapped[str] = mapped_column(Text)  # JSON array of strings
    sources_used: Mapped[str] = mapped_column(Text)  # JSON array
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    def __repr__(self) -> str:
        return (
            f"<LensSnapshot {self.ticker} {self.mode}/{self.lens_name} "
            f"{self.direction}/{self.conviction} as_of={self.as_of:%Y-%m-%d}>"
        )


class LensOutcome(Base):
    """Realised return + direction-correct flag for a (LensSnapshot × horizon).

    Computed retroactively once price history is available for the horizon.
    """

    __tablename__ = "lens_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "horizon",
            name="uq_lens_outcome_horizon",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("lens_snapshots.id", ondelete="CASCADE"),
        index=True,
    )
    horizon: Mapped[str] = mapped_column(String(8))  # '1d' | '3d' | '5d' | '21d'
    return_pct: Mapped[float] = mapped_column(Float)
    excess_vs_spy_pct: Mapped[float] = mapped_column(Float)
    direction_correct: Mapped[bool] = mapped_column()
    regime: Mapped[str] = mapped_column(String(8))  # 'up' | 'flat' | 'down'
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )

    def __repr__(self) -> str:
        return (
            f"<LensOutcome snap={self.snapshot_id} {self.horizon} "
            f"ret={self.return_pct:+.2%} correct={self.direction_correct}>"
        )


# --- Portfolio Manager: manual trade ledger (ADR 0009) --------------------

# The ONLY stored portfolio entity. Positions are derived by folding these
# rows chronologically (see app/portfolio/ledger.py) — never stored, so they
# cannot drift from the ledger when a trade is edited.
#
# price_per_share is in `currency` (the stock's native currency).
# eur_amount is the all-in EUR total that actually moved in the account,
# fees and FX spread included; it is optional and, when present on every
# trade for a ticker, drives a parallel EUR cost-basis view.

class PortfolioTrade(Base):
    __tablename__ = "portfolio_trades"
    __table_args__ = (
        Index("ix_portfolio_trades_ticker", "ticker"),
        Index("ix_portfolio_trades_traded_at", "traded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))  # 'buy' | 'sell'
    quantity: Mapped[float] = mapped_column(Float)  # fractional shares allowed
    price_per_share: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    traded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    eur_amount: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return (
            f"<PortfolioTrade {self.side} {self.quantity} {self.ticker} "
            f"@ {self.price_per_share} {self.currency}>"
        )
