"""Aggregate ExtractedCall + Claim rows into TickerSignal rows.

Per ADR 0006, pre-computed (ticker × window × signal_type) aggregates are the
canonical feature shape for the dashboard's ticker page and for downstream ML.
This module is the source of truth for how those aggregates are computed.

Contract:
- Idempotent. Each (ticker, window_end, window_size, signal_type) row upserts
  on its natural key.
- Time-aware. The relevant time for inclusion is `Document.posted_at`, NOT
  `Claim.extracted_at` or `ExtractedCall.extracted_at`. The simulator clock
  is the post time, same as for backtests (ADR 0003).
- Read-only inputs. We only read accepted rows
  (ExtractedCall.status='accepted', Claim.status='accepted'). Pending-review
  rows are deliberately excluded — see ADR 0006 §"Aggregation policy."
- No leakage. window_end aligns to the most recent NYSE trading-day close
  at-or-before `now`; the window is [window_end - window_size, window_end].

Performance: aggregation runs in Python over rows pulled from SQLite. At the
expected V1 volume (<1M rows total), this is fine. If it ever isn't, push
the aggregations into SQL.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable

import pandas as pd

from app.db import session_scope
from app.logging import get_logger
from app.market.calendar import _schedule, to_utc
from app.models import (
    CLAIM_CLASS_FACTUAL,
    CLAIM_CLASS_HYPE,
    CLAIM_CLASS_OPINION,
    CLAIM_CLASS_SPECULATION,
    CLAIM_POLARITY_BEARISH,
    CLAIM_POLARITY_BULLISH,
    CLAIM_POLARITY_MIXED,
    CLAIM_POLARITY_NEUTRAL,
    CLAIM_STATUS_ACCEPTED,
    Claim,
    Creator,
    CreatorScorecard,
    Document,
    ExtractedCall,
    SIGNAL_TYPE_CLAIMS_ALL,
    SIGNAL_TYPE_CLAIMS_FACTUAL,
    SIGNAL_TYPE_CREATOR_CONSENSUS,
    SIGNAL_TYPE_TRADE_CALLS,
    SIGNAL_WINDOW_1D,
    SIGNAL_WINDOW_7D,
    SIGNAL_WINDOW_30D,
    SourceChannel,
    TickerSignal,
)

log = get_logger(__name__)

AGGREGATOR_VERSION = "v0.1"

# Mapping from window_size string → days. Mirrors the SIGNAL_WINDOW_* constants.
_WINDOW_DAYS = {
    SIGNAL_WINDOW_1D: 1,
    SIGNAL_WINDOW_7D: 7,
    SIGNAL_WINDOW_30D: 30,
}

# All four signal types the aggregator can produce. Empty inputs → no row.
_SIGNAL_TYPES = (
    SIGNAL_TYPE_TRADE_CALLS,
    SIGNAL_TYPE_CLAIMS_ALL,
    SIGNAL_TYPE_CLAIMS_FACTUAL,
    SIGNAL_TYPE_CREATOR_CONSENSUS,
)

# Per ADR 0006 §"Aggregation policy." Class weights × creator credibility
# determines a claim's contribution to credibility_weighted_polarity.
_CLAIM_CLASS_WEIGHT = {
    CLAIM_CLASS_FACTUAL: 1.0,
    CLAIM_CLASS_OPINION: 0.5,
    CLAIM_CLASS_SPECULATION: 0.25,
    CLAIM_CLASS_HYPE: 0.0,
}

# Polarity → numeric. mixed/neutral both contribute 0.0 to net polarity but
# still count as a mention.
_CLAIM_POLARITY_VALUE = {
    CLAIM_POLARITY_BULLISH: 1.0,
    CLAIM_POLARITY_BEARISH: -1.0,
    CLAIM_POLARITY_NEUTRAL: 0.0,
    CLAIM_POLARITY_MIXED: 0.0,
}

# Trade-call direction → numeric polarity.
_CALL_DIRECTION_VALUE = {
    "long": 1.0,
    "short": -1.0,
    "unspecified": 0.0,
}

# Creator credibility threshold for the 'creator_consensus' signal. Creators
# whose most-recent CreatorScorecard.hit_rate_lower_ci exceeds this contribute
# to consensus aggregates. Calibrated to match ADR 0007's strategy threshold.
CONSENSUS_HIT_RATE_LOWER_CI_THRESHOLD = 0.55


# ---------------------------------------------------------------------------
# Window alignment


def most_recent_close_at_or_before(now: datetime) -> datetime:
    """Most recent NYSE regular-session close at-or-before `now` (UTC).

    If `now` is during a trading session, returns the *previous* session's
    close. (We never use the in-progress session's "close" — that's a
    forward-looking value.)
    """
    now_utc = to_utc(now)
    # 14 days back is enough to cover any holiday weekend span.
    sched = _schedule(now_utc - timedelta(days=14), now_utc)
    if sched.empty:
        raise ValueError(f"no trading sessions in lookback window for {now_utc!r}")
    closed = sched[sched["market_close"] <= now_utc]
    if closed.empty:
        raise ValueError(f"no closed sessions before {now_utc!r}")
    return closed["market_close"].iloc[-1].to_pydatetime()


# ---------------------------------------------------------------------------
# Inputs: typed rows pulled from the DB


@dataclass(frozen=True)
class _CallRow:
    call_id: int
    document_id: int
    creator_id: int
    ticker: str
    direction: str
    entry_price: float | None
    target_price: float | None
    stop_price: float | None
    posted_at: datetime


@dataclass(frozen=True)
class _ClaimRow:
    claim_id: int
    document_id: int
    creator_id: int
    ticker: str          # ticker-less rows are filtered out before this stage
    polarity: str
    claim_class: str
    posted_at: datetime


def _load_calls(session, *, since: datetime, until: datetime) -> list[_CallRow]:
    rows = session.execute(
        ExtractedCall.__table__.select()
        .with_only_columns(
            ExtractedCall.id,
            ExtractedCall.document_id,
            SourceChannel.creator_id,
            ExtractedCall.ticker,
            ExtractedCall.direction,
            ExtractedCall.entry_price,
            ExtractedCall.target_price,
            ExtractedCall.stop_price,
            Document.posted_at,
        )
        .select_from(ExtractedCall.__table__)
        .join(Document.__table__, Document.id == ExtractedCall.document_id)
        .join(SourceChannel.__table__, SourceChannel.id == Document.source_channel_id)
        .where(ExtractedCall.status == "accepted")
        .where(Document.posted_at >= since)
        .where(Document.posted_at <= until)
    ).all()
    return [_CallRow(*r) for r in rows]


def _load_claims(session, *, since: datetime, until: datetime) -> list[_ClaimRow]:
    rows = session.execute(
        Claim.__table__.select()
        .with_only_columns(
            Claim.id,
            Claim.document_id,
            SourceChannel.creator_id,
            Claim.ticker,
            Claim.polarity,
            Claim.claim_class,
            Document.posted_at,
        )
        .select_from(Claim.__table__)
        .join(Document.__table__, Document.id == Claim.document_id)
        .join(SourceChannel.__table__, SourceChannel.id == Document.source_channel_id)
        .where(Claim.status == CLAIM_STATUS_ACCEPTED)
        .where(Claim.ticker.is_not(None))   # sector/macro claims excluded
        .where(Document.posted_at >= since)
        .where(Document.posted_at <= until)
    ).all()
    return [_ClaimRow(*r) for r in rows]


def _load_creator_credibility(session) -> dict[int, float]:
    """Most recent CreatorScorecard.hit_rate_lower_ci per creator, where set.

    Used to weight claims/calls in 'creator_consensus' signals. Creators
    without a scorecard get neutral weight downstream (handled by caller).
    """
    rows = session.execute(
        CreatorScorecard.__table__.select()
        .with_only_columns(
            CreatorScorecard.creator_id,
            CreatorScorecard.hit_rate_lower_ci,
            CreatorScorecard.computed_at,
        )
        .where(CreatorScorecard.hit_rate_lower_ci.is_not(None))
        .order_by(CreatorScorecard.creator_id, CreatorScorecard.computed_at.desc())
    ).all()
    out: dict[int, float] = {}
    for cid, lower_ci, _ in rows:
        # First row per creator is most recent (we order DESC).
        if cid not in out:
            out[cid] = float(lower_ci)
    return out


# ---------------------------------------------------------------------------
# Per-window aggregation


def _aggregate_calls(
    rows: Iterable[_CallRow],
    *,
    credibility: dict[int, float],
    window_end: datetime,
    window_size: str,
    aggregator_version: str,
) -> dict[str, TickerSignal]:
    """Aggregate trade-call rows into one TickerSignal per ticker."""
    by_ticker: dict[str, list[_CallRow]] = defaultdict(list)
    for r in rows:
        by_ticker[r.ticker].append(r)

    out: dict[str, TickerSignal] = {}
    for ticker, ts in by_ticker.items():
        n = len(ts)
        polarities = [_CALL_DIRECTION_VALUE.get(r.direction, 0.0) for r in ts]
        net_pol = sum(polarities) / n if n else None

        # Credibility-weighted polarity. Calls without a creator scorecard get
        # neutral weight 1.0 (uncalibrated, per ADR).
        weights = [credibility.get(r.creator_id, 1.0) for r in ts]
        wsum = sum(weights)
        cred_pol = (
            sum(p * w for p, w in zip(polarities, weights)) / wsum if wsum else None
        )

        # Trade-call-specific: distance-from-stated-level percentages.
        # Skipped in V1 because they need the market price at posted_at, which
        # is on MarketSnapshot (not joined here). Reserved for slice D-2.
        out[ticker] = TickerSignal(
            ticker=ticker,
            window_end=window_end,
            window_size=window_size,
            signal_type=SIGNAL_TYPE_TRADE_CALLS,
            n_mentions=n,
            n_distinct_creators=len({r.creator_id for r in ts}),
            n_documents=len({r.document_id for r in ts}),
            net_polarity=net_pol,
            credibility_weighted_polarity=cred_pol,
            avg_entry_distance_pct=None,
            avg_target_distance_pct=None,
            avg_stop_distance_pct=None,
            n_factual=0,
            n_opinion=0,
            n_speculation=0,
            n_hype=0,
            aggregator_version=aggregator_version,
        )
    return out


def _aggregate_claims(
    rows: Iterable[_ClaimRow],
    *,
    credibility: dict[int, float],
    window_end: datetime,
    window_size: str,
    signal_type: str,
    aggregator_version: str,
) -> dict[str, TickerSignal]:
    """Aggregate claim rows for a given signal_type."""
    by_ticker: dict[str, list[_ClaimRow]] = defaultdict(list)
    for r in rows:
        by_ticker[r.ticker].append(r)

    out: dict[str, TickerSignal] = {}
    for ticker, ts in by_ticker.items():
        n = len(ts)
        polarities = [_CLAIM_POLARITY_VALUE.get(r.polarity, 0.0) for r in ts]
        net_pol = sum(polarities) / n if n else None

        # Credibility-weighted: class weight × creator credibility. Per ADR.
        cred_weights = [credibility.get(r.creator_id, 1.0) for r in ts]
        class_weights = [_CLAIM_CLASS_WEIGHT.get(r.claim_class, 0.0) for r in ts]
        weights = [cw * kw for cw, kw in zip(cred_weights, class_weights)]
        wsum = sum(weights)
        cred_pol = (
            sum(p * w for p, w in zip(polarities, weights)) / wsum if wsum else None
        )

        # Class mix.
        n_factual = sum(1 for r in ts if r.claim_class == CLAIM_CLASS_FACTUAL)
        n_opinion = sum(1 for r in ts if r.claim_class == CLAIM_CLASS_OPINION)
        n_spec = sum(1 for r in ts if r.claim_class == CLAIM_CLASS_SPECULATION)
        n_hype = sum(1 for r in ts if r.claim_class == CLAIM_CLASS_HYPE)

        out[ticker] = TickerSignal(
            ticker=ticker,
            window_end=window_end,
            window_size=window_size,
            signal_type=signal_type,
            n_mentions=n,
            n_distinct_creators=len({r.creator_id for r in ts}),
            n_documents=len({r.document_id for r in ts}),
            net_polarity=net_pol,
            credibility_weighted_polarity=cred_pol,
            avg_entry_distance_pct=None,
            avg_target_distance_pct=None,
            avg_stop_distance_pct=None,
            n_factual=n_factual,
            n_opinion=n_opinion,
            n_speculation=n_spec,
            n_hype=n_hype,
            aggregator_version=aggregator_version,
        )
    return out


# ---------------------------------------------------------------------------
# Persistence


def _upsert_signals(session, signals: list[TickerSignal]) -> int:
    """Replace existing rows on the natural key. Returns count written."""
    if not signals:
        return 0
    # SQLite doesn't support ON CONFLICT well across SQLAlchemy versions
    # without dialect-specific code. Delete-then-insert per natural key
    # keeps it portable and idempotent.
    for s in signals:
        session.execute(
            TickerSignal.__table__.delete().where(
                (TickerSignal.ticker == s.ticker)
                & (TickerSignal.window_end == s.window_end)
                & (TickerSignal.window_size == s.window_size)
                & (TickerSignal.signal_type == s.signal_type)
            )
        )
    session.add_all(signals)
    session.flush()
    return len(signals)


# ---------------------------------------------------------------------------
# Public API


def run_aggregation(
    *,
    now: datetime | None = None,
    window_sizes: list[str] | None = None,
    aggregator_version: str = AGGREGATOR_VERSION,
) -> dict[str, int]:
    """Compute and persist TickerSignal rows for all configured windows.

    Returns a summary dict: {window_size: n_rows_written, total: int}.
    """
    now = now or datetime.now(tz=UTC)
    window_sizes = window_sizes or list(_WINDOW_DAYS.keys())
    window_end = most_recent_close_at_or_before(now)

    summary: dict[str, int] = {"total": 0}

    with session_scope() as session:
        credibility = _load_creator_credibility(session)

        for ws in window_sizes:
            days = _WINDOW_DAYS[ws]
            window_start = window_end - timedelta(days=days)

            calls = _load_calls(session, since=window_start, until=window_end)
            claims = _load_claims(session, since=window_start, until=window_end)
            consensus_creator_ids = {
                cid for cid, lower_ci in credibility.items()
                if lower_ci > CONSENSUS_HIT_RATE_LOWER_CI_THRESHOLD
            }
            consensus_claims = [c for c in claims if c.creator_id in consensus_creator_ids]
            factual_claims = [c for c in claims if c.claim_class == CLAIM_CLASS_FACTUAL]

            signals: list[TickerSignal] = []
            signals.extend(_aggregate_calls(
                calls,
                credibility=credibility,
                window_end=window_end,
                window_size=ws,
                aggregator_version=aggregator_version,
            ).values())
            signals.extend(_aggregate_claims(
                claims,
                credibility=credibility,
                window_end=window_end,
                window_size=ws,
                signal_type=SIGNAL_TYPE_CLAIMS_ALL,
                aggregator_version=aggregator_version,
            ).values())
            signals.extend(_aggregate_claims(
                factual_claims,
                credibility=credibility,
                window_end=window_end,
                window_size=ws,
                signal_type=SIGNAL_TYPE_CLAIMS_FACTUAL,
                aggregator_version=aggregator_version,
            ).values())
            signals.extend(_aggregate_claims(
                consensus_claims,
                credibility=credibility,
                window_end=window_end,
                window_size=ws,
                signal_type=SIGNAL_TYPE_CREATOR_CONSENSUS,
                aggregator_version=aggregator_version,
            ).values())

            n = _upsert_signals(session, signals)
            summary[ws] = n
            summary["total"] += n

    log.info("aggregation.ticker_signals.done", **summary, window_end=window_end.isoformat())
    return summary
