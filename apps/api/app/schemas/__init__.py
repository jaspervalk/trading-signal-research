"""Pydantic response/request schemas for the API.

Kept separate from app/extract/schemas.py so API contract changes don't
ripple back into the extraction pipeline.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- Creator + scorecard --------------------------------------------------


class CreatorOut(_ORMBase):
    id: int
    display_name: str
    primary_source_type: str
    notes: str | None = None
    active: bool


class ScorecardOut(_ORMBase):
    id: int
    creator_id: int
    window_label: str
    horizon: str
    n_calls: int
    n_activated: int
    n_unique_tickers: int
    activation_rate: float | None
    hit_rate: float | None
    hit_rate_lower_ci: float | None
    hit_rate_upper_ci: float | None
    mean_return: float | None
    median_return: float | None
    std_return: float | None
    expectancy_unconditional: float | None
    sharpe_like: float | None
    mean_mae: float | None
    worst_mae: float | None
    mean_excess_return: float | None
    median_excess_return: float | None
    excess_hit_rate: float | None


class LeaderboardRow(BaseModel):
    creator_id: int
    creator_name: str
    n_calls: int
    n_activated: int
    n_unique_tickers: int
    hit_rate: float | None
    hit_rate_lower_ci: float | None
    hit_rate_upper_ci: float | None
    mean_return: float | None
    mean_excess_return: float | None
    expectancy_unconditional: float | None
    sharpe_like: float | None


# ---- Document + segment ---------------------------------------------------


class DocumentOut(_ORMBase):
    id: int
    source_type: str
    external_id: str
    title: str | None = None
    description: str | None = None
    posted_at: datetime
    url: str | None = None
    duration_seconds: int | None = None
    creator_id: int | None = None
    creator_name: str | None = None


class TranscriptSegmentOut(_ORMBase):
    id: int
    document_id: int
    start_seconds: float
    end_seconds: float
    text: str
    speaker_label: str | None = None


# ---- Calls + outcomes ----------------------------------------------------


class OutcomeOut(_ORMBase):
    id: int
    call_id: int
    horizon: str
    activated: bool
    activation_at: datetime | None
    entry_fill_price: float | None
    exit_at: datetime | None
    exit_price: float | None
    return_pct: float | None
    benchmark_return_pct: float | None
    excess_return_pct: float | None
    mfe: float | None
    mae: float | None
    hit_target: bool | None
    hit_stop: bool | None
    status: str


class CallOut(_ORMBase):
    id: int
    document_id: int
    primary_segment_id: int | None
    ticker: str
    direction: str
    entry_type: str
    entry_price: float | None
    target_price: float | None
    stop_price: float | None
    timeframe: str
    reasoning_summary: str | None
    evidence_quote: str | None
    context_text: str | None
    context_start_seconds: float | None
    context_end_seconds: float | None
    extracted_at: datetime
    extractor_version: str
    llm_confidence: float | None
    rule_confidence: float | None
    final_confidence: float
    status: str
    validator_notes: str | None
    manual_status: str
    manual_notes: str | None
    manual_reviewed_at: datetime | None


class CallWithContext(BaseModel):
    """Call + denormalized creator/document metadata for list views."""

    call: CallOut
    creator_id: int | None
    creator_name: str | None
    document_title: str | None
    document_url: str | None
    posted_at: datetime
    outcomes: list[OutcomeOut] = Field(default_factory=list)


class CallsPage(BaseModel):
    items: list[CallWithContext]
    total: int
    limit: int
    offset: int


# ---- Annotations ---------------------------------------------------------


class AnnotationIn(BaseModel):
    entity_type: str
    entity_id: str
    body: str
    author: str | None = None


class AnnotationUpdate(BaseModel):
    body: str


class AnnotationOut(_ORMBase):
    id: int
    entity_type: str
    entity_id: str
    body: str
    author: str | None
    created_at: datetime
    updated_at: datetime


# ---- Tags ----------------------------------------------------------------


class TagIn(BaseModel):
    entity_type: str
    entity_id: str
    label: str


class TagOut(_ORMBase):
    id: int
    entity_type: str
    entity_id: str
    label: str
    created_at: datetime


# ---- Watchlist -----------------------------------------------------------


class WatchlistIn(BaseModel):
    entity_type: str
    entity_id: str
    note: str | None = None


class WatchlistOut(_ORMBase):
    id: int
    entity_type: str
    entity_id: str
    note: str | None
    pinned_at: datetime


# ---- Review --------------------------------------------------------------


class ReviewIn(BaseModel):
    manual_status: str  # 'unreviewed' | 'confirmed' | 'rejected' | 'flagged'
    manual_notes: str | None = None


# ---- Gold labels ---------------------------------------------------------


class GoldLabelIn(BaseModel):
    source_key: str
    source_text: str
    expected_is_call: bool
    origin_call_id: int | None = None
    origin_document_id: int | None = None
    expected_ticker: str | None = None
    expected_direction: str | None = None
    expected_entry_type: str | None = None
    expected_entry_price: float | None = None
    expected_target_price: float | None = None
    expected_stop_price: float | None = None
    expected_timeframe: str | None = None
    notes: str | None = None


class GoldLabelOut(_ORMBase):
    id: int
    source_key: str
    source_text: str
    origin_call_id: int | None
    origin_document_id: int | None
    expected_is_call: bool
    expected_ticker: str | None
    expected_direction: str | None
    expected_entry_type: str | None
    expected_entry_price: float | None
    expected_target_price: float | None
    expected_stop_price: float | None
    expected_timeframe: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
