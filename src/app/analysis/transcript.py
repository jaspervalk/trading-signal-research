"""Transcript-side context for the research view.

Reads `TickerSignal`, `Claim`, `ExtractedCall`, and `CreatorScorecard`
rows for a ticker and produces a `TranscriptContext` panel — a *summary*
of what the transcript-derived layer says, plus a "confirms or
contradicts" read against the technical setup.

This module is the single place that joins technical-side analysis with
transcript-side signals. The orchestrator calls into it once.

Important: this module reads the DB but does NOT write. It also does
not duplicate the existing /tickers/{t}/signals / /claims / /coverage
endpoints — those remain the source of truth for the raw rows. We only
surface a summary here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Normalize a naive datetime to UTC. Older ingests in this DB stored
    naive timestamps; newer ones store tz-aware. Mixing the two crashes
    `max()` and any comparison; normalize on read.
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt

from app.analysis.schema import (
    SetupClassification,
    TranscriptContext,
)
from app.models import (
    CLAIM_POLARITY_BEARISH,
    CLAIM_POLARITY_BULLISH,
    CLAIM_POLARITY_NEUTRAL,
    CLAIM_STATUS_ACCEPTED,
    Claim,
    CreatorScorecard,
    Document,
    ExtractedCall,
    SIGNAL_TYPE_CLAIMS_ALL,
    SIGNAL_WINDOW_30D,
    SourceChannel,
    TickerSignal,
)


_BULLISH_SETUPS = {
    "strong_uptrend",
    "uptrend_pullback",
    "breakout_candidate",
    "extended_momentum",
}
_BEARISH_SETUPS = {"downtrend"}


def build_transcript_context(
    session: Session,
    ticker: str,
    setup: SetupClassification,
    *,
    as_of: datetime,
) -> TranscriptContext:
    """Pull and summarize transcript-side data for `ticker` as of `as_of`."""
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    ticker = ticker.upper()

    # --- 1. Most recent TickerSignal rows by (window_size, signal_type) ---
    signal_rows = session.execute(
        select(TickerSignal)
        .where(TickerSignal.ticker == ticker)
        .where(TickerSignal.window_end <= as_of)
        .order_by(TickerSignal.window_end.desc(), TickerSignal.signal_type)
    ).scalars().all()
    latest_per_slot: dict[tuple[str, str], TickerSignal] = {}
    for r in signal_rows:
        key = (r.window_size, r.signal_type)
        if key not in latest_per_slot:
            latest_per_slot[key] = r

    # --- 2. Claims and calls aggregates for "what we have on this ticker" ---
    claim_rows = session.execute(
        select(Claim, Document, SourceChannel)
        .join(Document, Document.id == Claim.document_id)
        .join(SourceChannel, SourceChannel.id == Document.source_channel_id)
        .where(Claim.ticker == ticker)
        .where(Claim.status == CLAIM_STATUS_ACCEPTED)
        .where(Document.posted_at <= as_of)
    ).all()
    call_rows = session.execute(
        select(ExtractedCall, Document, SourceChannel)
        .join(Document, Document.id == ExtractedCall.document_id)
        .join(SourceChannel, SourceChannel.id == Document.source_channel_id)
        .where(ExtractedCall.ticker == ticker)
        .where(ExtractedCall.status == "accepted")
        .where(Document.posted_at <= as_of)
    ).all()

    n_claims = len(claim_rows)
    n_calls = len(call_rows)
    n_signals = len(signal_rows)

    if n_claims == 0 and n_calls == 0 and n_signals == 0:
        return TranscriptContext(
            has_data=False,
            n_signals=0,
            n_calls=0,
            n_claims=0,
            coverage_status="absent",
            summary="No transcript-derived signals or claims on this ticker.",
            notes=[
                "Random tickers without YouTube creator coverage will have no transcript context here. "
                "The technical analysis sections are still meaningful.",
            ],
        )

    # --- 3. Recency / coverage status ---
    posted_dates = [_ensure_utc(r[1].posted_at) for r in (*claim_rows, *call_rows)]
    posted_dates = [d for d in posted_dates if d is not None]
    most_recent = max(posted_dates) if posted_dates else None

    days_since = (
        int((as_of - most_recent).total_seconds() // (24 * 3600))
        if most_recent is not None
        else None
    )
    if days_since is None:
        coverage_status = "absent"
    elif days_since <= 30:
        coverage_status = "fresh"
    elif days_since <= 90:
        coverage_status = "stale"
    else:
        coverage_status = "historical_only"

    # --- 4. Distinct + calibrated creator counts ---
    creator_ids: set[int] = set()
    for _, _, ch in (*claim_rows, *call_rows):
        creator_ids.add(int(ch.creator_id))

    calibrated_ids: set[int] = set()
    if creator_ids:
        scorecard_rows = session.execute(
            select(CreatorScorecard)
            .where(CreatorScorecard.creator_id.in_(creator_ids))
            .where(CreatorScorecard.hit_rate_lower_ci.is_not(None))
        ).scalars().all()
        for sc in scorecard_rows:
            calibrated_ids.add(int(sc.creator_id))

    # --- 5. Polarity from the most-recent claims_all 30d signal ---
    claims_all_30d = latest_per_slot.get((SIGNAL_WINDOW_30D, SIGNAL_TYPE_CLAIMS_ALL))
    net_polarity_30d = (
        float(claims_all_30d.net_polarity) if claims_all_30d is not None else None
    )
    weighted_polarity_30d = (
        float(claims_all_30d.credibility_weighted_polarity)
        if claims_all_30d is not None
        else None
    )

    # If no aggregate row exists yet, fall back to a quick computation from
    # the claims we already pulled. Useful right after extraction, before
    # `tsr aggregate-signals` has run.
    if net_polarity_30d is None and claim_rows:
        cutoff = as_of - timedelta(days=30)
        recent_claims = [
            (c, d) for c, d, _ in claim_rows
            if (_ensure_utc(d.posted_at) or as_of) >= cutoff
        ]
        if recent_claims:
            polarity_value = {
                CLAIM_POLARITY_BULLISH: 1.0,
                CLAIM_POLARITY_BEARISH: -1.0,
                CLAIM_POLARITY_NEUTRAL: 0.0,
            }
            vals = [polarity_value.get(c.polarity, 0.0) for c, _ in recent_claims]
            net_polarity_30d = sum(vals) / len(vals) if vals else None

    # --- 6. Sample-size caveat ---
    total_mentions = n_calls + n_claims
    sample_caveat = (
        f"Total mentions = {total_mentions}; below 5 → treat polarity as suggestive, not actionable."
        if total_mentions < 5
        else None
    )

    # --- 7. Confirms or contradicts the setup ---
    confirms = "unknown"
    if net_polarity_30d is not None and total_mentions >= 3:
        if setup.setup_type in _BULLISH_SETUPS:
            confirms = "confirms" if net_polarity_30d > 0.1 else (
                "contradicts" if net_polarity_30d < -0.1 else "irrelevant"
            )
        elif setup.setup_type in _BEARISH_SETUPS:
            confirms = "confirms" if net_polarity_30d < -0.1 else (
                "contradicts" if net_polarity_30d > 0.1 else "irrelevant"
            )
        else:
            # Range-bound, unclear, etc. — we don't claim confirmation on these.
            confirms = "irrelevant"

    summary = _build_summary(
        n_calls=n_calls,
        n_claims=n_claims,
        days_since=days_since,
        n_creators=len(creator_ids),
        n_calibrated=len(calibrated_ids),
        polarity=net_polarity_30d,
        coverage_status=coverage_status,
    )

    notes: list[str] = []
    if total_mentions < 5:
        notes.append(
            "Low transcript-mention sample — read polarity with caution."
        )
    if creator_ids and len(calibrated_ids) == 0:
        notes.append(
            "All mentioning creators are uncalibrated (insufficient resolved-call N for credibility scoring)."
        )

    return TranscriptContext(
        has_data=True,
        n_signals=n_signals,
        n_calls=n_calls,
        n_claims=n_claims,
        most_recent_mention_at=most_recent,
        days_since_most_recent=days_since,
        coverage_status=coverage_status,
        n_distinct_creators=len(creator_ids),
        n_calibrated_creators=len(calibrated_ids),
        net_polarity_30d=net_polarity_30d,
        credibility_weighted_polarity_30d=weighted_polarity_30d,
        sample_size_caveat=sample_caveat,
        summary=summary,
        confirms_or_contradicts=confirms,
        notes=notes,
    )


def _build_summary(
    *,
    n_calls: int,
    n_claims: int,
    days_since: int | None,
    n_creators: int,
    n_calibrated: int,
    polarity: float | None,
    coverage_status: str,
) -> str:
    bits: list[str] = []
    bits.append(f"{n_calls} call{'s' if n_calls != 1 else ''}")
    bits.append(f"{n_claims} claim{'s' if n_claims != 1 else ''}")
    bits.append(f"{n_creators} creator{'s' if n_creators != 1 else ''}")
    if n_calibrated == 0 and n_creators > 0:
        bits.append("none calibrated")
    elif n_creators > 0:
        bits.append(f"{n_calibrated} calibrated")
    if days_since is not None:
        bits.append(f"most recent {days_since}d ago")
    if polarity is not None:
        sign = "+" if polarity >= 0 else ""
        bits.append(f"30d polarity {sign}{polarity:.2f}")
    bits.append(f"coverage:{coverage_status}")
    return " · ".join(bits)


__all__ = ["build_transcript_context"]
