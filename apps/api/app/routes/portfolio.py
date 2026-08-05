"""Portfolio Manager endpoints: the manual trade ledger and derived view.

Positions are derived, never stored, so there are no position endpoints —
holdings are only ever read through GET /portfolio.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.portfolio import service
from app.portfolio.ledger import LedgerError
from app.portfolio.schema import PortfolioView, TradeIn, TradePatch, TradeRecord
from apps.api.app.deps import db_session

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("", response_model=PortfolioView)
def get_portfolio(
    with_quotes: bool = Query(True, description="Fetch live quotes (~15m delayed)."),
    session: Session = Depends(db_session),
) -> PortfolioView:
    try:
        return service.build_portfolio_view(session, with_quotes=with_quotes)
    except LedgerError as exc:
        raise HTTPException(
            422, f"portfolio ledger is inconsistent: {exc}. Fix it from the trade ledger."
        ) from exc


@router.get("/trades", response_model=list[TradeRecord])
def list_trades(
    ticker: str | None = Query(None),
    session: Session = Depends(db_session),
) -> list[TradeRecord]:
    return service.list_trades(session, ticker)


@router.post("/trades", response_model=TradeRecord, status_code=201)
def create_trade(payload: TradeIn, session: Session = Depends(db_session)) -> TradeRecord:
    try:
        row = service.create_trade(session, payload)
    except LedgerError as exc:
        raise HTTPException(422, str(exc)) from exc
    return service.to_record(row)


@router.patch("/trades/{trade_id}", response_model=TradeRecord)
def update_trade(
    trade_id: int, patch: TradePatch, session: Session = Depends(db_session)
) -> TradeRecord:
    try:
        row = service.update_trade(session, trade_id, patch)
    except KeyError as exc:
        raise HTTPException(404, "trade not found") from exc
    except LedgerError as exc:
        raise HTTPException(422, str(exc)) from exc
    return service.to_record(row)


@router.delete("/trades/{trade_id}", status_code=204)
def delete_trade(trade_id: int, session: Session = Depends(db_session)) -> None:
    try:
        service.delete_trade(session, trade_id)
    except KeyError as exc:
        raise HTTPException(404, "trade not found") from exc
    except LedgerError as exc:
        raise HTTPException(422, str(exc)) from exc
