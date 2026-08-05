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


# --- _fetch_one against a realistic FastInfo -------------------------------
#
# Every other test in this file monkeypatches `_fetch_one`, so none of them
# exercised the real yfinance call. That gap hid a total failure: FastInfo
# exposes snake_case ATTRIBUTES but camelCase dict KEYS, so `.get("last_price")`
# returned None for every ticker and the whole portfolio priced as "—".


class _FakeFastInfo:
    """Mimics yfinance FastInfo: snake_case attributes, camelCase keys."""

    _KEYS = {"lastPrice": 492.81, "previousClose": 490.63, "currency": "USD"}

    @property
    def last_price(self) -> float:
        return self._KEYS["lastPrice"]

    @property
    def previous_close(self) -> float:
        return self._KEYS["previousClose"]

    @property
    def currency(self) -> str:
        return self._KEYS["currency"]

    def get(self, key, default=None):
        # Deliberately only knows camelCase, exactly like the real thing.
        return self._KEYS.get(key, default)


class _CamelOnlyFastInfo:
    """A shape with no snake_case attributes at all — exercises the fallback."""

    _KEYS = {"lastPrice": 10.0, "previousClose": 9.0, "currency": "EUR"}

    def get(self, key, default=None):
        return self._KEYS.get(key, default)


def _patch_ticker(monkeypatch, fast_info_obj):
    import yfinance

    class _FakeTicker:
        def __init__(self, ticker):
            self.fast_info = fast_info_obj

    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)


def test_fetch_one_reads_snake_case_attributes_not_dict_keys(monkeypatch):
    """The regression guard: `.get("last_price")` is None on a real FastInfo."""
    _patch_ticker(monkeypatch, _FakeFastInfo())
    quote = pricing._fetch_one("MSFT")
    assert quote.last_price == 492.81
    assert quote.previous_close == 490.63
    assert quote.currency == "USD"


def test_fetch_one_falls_back_to_camel_case_keys(monkeypatch):
    """A shape exposing only mapping keys must still yield a priced quote."""
    _patch_ticker(monkeypatch, _CamelOnlyFastInfo())
    quote = pricing._fetch_one("ASML.AS")
    assert quote.last_price == 10.0
    assert quote.previous_close == 9.0
    assert quote.currency == "EUR"
