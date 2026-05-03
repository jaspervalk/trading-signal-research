"""Compute MarketSnapshot rows for a (ticker, snapshot_at).

Strict leakage discipline: every value uses only daily bars with index
`<= snapshot_at`. A snapshot is reproducible: passing the same inputs
always yields the same numbers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from app.market.yfinance_client import get_daily_bars


def _slice_at_or_before(df: pd.DataFrame, ts: datetime) -> pd.DataFrame:
    if ts.tzinfo is None:
        cutoff = pd.Timestamp(ts).tz_localize("UTC")
    else:
        cutoff = pd.Timestamp(ts).tz_convert("UTC")
    return df[df.index <= cutoff]


def _safe_div(a: float, b: float) -> float | None:
    if b is None or b == 0:
        return None
    return a / b


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> float | None:
    if len(close) < n + 1:
        return None
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(n).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else None


def _rsi(close: pd.Series, n: int = 14) -> float | None:
    if len(close) < n + 1:
        return None
    delta = close.diff()
    up = delta.clip(lower=0).rolling(n).mean()
    down = (-delta.clip(upper=0)).rolling(n).mean()
    rs = up / down.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).iloc[-1]
    return float(rsi) if pd.notna(rsi) else None


def _ma(close: pd.Series, n: int) -> float | None:
    if len(close) < n:
        return None
    v = close.rolling(n).mean().iloc[-1]
    return float(v) if pd.notna(v) else None


def compute_snapshot_fields(
    ticker: str,
    snapshot_at: datetime,
    *,
    benchmark_ticker: str = "SPY",
    history_days: int = 365,
) -> dict[str, float | int | None]:
    """Compute the fields needed by `MarketSnapshot`. Returns a dict; the
    caller is responsible for persisting (or refreshing) the row."""
    if snapshot_at.tzinfo is None:
        snapshot_at = snapshot_at.replace(tzinfo=UTC)

    start = snapshot_at - timedelta(days=history_days)
    df = get_daily_bars(ticker, start=start, end=snapshot_at)
    df = _slice_at_or_before(df, snapshot_at)

    spy = get_daily_bars(benchmark_ticker, start=start, end=snapshot_at)
    spy = _slice_at_or_before(spy, snapshot_at)

    if df.empty:
        return {
            "price": None, "volume": None, "atr_14": None,
            "return_5d": None, "return_21d": None, "return_63d": None,
            "dist_to_ma20": None, "dist_to_ma50": None, "dist_to_ma200": None,
            "rsi_14": None, "volume_ratio_20": None, "spy_return_21d": None,
        }

    close = df["Close"].astype(float)
    price = float(close.iloc[-1])
    volume_today = int(df["Volume"].iloc[-1]) if "Volume" in df else None

    def _ret(n: int) -> float | None:
        if len(close) < n + 1:
            return None
        prior = close.iloc[-(n + 1)]
        return _safe_div(price - prior, prior)

    ma20 = _ma(close, 20)
    ma50 = _ma(close, 50)
    ma200 = _ma(close, 200)

    vol20 = df["Volume"].rolling(20).mean().iloc[-1] if "Volume" in df else None
    vol_ratio = (
        _safe_div(float(volume_today), float(vol20))
        if vol20 is not None and pd.notna(vol20) and volume_today is not None
        else None
    )

    spy_close = spy["Close"].astype(float) if not spy.empty else pd.Series(dtype=float)
    spy_return_21d = (
        _safe_div(spy_close.iloc[-1] - spy_close.iloc[-22], spy_close.iloc[-22])
        if len(spy_close) >= 22
        else None
    )

    return {
        "price": price,
        "volume": volume_today,
        "atr_14": _atr(df["High"], df["Low"], close, 14),
        "return_5d": _ret(5),
        "return_21d": _ret(21),
        "return_63d": _ret(63),
        "dist_to_ma20": _safe_div(price - ma20, ma20) if ma20 else None,
        "dist_to_ma50": _safe_div(price - ma50, ma50) if ma50 else None,
        "dist_to_ma200": _safe_div(price - ma200, ma200) if ma200 else None,
        "rsi_14": _rsi(close, 14),
        "volume_ratio_20": vol_ratio,
        "spy_return_21d": spy_return_21d,
    }
