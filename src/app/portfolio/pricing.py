"""Intraday quotes for portfolio valuation.

yfinance `fast_info` is the cheapest path to a last price. Quotes are
~15 minutes delayed — good enough for a position ledger, and stated as such
in the UI. A short in-process TTL cache keeps repeated refreshes from
hammering the API. A ticker that cannot be quoted yields None; it never
raises, so one bad symbol cannot break the portfolio page.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.logging import get_logger

log = get_logger(__name__)

TTL_SECONDS = 60.0
EUR_USD_SYMBOL = "EURUSD=X"


@dataclass(frozen=True)
class Quote:
    ticker: str
    last_price: float | None
    previous_close: float | None
    currency: str | None
    as_of: datetime


_cache: dict[str, tuple[float, Quote]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _monotonic() -> float:
    return time.monotonic()


def clear_cache() -> None:
    """Drop all cached quotes. Used by tests and by an explicit hard refresh."""
    _cache.clear()


def _fetch_one(ticker: str) -> Quote:
    """Fetch one quote from yfinance. Raises on failure; callers isolate."""
    import yfinance as yf

    info = yf.Ticker(ticker).fast_info
    return Quote(
        ticker=ticker,
        last_price=_as_float(info.get("last_price")),
        previous_close=_as_float(info.get("previous_close")),
        currency=info.get("currency"),
        as_of=_now(),
    )


def _as_float(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN check


def get_quotes(tickers: Iterable[str]) -> dict[str, Quote | None]:
    """Quote each ticker, serving fresh cache hits and isolating failures."""
    out: dict[str, Quote | None] = {}
    now = _monotonic()

    for ticker in tickers:
        cached = _cache.get(ticker)
        if cached is not None and now - cached[0] < TTL_SECONDS:
            out[ticker] = cached[1]
            continue
        try:
            quote = _fetch_one(ticker)
        except Exception:  # noqa: BLE001 - one bad symbol must not break the page
            log.warning("portfolio.pricing.quote_failed", ticker=ticker)
            out[ticker] = None
            continue
        _cache[ticker] = (now, quote)
        out[ticker] = quote

    return out


def get_eur_usd_rate() -> float | None:
    """USD per 1 EUR, or None when unavailable."""
    quote = get_quotes([EUR_USD_SYMBOL]).get(EUR_USD_SYMBOL)
    return quote.last_price if quote is not None else None
