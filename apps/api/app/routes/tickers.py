from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Creator,
    Document,
    ExtractedCall,
    OutcomeWindow,
    SourceChannel,
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
