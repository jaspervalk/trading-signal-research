"""Portfolio Manager endpoints: the manual trade ledger and derived view.

Positions are derived, never stored, so there are no position endpoints —
holdings are only ever read through GET /portfolio.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.portfolio import service
from app.portfolio.ledger import LedgerError
from app.portfolio.policy import build_policy_view, load_policy
from app.portfolio.schema import (
    FactorSliceOut,
    PolicyPositionOut,
    PolicyViewOut,
    PortfolioView,
    TradeIn,
    TradePatch,
    TradeRecord,
    TriggerOut,
)
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


@router.get("/policy", response_model=PolicyViewOut)
def get_policy(
    cash_base: float = Query(0.0, description="Uninvested balance, in the policy's base currency."),
    session: Session = Depends(db_session),
) -> PolicyViewOut:
    try:
        portfolio = service.build_portfolio_view(session, with_quotes=True)
    except LedgerError as exc:
        raise HTTPException(
            422, f"portfolio ledger is inconsistent: {exc}. Fix it from the trade ledger."
        ) from exc

    policy = load_policy()
    view = build_policy_view(portfolio, policy=policy, cash_base=cash_base)
    return PolicyViewOut(
        available=view.available,
        reason=view.reason,
        base_currency=view.base_currency,
        total_base=view.total_base,
        positions=[
            PolicyPositionOut(
                ticker=p.ticker,
                factor=p.factor,
                value_base=p.value_base,
                weight=p.weight,
                target=p.target,
                status=p.status,
                band_low=p.band_low,
                band_high=p.band_high,
                band_status=p.band_status,
                deviation_pp=p.deviation_pp,
            )
            for p in view.positions
        ],
        factors=[
            FactorSliceOut(name=f.name, value_base=f.value_base, weight=f.weight)
            for f in view.factors
        ],
        ai_weight=view.ai_weight,
        ai_target_max=view.ai_target_max,
        ai_excess_pp=view.ai_excess_pp,
        missing_targets=view.missing_targets,
        buy_order=[p.ticker for p in view.most_underweight],
        triggers=[
            TriggerOut(
                ticker=t.ticker, status=t.status, condition=t.condition, next_report=t.next_report
            )
            for t in policy.triggers
        ],
        monthly_trade_budget=policy.monthly_trade_budget,
    )


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
