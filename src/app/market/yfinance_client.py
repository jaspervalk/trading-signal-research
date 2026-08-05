"""yfinance fetcher with on-disk parquet cache.

Caches one parquet per ticker under `data/cache/prices/{TICKER}.parquet`,
indexed by tz-aware UTC datetime. Re-fetches only missing date ranges.

This is the V1 cache — simple, file-based, single-process. We can swap to
DuckDB or Postgres if scale demands it.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from app.marketdata.symbols import canonical_symbol
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import REPO_ROOT
from app.logging import get_logger

log = get_logger(__name__)

_CACHE_DIR = REPO_ROOT / "data" / "cache" / "prices"


def _cache_path(ticker: str) -> Path:
    """Cache file for `ticker`, keyed on the canonical symbol.

    Keyed canonically so `BRK.B` and `BRK-B` share one file. Previously the
    dot form was rewritten to `BRK_B.parquet` and the dash form to
    `BRK-B.parquet` — two caches for one company, each half-populated
    depending on which caller warmed it.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = canonical_symbol(ticker).replace("/", "_")
    return _CACHE_DIR / f"{safe}.parquet"


def _normalize_index(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure the dataframe is indexed by tz-aware UTC, sorted, dedup'd."""
    if df.empty:
        return df
    idx = pd.to_datetime(df.index, utc=True)
    df = df.copy()
    df.index = idx
    df.index.name = "ts"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def _load_cached(ticker: str) -> pd.DataFrame:
    p = _cache_path(ticker)
    if not p.exists():
        return pd.DataFrame()
    return _normalize_index(pd.read_parquet(p))


def _save_cached(ticker: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    df = _normalize_index(df)
    df.to_parquet(_cache_path(ticker))


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
def _fetch_yf(
    ticker: str, start: datetime, end: datetime, *, interval: str = "1d"
) -> pd.DataFrame:
    # yfinance recognises the dash form only; asking for `BRK.B` returns an
    # empty frame with no error, which downstream reads as "no bars".
    ticker = canonical_symbol(ticker)
    log.info("yfinance.fetch", ticker=ticker, start=str(start.date()), end=str(end.date()), interval=interval)
    raw = yf.download(
        ticker,
        start=start.strftime("%Y-%m-%d"),
        end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
        interval=interval,
        auto_adjust=True,
        actions=False,
        progress=False,
        threads=False,
    )
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]
    return _normalize_index(raw)


def _to_utc_ts(dt: datetime) -> pd.Timestamp:
    if dt.tzinfo is None:
        return pd.Timestamp(dt).tz_localize("UTC")
    return pd.Timestamp(dt).tz_convert("UTC")


def get_daily_bars(
    ticker: str,
    *,
    start: datetime,
    end: datetime,
    refresh_window_days: int = 5,
) -> pd.DataFrame:
    """Return daily OHLCV for `ticker` covering [start, end], using the cache.

    We always re-fetch the most recent `refresh_window_days` from yfinance,
    even when cached, because the most recent bars sometimes get revised.
    """
    cached = _load_cached(ticker)
    needed_start = _to_utc_ts(start)
    needed_end = _to_utc_ts(end)

    if cached.empty:
        fresh = _fetch_yf(ticker, start, end)
        _save_cached(ticker, fresh)
        return fresh.loc[
            (fresh.index >= needed_start) & (fresh.index <= needed_end)
        ]

    cache_start = cached.index.min()
    cache_end = cached.index.max()

    pieces = [cached]

    # Backfill earlier history if cache doesn't cover it.
    if needed_start < cache_start:
        fresh = _fetch_yf(ticker, start, cache_start.to_pydatetime() - timedelta(days=1))
        pieces.append(fresh)

    # Top up forward history (and refresh recent bars).
    refresh_from = max(needed_start, cache_end - timedelta(days=refresh_window_days))
    if needed_end > cache_end - timedelta(days=refresh_window_days):
        fresh = _fetch_yf(ticker, refresh_from.to_pydatetime(), end)
        pieces.append(fresh)

    combined = _normalize_index(pd.concat(pieces))
    _save_cached(ticker, combined)
    return combined.loc[
        (combined.index >= needed_start) & (combined.index <= needed_end)
    ]


def warm_cache(tickers: Iterable[str], *, start: datetime, end: datetime) -> dict[str, int]:
    """Pre-load daily bars for several tickers. Returns {ticker: n_rows}."""
    out: dict[str, int] = {}
    for t in tickers:
        try:
            df = get_daily_bars(t, start=start, end=end)
            out[t] = len(df)
        except Exception as e:  # pragma: no cover
            log.warning("yfinance.warm.error", ticker=t, error=str(e))
            out[t] = 0
    return out


def latest_close_at_or_before(
    ticker: str, ts: datetime, *, lookback_days: int = 30
) -> float | None:
    """Most recent daily close on or before `ts`. Used for activation fills
    when intraday bars aren't being used."""
    ts = ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)
    df = get_daily_bars(ticker, start=ts - timedelta(days=lookback_days), end=ts)
    if df.empty:
        return None
    on_or_before = df[df.index <= _to_utc_ts(ts)]
    if on_or_before.empty:
        return None
    return float(on_or_before["Close"].iloc[-1])
