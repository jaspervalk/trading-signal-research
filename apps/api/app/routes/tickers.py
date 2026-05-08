from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.research import build_ticker_research_view
from app.analysis.scan import ScanResult, scan_tickers
from app.analysis.schema import TickerResearchView
from app.analysis.snapshot import persist_snapshot
from app.models import (
    CLAIM_STATUS_ACCEPTED,
    Claim,
    Creator,
    CreatorScorecard,
    Document,
    ExtractedCall,
    OutcomeWindow,
    SourceChannel,
    TickerSignal,
)
from apps.api.app.deps import db_session

router = APIRouter(prefix="/tickers", tags=["tickers"])


@router.get("")
def list_tickers(session: Session = Depends(db_session)) -> list[dict]:
    """Distinct tickers seen in extracted_calls, with N + last_seen."""
    rows = session.execute(
        select(
            ExtractedCall.ticker,
            func.count(ExtractedCall.id).label("n_calls"),
            func.max(ExtractedCall.extracted_at).label("last_seen"),
        )
        .group_by(ExtractedCall.ticker)
        .order_by(func.count(ExtractedCall.id).desc())
    ).all()
    return [
        {"ticker": r.ticker, "n_calls": r.n_calls, "last_seen": r.last_seen}
        for r in rows
    ]


@router.get("/{ticker}")
def get_ticker(ticker: str, session: Session = Depends(db_session)) -> dict:
    """Summary for one ticker: N calls, breakdown by direction, recent outcomes."""
    ticker = ticker.upper()
    n_calls = session.scalar(
        select(func.count(ExtractedCall.id)).where(ExtractedCall.ticker == ticker)
    ) or 0
    by_direction = dict(
        session.execute(
            select(ExtractedCall.direction, func.count(ExtractedCall.id))
            .where(ExtractedCall.ticker == ticker)
            .group_by(ExtractedCall.direction)
        ).all()
    )
    by_outcome = dict(
        session.execute(
            select(OutcomeWindow.status, func.count(OutcomeWindow.id))
            .join(ExtractedCall, ExtractedCall.id == OutcomeWindow.call_id)
            .where(ExtractedCall.ticker == ticker)
            .group_by(OutcomeWindow.status)
        ).all()
    )
    return {
        "ticker": ticker,
        "n_calls": n_calls,
        "by_direction": by_direction,
        "by_outcome": by_outcome,
    }


@router.get("/{ticker}/bars")
def get_ticker_bars(
    ticker: str,
    days: int = Query(180, ge=10, le=2000),
    session: Session = Depends(db_session),  # noqa: ARG001 — kept for future filtering
) -> dict:
    """Daily OHLCV for the ticker over the last `days` calendar days.

    Reads through the cached yfinance client; expensive on cold cache,
    instant after that. Returns a shape suitable for `lightweight-charts`.
    """
    from app.market.yfinance_client import get_daily_bars

    end = datetime.now(tz=UTC)
    start = end - timedelta(days=days)
    try:
        df = get_daily_bars(ticker.upper(), start=start, end=end)
    except Exception as e:  # pragma: no cover
        raise HTTPException(502, f"yfinance error: {e}") from e

    if df.empty:
        return {"ticker": ticker.upper(), "bars": []}

    bars = [
        {
            "time": int(idx.timestamp()),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row["Volume"]) if "Volume" in row else 0,
        }
        for idx, row in df.iterrows()
    ]
    return {"ticker": ticker.upper(), "bars": bars}


# ---------------------------------------------------------------------------
# Ticker-first dashboard endpoints (per docs/dashboard-ticker-page-ia.md)


# Polarity → numeric for the per-creator polarity rollup. Keep in sync with
# src/app/aggregation/ticker_signals.py:_CLAIM_POLARITY_VALUE.
_POLARITY_VALUE = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "mixed": 0.0}
_DIRECTION_VALUE = {"long": 1.0, "short": -1.0, "unspecified": 0.0}


@router.get("/{ticker}/signals")
def get_ticker_signals(
    ticker: str,
    signal_type: str | None = Query(None, description="Filter to one signal_type."),
    window_size: str | None = Query(None, description="Filter to one window_size (e.g., '7d')."),
    session: Session = Depends(db_session),
) -> list[dict]:
    """Pre-aggregated TickerSignal rows for the live-signal strip.

    See ADR 0006 §"TickerSignal (new aggregation entity)" for the schema and
    docs/dashboard-ticker-page-ia.md §2 for how the dashboard consumes this.
    """
    ticker = ticker.upper()
    q = select(TickerSignal).where(TickerSignal.ticker == ticker)
    if signal_type is not None:
        q = q.where(TickerSignal.signal_type == signal_type)
    if window_size is not None:
        q = q.where(TickerSignal.window_size == window_size)
    q = q.order_by(TickerSignal.window_end.desc(), TickerSignal.signal_type)
    rows = session.execute(q).scalars().all()
    return [
        {
            "ticker": r.ticker,
            "window_end": r.window_end,
            "window_size": r.window_size,
            "signal_type": r.signal_type,
            "n_mentions": r.n_mentions,
            "n_distinct_creators": r.n_distinct_creators,
            "n_documents": r.n_documents,
            "net_polarity": r.net_polarity,
            "credibility_weighted_polarity": r.credibility_weighted_polarity,
            "avg_entry_distance_pct": r.avg_entry_distance_pct,
            "avg_target_distance_pct": r.avg_target_distance_pct,
            "avg_stop_distance_pct": r.avg_stop_distance_pct,
            "n_factual": r.n_factual,
            "n_opinion": r.n_opinion,
            "n_speculation": r.n_speculation,
            "n_hype": r.n_hype,
            "computed_at": r.computed_at,
            "aggregator_version": r.aggregator_version,
        }
        for r in rows
    ]


@router.get("/{ticker}/claims")
def get_ticker_claims(
    ticker: str,
    since: datetime | None = Query(None, description="ISO datetime; only claims posted on/after this."),
    claim_type: list[str] | None = Query(None, description="Multi-select claim_type filter."),
    claim_class: list[str] | None = Query(None, description="Multi-select claim_class filter."),
    polarity: str | None = Query(None, description="bullish | bearish | neutral | mixed."),
    creator_id: int | None = Query(None),
    status: str = Query(CLAIM_STATUS_ACCEPTED, description="Default 'accepted' — only validated claims."),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(db_session),
) -> list[dict]:
    """Recent-claims feed for the ticker page (IA section 4).

    Returns claim rows joined with creator + document context so the frontend
    can render the table and link back to the source video timestamp.
    """
    ticker = ticker.upper()
    q = (
        select(
            Claim.id.label("claim_id"),
            Claim.claim_type,
            Claim.ticker,
            Claim.sector,
            Claim.polarity,
            Claim.claim_class,
            Claim.summary,
            Claim.evidence_quote,
            Claim.context_start_seconds,
            Claim.context_end_seconds,
            Claim.final_confidence,
            Claim.status,
            Claim.extracted_at,
            Document.id.label("document_id"),
            Document.title.label("document_title"),
            Document.url.label("document_url"),
            Document.posted_at,
            Creator.id.label("creator_id"),
            Creator.display_name.label("creator_name"),
        )
        .select_from(Claim)
        .join(Document, Document.id == Claim.document_id)
        .join(SourceChannel, SourceChannel.id == Document.source_channel_id)
        .join(Creator, Creator.id == SourceChannel.creator_id)
        .where(Claim.ticker == ticker)
        .where(Claim.status == status)
    )
    if since is not None:
        q = q.where(Document.posted_at >= since)
    if claim_type:
        q = q.where(Claim.claim_type.in_(claim_type))
    if claim_class:
        q = q.where(Claim.claim_class.in_(claim_class))
    if polarity is not None:
        q = q.where(Claim.polarity == polarity)
    if creator_id is not None:
        q = q.where(Creator.id == creator_id)
    q = q.order_by(Document.posted_at.desc()).offset(offset).limit(limit)
    rows = session.execute(q).all()
    return [dict(r._mapping) for r in rows]


@router.get("/{ticker}/coverage")
def get_ticker_coverage(
    ticker: str, session: Session = Depends(db_session)
) -> list[dict]:
    """Per-creator coverage rollup for the ticker page (IA section 6).

    For each creator with at least one accepted call OR claim on this ticker:
    counts, most-recent mention, calibrated credibility (when a CreatorScorecard
    exists), and the creator's net polarity on THIS ticker.
    """
    ticker = ticker.upper()

    # Pull all calls for this ticker grouped by creator.
    call_rows = session.execute(
        select(
            Creator.id.label("creator_id"),
            Creator.display_name.label("creator_name"),
            ExtractedCall.direction,
            Document.posted_at,
        )
        .select_from(ExtractedCall)
        .join(Document, Document.id == ExtractedCall.document_id)
        .join(SourceChannel, SourceChannel.id == Document.source_channel_id)
        .join(Creator, Creator.id == SourceChannel.creator_id)
        .where(ExtractedCall.ticker == ticker)
        .where(ExtractedCall.status == "accepted")
    ).all()

    # Pull all claims for this ticker grouped by creator.
    claim_rows = session.execute(
        select(
            Creator.id.label("creator_id"),
            Creator.display_name.label("creator_name"),
            Claim.polarity,
            Document.posted_at,
        )
        .select_from(Claim)
        .join(Document, Document.id == Claim.document_id)
        .join(SourceChannel, SourceChannel.id == Document.source_channel_id)
        .join(Creator, Creator.id == SourceChannel.creator_id)
        .where(Claim.ticker == ticker)
        .where(Claim.status == CLAIM_STATUS_ACCEPTED)
    ).all()

    # Pull most recent CreatorScorecard.hit_rate_lower_ci per creator. Used as
    # the dashboard's "calibrated credibility" badge. Mirrors the aggregator's
    # _load_creator_credibility logic (most recent scorecard wins).
    sc_rows = session.execute(
        select(
            CreatorScorecard.creator_id,
            CreatorScorecard.hit_rate_lower_ci,
            CreatorScorecard.computed_at,
        )
        .where(CreatorScorecard.hit_rate_lower_ci.is_not(None))
        .order_by(CreatorScorecard.creator_id, CreatorScorecard.computed_at.desc())
    ).all()
    cred: dict[int, float] = {}
    for r in sc_rows:
        if r.creator_id not in cred:
            cred[r.creator_id] = float(r.hit_rate_lower_ci)

    # Roll up per creator.
    rollup: dict[int, dict] = {}
    for r in call_rows:
        d = rollup.setdefault(r.creator_id, {
            "creator_id": r.creator_id,
            "creator_name": r.creator_name,
            "n_calls": 0,
            "n_claims": 0,
            "most_recent_mention_at": r.posted_at,
            "polarity_sum": 0.0,
            "n_polarity_contributions": 0,
        })
        d["n_calls"] += 1
        d["polarity_sum"] += _DIRECTION_VALUE.get(r.direction, 0.0)
        d["n_polarity_contributions"] += 1
        if r.posted_at > d["most_recent_mention_at"]:
            d["most_recent_mention_at"] = r.posted_at

    for r in claim_rows:
        d = rollup.setdefault(r.creator_id, {
            "creator_id": r.creator_id,
            "creator_name": r.creator_name,
            "n_calls": 0,
            "n_claims": 0,
            "most_recent_mention_at": r.posted_at,
            "polarity_sum": 0.0,
            "n_polarity_contributions": 0,
        })
        d["n_claims"] += 1
        d["polarity_sum"] += _POLARITY_VALUE.get(r.polarity, 0.0)
        d["n_polarity_contributions"] += 1
        if r.posted_at > d["most_recent_mention_at"]:
            d["most_recent_mention_at"] = r.posted_at

    out: list[dict] = []
    for creator_id, d in rollup.items():
        n = d["n_polarity_contributions"]
        out.append({
            "creator_id": creator_id,
            "creator_name": d["creator_name"],
            "n_calls": d["n_calls"],
            "n_claims": d["n_claims"],
            "most_recent_mention_at": d["most_recent_mention_at"],
            "hit_rate_lower_ci": cred.get(creator_id),
            "is_calibrated": creator_id in cred,
            "net_polarity_on_this_ticker": (d["polarity_sum"] / n) if n else None,
        })
    # Sort: calibrated creators first, then by mention count desc.
    out.sort(
        key=lambda d: (
            0 if d["is_calibrated"] else 1,
            -(d["hit_rate_lower_ci"] or 0.0),
            -(d["n_calls"] + d["n_claims"]),
        )
    )
    return out


@router.get("/{ticker}/research", response_model=TickerResearchView)
def get_ticker_research(
    ticker: str,
    history_days: int = Query(540, ge=120, le=2000, description="Bars of history to load."),
    benchmark: str = Query("SPY", description="Benchmark symbol for RS + excess returns."),
    fetch_metadata: bool = Query(
        True,
        description=(
            "Whether to call yfinance .info for company name / sector. "
            "Disable on slow networks or in CI."
        ),
    ),
    session: Session = Depends(db_session),
) -> TickerResearchView:
    """Structured research view for a ticker — works for any symbol yfinance can resolve.

    Per ADR 0005, output is decision support, not advice. The view bundles
    identity, market snapshot, technical indicators, swing-based levels, a
    rule-based setup classification + style fit + decision-support status +
    entry-zone candidate, and (when available) transcript-derived context.

    Random tickers without transcript coverage still produce a meaningful
    technicals-only view; the `transcript` panel reports `coverage_status='absent'`.
    """
    try:
        view = build_ticker_research_view(
            ticker,
            session=session,
            history_days=history_days,
            benchmark_ticker=benchmark,
            fetch_metadata=fetch_metadata,
        )
        # Append-only snapshot for time-series of how a ticker's setup evolves.
        # Best-effort — research view returns even if snapshot persist fails.
        persist_snapshot(view, session=session)
        return view
    except Exception as e:  # pragma: no cover — defensive surface
        raise HTTPException(500, f"research view failed: {e}") from e


@router.get("/{ticker}/calls")
def get_ticker_calls(
    ticker: str, session: Session = Depends(db_session)
) -> list[dict]:
    """All calls on a ticker with creator + outcome attached, for chart overlay."""
    ticker = ticker.upper()
    rows = session.execute(
        select(
            ExtractedCall.id,
            ExtractedCall.direction,
            ExtractedCall.entry_type,
            ExtractedCall.entry_price,
            ExtractedCall.target_price,
            ExtractedCall.stop_price,
            ExtractedCall.final_confidence,
            ExtractedCall.status,
            Document.posted_at,
            Document.title.label("doc_title"),
            Creator.id.label("creator_id"),
            Creator.display_name.label("creator_name"),
        )
        .select_from(ExtractedCall)
        .join(Document, Document.id == ExtractedCall.document_id)
        .join(SourceChannel, SourceChannel.id == Document.source_channel_id)
        .join(Creator, Creator.id == SourceChannel.creator_id)
        .where(ExtractedCall.ticker == ticker)
        .order_by(Document.posted_at)
    ).all()

    out: list[dict] = []
    for r in rows:
        # Get the 5d outcome if available
        outcome = session.execute(
            select(OutcomeWindow.return_pct, OutcomeWindow.activated)
            .where(OutcomeWindow.call_id == r.id)
            .where(OutcomeWindow.horizon == "5d")
        ).one_or_none()
        out.append(
            {
                "call_id": r.id,
                "posted_at": r.posted_at,
                "time": int(r.posted_at.timestamp()),
                "direction": r.direction,
                "entry_type": r.entry_type,
                "entry_price": r.entry_price,
                "target_price": r.target_price,
                "stop_price": r.stop_price,
                "final_confidence": r.final_confidence,
                "status": r.status,
                "creator_id": r.creator_id,
                "creator_name": r.creator_name,
                "doc_title": r.doc_title,
                "return_5d": outcome.return_pct if outcome else None,
                "activated": outcome.activated if outcome else None,
            }
        )
    return out
