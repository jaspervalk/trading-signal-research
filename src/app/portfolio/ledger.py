"""The money math: fold trades into positions, and validate a ledger.

Pure — no database, no network, no clock. Weighted-average cost basis:
a buy adds shares and folds fees into basis; a sell realizes
`qty * (price - avg) - fees`, shrinks quantity and total basis, and
leaves average cost untouched. A fully-closed position stays visible with
its realized P&L.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import groupby

from app.portfolio.schema import SIDE_BUY, SIDE_SELL, Position, TradeRecord

EPS = 1e-9


class LedgerError(ValueError):
    """A ledger that cannot be folded: oversell, bad side, mixed currency."""


def _sort_key(t: TradeRecord) -> tuple:
    return (t.traded_at, t.id if t.id is not None else 0)


def _fold_amounts(
    trades: Sequence[TradeRecord],
    price_of: Callable[[TradeRecord], float],
    fees_of: Callable[[TradeRecord], float],
) -> tuple[float, float, float]:
    """Fold one ticker's trades into (quantity, cost_basis, realized_pnl)."""
    qty = 0.0
    basis = 0.0
    realized = 0.0

    for t in trades:
        if t.side not in (SIDE_BUY, SIDE_SELL):
            raise LedgerError(f"{t.ticker}: unknown side {t.side!r}")

        price = price_of(t)
        fees = fees_of(t)

        if t.side == SIDE_BUY:
            basis += t.quantity * price + fees
            qty += t.quantity
            continue

        if t.quantity > qty + EPS:
            raise LedgerError(
                f"{t.ticker}: oversell — selling {t.quantity} on "
                f"{t.traded_at.date()} but only {qty} held at that point"
            )
        avg = basis / qty if qty > EPS else 0.0
        realized += (t.quantity * price - fees) - t.quantity * avg
        basis -= t.quantity * avg
        qty -= t.quantity
        if qty <= EPS:
            qty = 0.0
            basis = 0.0

    return qty, basis, realized


def _check_currency(ticker: str, trades: Sequence[TradeRecord]) -> str:
    currencies = {t.currency for t in trades}
    if len(currencies) > 1:
        raise LedgerError(
            f"{ticker}: mixed currency {sorted(currencies)} — all trades for "
            "one ticker must share a currency"
        )
    return next(iter(currencies))


def fold_trades(trades: Sequence[TradeRecord]) -> list[Position]:
    """Fold a whole ledger into one Position per ticker, sorted by ticker."""
    positions: list[Position] = []
    ordered = sorted(trades, key=lambda t: (t.ticker, *_sort_key(t)))

    for ticker, group in groupby(ordered, key=lambda t: t.ticker):
        rows = list(group)
        currency = _check_currency(ticker, rows)

        qty, basis, realized = _fold_amounts(
            rows, lambda t: t.price_per_share, lambda t: t.fees
        )

        eur_basis = eur_realized = None
        if all(t.eur_amount is not None for t in rows):
            # eur_amount is all-in (fees inside), so per-share price is the
            # total divided by quantity and fees are zero.
            _, eur_basis, eur_realized = _fold_amounts(
                rows,
                lambda t: t.eur_amount / t.quantity,  # type: ignore[operator]
                lambda t: 0.0,
            )

        positions.append(
            Position(
                ticker=ticker,
                currency=currency,
                quantity=qty,
                avg_cost=(basis / qty) if qty > EPS else None,
                cost_basis=basis,
                realized_pnl=realized,
                eur_avg_cost=(
                    (eur_basis / qty) if (eur_basis is not None and qty > EPS) else None
                ),
                eur_cost_basis=eur_basis,
                eur_realized_pnl=eur_realized,
                first_traded_at=min(t.traded_at for t in rows),
                last_traded_at=max(t.traded_at for t in rows),
                trade_count=len(rows),
            )
        )

    return positions


def validate_ledger(trades: Sequence[TradeRecord]) -> None:
    """Raise LedgerError if this ledger cannot be folded. Otherwise silent."""
    fold_trades(trades)
