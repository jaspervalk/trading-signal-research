"""Quote fetching: caching, per-ticker failure isolation, EUR rate."""

from __future__ import annotations

import pytest

from app.portfolio import pricing


@pytest.fixture(autouse=True)
def _clear():
    pricing.clear_cache()
    yield
    pricing.clear_cache()


def test_get_quotes_returns_quote_per_ticker(monkeypatch):
    def fake(ticker):
        return pricing.Quote(
            ticker=ticker,
            last_price=100.0,
            previous_close=95.0,
            currency="USD",
            as_of=pricing._now(),
        )

    monkeypatch.setattr(pricing, "_fetch_one", fake)
    quotes = pricing.get_quotes(["NVDA", "ASML"])
    assert set(quotes) == {"NVDA", "ASML"}
    assert quotes["NVDA"].last_price == 100.0


def test_failed_ticker_yields_none_and_does_not_raise(monkeypatch):
    def fake(ticker):
        if ticker == "BADTICKER":
            raise RuntimeError("no such symbol")
        return pricing.Quote(ticker, 10.0, 9.0, "USD", pricing._now())

    monkeypatch.setattr(pricing, "_fetch_one", fake)
    quotes = pricing.get_quotes(["NVDA", "BADTICKER"])
    assert quotes["NVDA"] is not None
    assert quotes["BADTICKER"] is None


def test_second_call_within_ttl_is_served_from_cache(monkeypatch):
    calls = []

    def fake(ticker):
        calls.append(ticker)
        return pricing.Quote(ticker, 10.0, 9.0, "USD", pricing._now())

    monkeypatch.setattr(pricing, "_fetch_one", fake)
    pricing.get_quotes(["NVDA"])
    pricing.get_quotes(["NVDA"])
    assert calls == ["NVDA"]


def test_cache_expires_after_ttl(monkeypatch):
    calls = []

    def fake(ticker):
        calls.append(ticker)
        return pricing.Quote(ticker, 10.0, 9.0, "USD", pricing._now())

    monkeypatch.setattr(pricing, "_fetch_one", fake)

    clock = [1000.0]
    monkeypatch.setattr(pricing, "_monotonic", lambda: clock[0])

    pricing.get_quotes(["NVDA"])
    clock[0] += pricing.TTL_SECONDS + 1
    pricing.get_quotes(["NVDA"])
    assert calls == ["NVDA", "NVDA"]


def test_empty_ticker_list_does_not_fetch(monkeypatch):
    def boom(ticker):  # pragma: no cover - must never run
        raise AssertionError("should not fetch")

    monkeypatch.setattr(pricing, "_fetch_one", boom)
    assert pricing.get_quotes([]) == {}


def test_eur_usd_rate_uses_the_fx_symbol(monkeypatch):
    def fake(ticker):
        assert ticker == "EURUSD=X"
        return pricing.Quote(ticker, 1.10, 1.09, "USD", pricing._now())

    monkeypatch.setattr(pricing, "_fetch_one", fake)
    assert pricing.get_eur_usd_rate() == pytest.approx(1.10)


def test_eur_usd_rate_returns_none_on_failure(monkeypatch):
    def fake(ticker):
        raise RuntimeError("offline")

    monkeypatch.setattr(pricing, "_fetch_one", fake)
    assert pricing.get_eur_usd_rate() is None
