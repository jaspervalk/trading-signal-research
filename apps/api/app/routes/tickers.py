from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ExtractedCall, OutcomeWindow
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
