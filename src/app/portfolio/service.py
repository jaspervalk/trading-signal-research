"""Portfolio service: DB access, write validation, and view assembly.

Every write re-folds the ticker's whole ledger with the candidate change
applied, so a backdated edit that would have oversold at the time is rejected
even when today's quantity would allow it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PortfolioTrade
from app.portfolio import pricing
from app.portfolio.ledger import EPS, LedgerError, fold_trades
from app.portfolio.schema import (
    SIDES,
    PortfolioView,
    Position,
    PositionView,
    TradeIn,
    TradePatch,
    TradeRecord,
)


def to_record(row: PortfolioTrade) -> TradeRecord:
    return TradeRecord(
        id=row.id,
        ticker=row.ticker,
        side=row.side,
        quantity=row.quantity,
        price_per_share=row.price_per_share,
        currency=row.currency,
        fees=row.fees,
        traded_at=_aware(row.traded_at),
        eur_amount=row.eur_amount,
        note=row.note,
    )


def _aware(dt: datetime) -> datetime:
    """SQLite may hand back naive datetimes for tz-aware columns."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def list_trades(session: Session, ticker: str | None = None) -> list[TradeRecord]:
    q = select(PortfolioTrade).order_by(
        PortfolioTrade.traded_at.desc(), PortfolioTrade.id.desc()
    )
    if ticker:
        q = q.where(PortfolioTrade.ticker == ticker.strip().upper())
    return [to_record(r) for r in session.execute(q).scalars().all()]


def _all_records(session: Session) -> list[TradeRecord]:
    q = select(PortfolioTrade)
    return [to_record(r) for r in session.execute(q).scalars().all()]


def _check_basics(record: TradeRecord) -> None:
    if record.side not in SIDES:
        raise LedgerError(f"unknown side {record.side!r} — expected one of {SIDES}")
    if record.traded_at > datetime.now(timezone.utc):
        raise LedgerError("traded_at is in the future")


def _validate_with(session: Session, candidate: TradeRecord, *, replacing: int | None) -> None:
    """Fold the whole ledger with `candidate` applied, and raise if invalid."""
    _check_basics(candidate)
    records = [r for r in _all_records(session) if r.id != replacing]
    records.append(candidate)
    fold_trades(records)


def create_trade(session: Session, payload: TradeIn) -> PortfolioTrade:
    candidate = TradeRecord(
        id=None,
        ticker=payload.ticker.strip().upper(),
        side=payload.side,
        quantity=payload.quantity,
        price_per_share=payload.price_per_share,
        currency=payload.currency.upper(),
        fees=payload.fees,
        traded_at=payload.traded_at,
        eur_amount=payload.eur_amount,
        note=payload.note,
    )
    candidate.traded_at = _aware(candidate.traded_at)
    _validate_with(session, candidate, replacing=None)

    row = PortfolioTrade(
        ticker=candidate.ticker,
        side=candidate.side,
        quantity=candidate.quantity,
        price_per_share=candidate.price_per_share,
        currency=candidate.currency,
        fees=candidate.fees,
        traded_at=candidate.traded_at,
        eur_amount=candidate.eur_amount,
        note=candidate.note,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_trade(session: Session, trade_id: int, patch: TradePatch) -> PortfolioTrade:
    row = session.get(PortfolioTrade, trade_id)
    if row is None:
        raise KeyError(trade_id)

    current = to_record(row)
    data = current.model_dump()
    for field, value in patch.model_dump(exclude_unset=True).items():
        data[field] = value
    candidate = TradeRecord(**data)
    candidate.ticker = candidate.ticker.strip().upper()
    candidate.currency = candidate.currency.upper()
    candidate.traded_at = _aware(candidate.traded_at)

    _validate_with(session, candidate, replacing=trade_id)

    row.ticker = candidate.ticker
    row.side = candidate.side
    row.quantity = candidate.quantity
    row.price_per_share = candidate.price_per_share
    row.currency = candidate.currency
    row.fees = candidate.fees
    row.traded_at = candidate.traded_at
    row.eur_amount = candidate.eur_amount
    row.note = candidate.note
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(row)
    return row


def delete_trade(session: Session, trade_id: int) -> None:
    row = session.get(PortfolioTrade, trade_id)
    if row is None:
        raise KeyError(trade_id)

    remaining = [r for r in _all_records(session) if r.id != trade_id]
    try:
        fold_trades(remaining)
    except LedgerError as exc:
        raise LedgerError(
            f"cannot delete #{trade_id}: the remaining ledger is invalid — {exc}"
        ) from exc

    session.delete(row)
    session.commit()


def _to_view(position: Position, quote: pricing.Quote | None, rate: float | None) -> PositionView:
    view = PositionView(**position.model_dump())
    if quote is None or quote.last_price is None:
        return view

    if quote.currency is not None and quote.currency.upper() != position.currency.upper():
        # The quote is denominated in a different unit than the cost basis —
        # treat this as unpriced rather than silently mixing currencies.
        return view

    view.last_price = quote.last_price
    view.previous_close = quote.previous_close
    if quote.previous_close:
        view.day_change_pct = (
            (quote.last_price - quote.previous_close) / quote.previous_close * 100
        )

    if position.quantity > EPS:
        view.market_value = position.quantity * quote.last_price
        view.unrealized_pnl = view.market_value - position.cost_basis
        if position.cost_basis > EPS:
            view.unrealized_pct = view.unrealized_pnl / position.cost_basis * 100
        if position.currency == "EUR":
            view.market_value_eur = view.market_value
        elif position.currency == "USD" and rate:
            view.market_value_eur = view.market_value / rate

    return view


def build_portfolio_view(session: Session, *, with_quotes: bool = True) -> PortfolioView:
    positions = fold_trades(_all_records(session))

    quotes: dict[str, pricing.Quote | None] = {}
    rate: float | None = None
    errors: list[str] = []
    if with_quotes and positions:
        open_tickers = [p.ticker for p in positions if p.quantity > EPS]
        position_currency = {p.ticker: p.currency for p in positions}
        quotes = pricing.get_quotes(open_tickers)
        errors = [
            t
            for t, q in quotes.items()
            if q is None
            or q.last_price is None
            or (
                q.currency is not None
                and q.currency.upper() != position_currency[t].upper()
            )
        ]
        if any(p.currency == "USD" for p in positions):
            rate = pricing.get_eur_usd_rate()

    views = [_to_view(p, quotes.get(p.ticker), rate) for p in positions]
    open_views = [v for v in views if v.quantity > EPS]
    closed_views = [v for v in views if v.quantity <= EPS]

    priced = [v for v in open_views if v.market_value is not None]
    open_currencies = {v.currency for v in open_views}
    fully_priced = bool(open_views) and len(priced) == len(open_views)
    single_currency = len(open_currencies) <= 1

    total_value = (
        sum(v.market_value for v in priced)
        if single_currency and fully_priced
        else None
    )
    total_unrealized = (
        sum(v.unrealized_pnl for v in priced if v.unrealized_pnl is not None)
        if single_currency and fully_priced
        else None
    )

    all_currencies = {v.currency for v in views}
    total_realized = (
        sum(v.realized_pnl for v in views) if len(all_currencies) <= 1 else None
    )

    eur_convertible = all(v.market_value_eur is not None for v in open_views)
    total_market_value_eur = (
        sum(v.market_value_eur for v in open_views)
        if open_views and eur_convertible
        else None
    )

    return PortfolioView(
        open_positions=sorted(open_views, key=lambda v: v.ticker),
        closed_positions=sorted(closed_views, key=lambda v: v.ticker),
        total_market_value=total_value,
        total_market_value_eur=total_market_value_eur,
        total_unrealized_pnl=total_unrealized,
        total_realized_pnl=total_realized,
        eur_usd_rate=rate,
        quote_errors=sorted(errors),
        as_of=datetime.now(timezone.utc),
    )
