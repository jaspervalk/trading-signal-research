"""Ledger fold: weighted-average cost basis, realized P&L, validation.

Real values, no mocks — this module reports the owner's money.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.portfolio.ledger import LedgerError, fold_trades, validate_ledger
from app.portfolio.schema import TradeRecord

BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _t(ticker, side, qty, price, *, day=0, fees=0.0, eur=None, tid=None):
    return TradeRecord(
        id=tid,
        ticker=ticker,
        side=side,
        quantity=qty,
        price_per_share=price,
        currency="USD",
        fees=fees,
        traded_at=BASE + timedelta(days=day),
        eur_amount=eur,
    )


def test_single_buy_produces_position():
    [p] = fold_trades([_t("NVDA", "buy", 10, 100.0)])
    assert p.ticker == "NVDA"
    assert p.quantity == 10
    assert p.cost_basis == 1000.0
    assert p.avg_cost == 100.0
    assert p.realized_pnl == 0.0
    assert p.is_open is True


def test_multiple_buys_weighted_average():
    trades = [
        _t("NVDA", "buy", 10, 100.0, day=0),
        _t("NVDA", "buy", 30, 200.0, day=1),
    ]
    [p] = fold_trades(trades)
    assert p.quantity == 40
    assert p.cost_basis == 7000.0
    assert p.avg_cost == 175.0


def test_buy_fees_raise_cost_basis():
    [p] = fold_trades([_t("NVDA", "buy", 10, 100.0, fees=25.0)])
    assert p.cost_basis == 1025.0
    assert p.avg_cost == 102.5


def test_partial_sell_realizes_and_keeps_avg_cost():
    trades = [
        _t("NVDA", "buy", 10, 100.0, day=0),
        _t("NVDA", "sell", 4, 150.0, day=1),
    ]
    [p] = fold_trades(trades)
    assert p.quantity == 6
    assert p.realized_pnl == pytest.approx(200.0)  # 4 * (150 - 100)
    assert p.avg_cost == pytest.approx(100.0)      # unchanged by a sell
    assert p.cost_basis == pytest.approx(600.0)


def test_sell_fees_reduce_proceeds():
    trades = [
        _t("NVDA", "buy", 10, 100.0, day=0),
        _t("NVDA", "sell", 4, 150.0, day=1, fees=10.0),
    ]
    [p] = fold_trades(trades)
    assert p.realized_pnl == pytest.approx(190.0)


def test_full_close_keeps_position_visible():
    trades = [
        _t("NVDA", "buy", 10, 100.0, day=0),
        _t("NVDA", "sell", 10, 120.0, day=1),
    ]
    [p] = fold_trades(trades)
    assert p.quantity == 0
    assert p.cost_basis == 0.0
    assert p.avg_cost is None
    assert p.realized_pnl == pytest.approx(200.0)
    assert p.is_open is False


def test_oversell_is_rejected():
    trades = [
        _t("NVDA", "buy", 5, 100.0, day=0),
        _t("NVDA", "sell", 6, 120.0, day=1),
    ]
    with pytest.raises(LedgerError, match="oversell"):
        validate_ledger(trades)


def test_backdated_sell_that_oversold_at_the_time_is_rejected():
    """Today's quantity would allow it; the chronological fold must not."""
    trades = [
        _t("NVDA", "buy", 5, 100.0, day=0),
        _t("NVDA", "sell", 8, 120.0, day=1),   # only 5 held on day 1
        _t("NVDA", "buy", 10, 110.0, day=2),
    ]
    with pytest.raises(LedgerError, match="oversell"):
        validate_ledger(trades)


def test_trades_are_folded_chronologically_not_in_input_order():
    trades = [
        _t("NVDA", "sell", 4, 150.0, day=1),
        _t("NVDA", "buy", 10, 100.0, day=0),
    ]
    [p] = fold_trades(trades)
    assert p.quantity == 6
    assert p.realized_pnl == pytest.approx(200.0)


def test_multiple_tickers_stay_isolated():
    trades = [
        _t("NVDA", "buy", 10, 100.0),
        _t("ASML", "buy", 2, 900.0),
    ]
    positions = {p.ticker: p for p in fold_trades(trades)}
    assert positions["NVDA"].cost_basis == 1000.0
    assert positions["ASML"].cost_basis == 1800.0


def test_mixed_currency_for_one_ticker_is_rejected():
    a = _t("NVDA", "buy", 1, 100.0, day=0)
    b = _t("NVDA", "buy", 1, 100.0, day=1)
    b.currency = "EUR"
    with pytest.raises(LedgerError, match="currency"):
        validate_ledger([a, b])


def test_eur_parallel_ledger_when_all_trades_have_eur_amount():
    trades = [
        _t("NVDA", "buy", 10, 100.0, day=0, eur=920.0),
        _t("NVDA", "buy", 10, 120.0, day=1, eur=1100.0),
    ]
    [p] = fold_trades(trades)
    assert p.eur_cost_basis == pytest.approx(2020.0)
    assert p.eur_avg_cost == pytest.approx(101.0)


def test_eur_view_is_none_when_any_trade_lacks_eur_amount():
    trades = [
        _t("NVDA", "buy", 10, 100.0, day=0, eur=920.0),
        _t("NVDA", "buy", 10, 120.0, day=1, eur=None),
    ]
    [p] = fold_trades(trades)
    assert p.eur_cost_basis is None
    assert p.eur_avg_cost is None


def test_empty_ledger_yields_no_positions():
    assert fold_trades([]) == []
