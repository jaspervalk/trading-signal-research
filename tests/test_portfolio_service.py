"""Service layer: validation on write, and view assembly with quotes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.portfolio import pricing, service
from app.portfolio.ledger import LedgerError
from app.portfolio.schema import TradeIn, TradePatch

BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _in(ticker="NVDA", side="buy", qty=10.0, price=100.0, day=0, **kw):
    return TradeIn(
        ticker=ticker,
        side=side,
        quantity=qty,
        price_per_share=price,
        traded_at=BASE + timedelta(days=day),
        **kw,
    )


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(pricing, "_fetch_one", lambda t: pricing.Quote(
        ticker=t, last_price=150.0, previous_close=140.0,
        currency="USD", as_of=pricing._now(),
    ))
    pricing.clear_cache()
    yield
    pricing.clear_cache()


def test_create_trade_persists_and_uppercases_ticker(session):
    row = service.create_trade(session, _in(ticker="nvda"))
    assert row.id is not None
    assert row.ticker == "NVDA"


def test_create_rejects_future_trade_date(session):
    future = datetime.now(timezone.utc) + timedelta(days=2)
    payload = TradeIn(
        ticker="NVDA", side="buy", quantity=1, price_per_share=100.0,
        traded_at=future,
    )
    with pytest.raises(LedgerError, match="future"):
        service.create_trade(session, payload)


def test_create_rejects_unknown_side(session):
    payload = TradeIn(
        ticker="NVDA", side="short", quantity=1, price_per_share=100.0,
        traded_at=BASE,
    )
    with pytest.raises(LedgerError, match="side"):
        service.create_trade(session, payload)


def test_create_rejects_oversell(session):
    service.create_trade(session, _in(qty=5, day=0))
    with pytest.raises(LedgerError, match="oversell"):
        service.create_trade(session, _in(side="sell", qty=9, day=1))


def test_rejected_trade_is_not_persisted(session):
    service.create_trade(session, _in(qty=5, day=0))
    with pytest.raises(LedgerError):
        service.create_trade(session, _in(side="sell", qty=9, day=1))
    assert len(service.list_trades(session)) == 1


def test_update_trade_revalidates_whole_ledger(session):
    service.create_trade(session, _in(qty=5, day=0))
    sell = service.create_trade(session, _in(side="sell", qty=5, day=1))
    with pytest.raises(LedgerError, match="oversell"):
        service.update_trade(session, sell.id, TradePatch(quantity=99))


def test_delete_trade_removes_it(session):
    row = service.create_trade(session, _in())
    service.delete_trade(session, row.id)
    assert service.list_trades(session) == []


def test_delete_missing_trade_raises(session):
    with pytest.raises(KeyError):
        service.delete_trade(session, 4242)


def test_delete_buy_that_a_later_sell_depends_on_is_refused(session):
    buy = service.create_trade(session, _in(qty=10, day=0))
    service.create_trade(session, _in(side="sell", qty=5, day=1))

    with pytest.raises(LedgerError, match="cannot delete"):
        service.delete_trade(session, buy.id)

    # Refused delete must not have touched the ledger.
    assert len(service.list_trades(session)) == 2


def test_delete_sell_then_buy_both_succeed(session):
    buy = service.create_trade(session, _in(qty=10, day=0))
    sell = service.create_trade(session, _in(side="sell", qty=5, day=1))

    service.delete_trade(session, sell.id)
    service.delete_trade(session, buy.id)

    assert service.list_trades(session) == []


def test_build_view_splits_open_and_closed(session):
    service.create_trade(session, _in(ticker="NVDA", qty=10, day=0))
    service.create_trade(session, _in(ticker="ASML", qty=4, price=800.0, day=0))
    service.create_trade(session, _in(ticker="ASML", side="sell", qty=4, price=900.0, day=1))

    view = service.build_portfolio_view(session)
    assert [p.ticker for p in view.open_positions] == ["NVDA"]
    assert [p.ticker for p in view.closed_positions] == ["ASML"]
    assert view.total_realized_pnl == pytest.approx(400.0)


def test_build_view_applies_quotes(session):
    service.create_trade(session, _in(qty=10, price=100.0))
    view = service.build_portfolio_view(session)
    [pos] = view.open_positions
    assert pos.last_price == 150.0
    assert pos.market_value == pytest.approx(1500.0)
    assert pos.unrealized_pnl == pytest.approx(500.0)
    assert pos.unrealized_pct == pytest.approx(50.0)
    assert pos.day_change_pct == pytest.approx((150.0 - 140.0) / 140.0 * 100)


def test_unquotable_ticker_is_reported_not_fatal(session, monkeypatch):
    service.create_trade(session, _in(ticker="NOSUCH"))
    monkeypatch.setattr(pricing, "_fetch_one", lambda t: (_ for _ in ()).throw(RuntimeError()))
    pricing.clear_cache()

    view = service.build_portfolio_view(session)
    [pos] = view.open_positions
    assert pos.last_price is None
    assert pos.market_value is None
    assert "NOSUCH" in view.quote_errors


def test_build_view_without_quotes_skips_pricing(session):
    service.create_trade(session, _in())
    view = service.build_portfolio_view(session, with_quotes=False)
    assert view.open_positions[0].last_price is None
    assert view.quote_errors == []


def test_empty_portfolio_view(session):
    view = service.build_portfolio_view(session)
    assert view.open_positions == []
    assert view.total_realized_pnl == 0.0


def test_mixed_currency_open_positions_yield_none_totals(session):
    service.create_trade(session, _in(ticker="NVDA", qty=10, price=100.0, day=0))
    service.create_trade(
        session, _in(ticker="ASML", qty=4, price=800.0, day=0, currency="EUR")
    )

    view = service.build_portfolio_view(session)
    assert view.total_market_value is None
    assert view.total_unrealized_pnl is None
    assert view.total_realized_pnl is None


def test_closed_position_in_other_currency_blocks_realized_total(session):
    service.create_trade(session, _in(ticker="NVDA", qty=10, price=100.0, day=0))
    service.create_trade(
        session, _in(ticker="ASML", qty=4, price=800.0, day=0, currency="EUR")
    )
    service.create_trade(
        session,
        _in(ticker="ASML", side="sell", qty=4, price=900.0, day=1, currency="EUR"),
    )

    view = service.build_portfolio_view(session)
    assert view.total_realized_pnl is None


def test_partial_quote_failure_blocks_total_market_value(session, monkeypatch):
    service.create_trade(session, _in(ticker="NVDA", qty=10, price=100.0, day=0))
    service.create_trade(session, _in(ticker="NOSUCH", qty=5, price=50.0, day=0))

    def _selective_fetch(ticker):
        if ticker == "NOSUCH":
            raise RuntimeError("no quote")
        return pricing.Quote(
            ticker=ticker, last_price=150.0, previous_close=140.0,
            currency="USD", as_of=pricing._now(),
        )

    monkeypatch.setattr(pricing, "_fetch_one", _selective_fetch)
    pricing.clear_cache()

    view = service.build_portfolio_view(session)
    assert view.total_market_value is None
    assert "NOSUCH" in view.quote_errors


def test_missing_fx_rate_blocks_eur_total(session, monkeypatch):
    service.create_trade(session, _in(ticker="NVDA", qty=10, price=100.0, day=0))
    service.create_trade(
        session, _in(ticker="ASML", qty=4, price=800.0, day=0, currency="EUR")
    )
    monkeypatch.setattr(pricing, "get_eur_usd_rate", lambda: None)

    view = service.build_portfolio_view(session)
    assert view.total_market_value_eur is None


def test_create_trade_accepts_naive_traded_at(session):
    payload = TradeIn(
        ticker="NVDA", side="buy", quantity=1, price_per_share=100.0,
        traded_at=BASE.replace(tzinfo=None),
    )
    row = service.create_trade(session, payload)
    assert row.id is not None
    record = service.to_record(row)
    assert record.traded_at.tzinfo is not None


def test_quote_currency_mismatch_leaves_position_unpriced(session, monkeypatch):
    service.create_trade(session, _in(ticker="NVDA", qty=10, price=100.0, currency="USD"))
    monkeypatch.setattr(pricing, "_fetch_one", lambda t: pricing.Quote(
        ticker=t, last_price=150.0, previous_close=140.0,
        currency="EUR", as_of=pricing._now(),
    ))
    pricing.clear_cache()

    view = service.build_portfolio_view(session)
    [pos] = view.open_positions
    assert pos.market_value is None
    assert pos.last_price is None
    assert "NVDA" in view.quote_errors


def test_single_currency_fully_priced_portfolio_has_totals(session):
    service.create_trade(session, _in(ticker="NVDA", qty=10, price=100.0, day=0))
    service.create_trade(session, _in(ticker="AAPL", qty=5, price=50.0, day=0))

    view = service.build_portfolio_view(session)
    assert view.total_market_value == pytest.approx(10 * 150.0 + 5 * 150.0)
    assert view.total_unrealized_pnl == pytest.approx(
        (10 * 150.0 - 10 * 100.0) + (5 * 150.0 - 5 * 50.0)
    )
    assert view.total_realized_pnl == pytest.approx(0.0)
