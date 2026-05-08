"""Indicator computations on a leakage-controlled price slice.

Every public function in this module takes a price DataFrame and an
`as_of` cutoff. Bars with `index > as_of` are dropped before any math
runs. There is no global / mutable state.

Math is deliberately transparent — pandas rolling operations, no opaque
TA library. Each function returns either a single float or `None` when
there isn't enough history. The orchestrator composes these into an
`IndicatorPanel` plus a `MarketSnapshotPanel`.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from app.analysis.schema import IndicatorPanel, MarketSnapshotPanel

TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Slice helper


def slice_at_or_before(df: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    """Return a copy of `df` with rows whose index is `<= as_of`.

    Inputs with naive `as_of` are interpreted as UTC. Empty input returns
    empty output.
    """
    if df.empty:
        return df
    if as_of.tzinfo is None:
        cutoff = pd.Timestamp(as_of).tz_localize("UTC")
    else:
        cutoff = pd.Timestamp(as_of).tz_convert("UTC")
    return df[df.index <= cutoff].copy()


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    if b == 0 or (isinstance(b, float) and math.isnan(b)):
        return None
    return a / b


def _last_or_none(series: pd.Series) -> float | None:
    if series is None or series.empty:
        return None
    v = series.iloc[-1]
    return float(v) if pd.notna(v) else None


# ---------------------------------------------------------------------------
# Trend (moving averages)


def sma(close: pd.Series, n: int) -> float | None:
    if len(close) < n:
        return None
    return _last_or_none(close.rolling(n).mean())


def ema(close: pd.Series, n: int) -> float | None:
    if len(close) < n:
        return None
    return _last_or_none(close.ewm(span=n, adjust=False).mean())


def ma_slope_pct(close: pd.Series, ma_window: int, lookback: int) -> float | None:
    """Percent change of the moving-average value across `lookback` bars.

    A simple proxy for "is this MA pointing up?" — the value of the MA
    `lookback` bars ago vs its current value, expressed as a fraction.
    """
    if len(close) < ma_window + lookback:
        return None
    ma_series = close.rolling(ma_window).mean()
    now = ma_series.iloc[-1]
    past = ma_series.iloc[-(lookback + 1)]
    if pd.isna(now) or pd.isna(past) or past == 0:
        return None
    return float((now - past) / past)


def ma_alignment(values: dict[str, float | None]) -> str:
    """Classify trend direction from the moving-average stack.

    Returns one of `bullish_stack` / `bearish_stack` / `mixed` / `insufficient`.

    The test is the standard institutional trend filter:
        bullish_stack ⇔ price > sma_50 > sma_200
        bearish_stack ⇔ price < sma_50 < sma_200
        mixed         otherwise
        insufficient  if sma_50 or sma_200 is missing.

    The 20-day and 150-day MAs are intermediate signals — useful for slope
    and pullback context, but treating them as gating produces too many
    "mixed" reads on perfectly normal pullback patterns (e.g., when the
    50-SMA snaps below the 150-SMA after a multi-week dip even though the
    primary trend is still up).
    """
    price = values.get("price")
    s50 = values.get("sma_50")
    s200 = values.get("sma_200")
    if price is None or s50 is None or s200 is None:
        return "insufficient"
    if price > s50 > s200:
        return "bullish_stack"
    if price < s50 < s200:
        return "bearish_stack"
    return "mixed"


# ---------------------------------------------------------------------------
# Momentum / volatility


def rsi(close: pd.Series, n: int = 14) -> float | None:
    """Wilder-style RSI(n).

    Edge cases:
    - Monotonic uptrend (no down moves over the lookback) → 100.0
    - Monotonic downtrend (no up moves) → 0.0
    - Insufficient bars → None
    """
    if len(close) < n + 1:
        return None
    delta = close.diff()
    up = delta.clip(lower=0).rolling(n).mean()
    down = (-delta.clip(upper=0)).rolling(n).mean()
    last_up = up.iloc[-1]
    last_down = down.iloc[-1]
    if pd.isna(last_up) or pd.isna(last_down):
        return None
    if last_down == 0 and last_up == 0:
        return 50.0  # flat — neutral
    if last_down == 0:
        return 100.0
    if last_up == 0:
        return 0.0
    rs = last_up / last_down
    return float(100 - 100 / (1 + rs))


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14
) -> float | None:
    if len(close) < n + 1:
        return None
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return _last_or_none(tr.rolling(n).mean())


def realized_vol_annualized(close: pd.Series, n: int = 21) -> float | None:
    """Annualized stdev of log returns over the last `n` bars."""
    if len(close) < n + 1:
        return None
    log_ret = np.log(close / close.shift(1)).dropna()
    if len(log_ret) < n:
        return None
    sigma_daily = float(log_ret.iloc[-n:].std(ddof=1))
    if math.isnan(sigma_daily):
        return None
    return sigma_daily * math.sqrt(TRADING_DAYS_PER_YEAR)


# ---------------------------------------------------------------------------
# Volume


def volume_ratio(volume: pd.Series, n: int = 20) -> float | None:
    """Latest volume / N-day average volume."""
    if len(volume) < n + 1:
        return None
    avg = volume.rolling(n).mean().iloc[-1]
    last = volume.iloc[-1]
    if pd.isna(avg) or avg == 0 or pd.isna(last):
        return None
    return float(last / avg)


def avg_volume(volume: pd.Series, n: int) -> float | None:
    if len(volume) < n:
        return None
    return _last_or_none(volume.rolling(n).mean())


# ---------------------------------------------------------------------------
# Returns


def trailing_return(close: pd.Series, n: int) -> float | None:
    """Return over `n` bars: (close[-1] - close[-1-n]) / close[-1-n]."""
    if len(close) < n + 1:
        return None
    last = close.iloc[-1]
    prior = close.iloc[-(n + 1)]
    if pd.isna(last) or pd.isna(prior) or prior == 0:
        return None
    return float((last - prior) / prior)


def gap_from_prev_close(open_: pd.Series, close: pd.Series) -> float | None:
    if len(close) < 2 or len(open_) < 1:
        return None
    last_open = open_.iloc[-1]
    prev_close = close.iloc[-2]
    if pd.isna(last_open) or pd.isna(prev_close) or prev_close == 0:
        return None
    return float((last_open - prev_close) / prev_close)


# ---------------------------------------------------------------------------
# 52w high/low and breakout context


def high_low_52w(close: pd.Series) -> tuple[float | None, float | None]:
    if len(close) == 0:
        return None, None
    window = close.iloc[-min(252, len(close)) :]
    return float(window.max()), float(window.min())


# ---------------------------------------------------------------------------
# Relative strength vs benchmark


def relative_strength(
    close: pd.Series, benchmark_close: pd.Series, n: int
) -> float | None:
    """Excess return over the benchmark across the last `n` bars.

    Defined as `ticker_return_n - benchmark_return_n`, so positive = the
    ticker outperformed SPY (or whatever benchmark was passed) over the
    last n bars.

    Requires both series to have at least `n+1` bars after their common
    intersection.
    """
    if close.empty or benchmark_close.empty:
        return None
    aligned = pd.concat([close, benchmark_close], axis=1, join="inner").dropna()
    if len(aligned) < n + 1:
        return None
    aligned.columns = ["t", "b"]
    t_ret = (aligned["t"].iloc[-1] - aligned["t"].iloc[-(n + 1)]) / aligned["t"].iloc[
        -(n + 1)
    ]
    b_ret = (aligned["b"].iloc[-1] - aligned["b"].iloc[-(n + 1)]) / aligned["b"].iloc[
        -(n + 1)
    ]
    return float(t_ret - b_ret)


# ---------------------------------------------------------------------------
# Composite builders — the two panel-shaped outputs.


def build_indicator_panel(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    *,
    as_of: datetime,
) -> IndicatorPanel:
    """Compose the IndicatorPanel from sliced ticker + benchmark frames.

    `df` is expected to have OHLCV columns indexed by tz-aware UTC.
    `benchmark_df` is the same shape for SPY (or a configured benchmark).
    `as_of` is enforced — both frames are sliced before any math.
    """
    df = slice_at_or_before(df, as_of)
    bench = slice_at_or_before(benchmark_df, as_of)

    if df.empty:
        return IndicatorPanel()

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float) if "Volume" in df else pd.Series(dtype=float)

    price = float(close.iloc[-1])

    sma_20 = sma(close, 20)
    sma_50 = sma(close, 50)
    sma_150 = sma(close, 150)
    sma_200 = sma(close, 200)
    ema_8 = ema(close, 8)
    ema_21 = ema(close, 21)

    panel = IndicatorPanel(
        sma_20=sma_20,
        sma_50=sma_50,
        sma_150=sma_150,
        sma_200=sma_200,
        ema_8=ema_8,
        ema_21=ema_21,
        dist_to_sma_20_pct=_safe_div(price - sma_20, sma_20) if sma_20 else None,
        dist_to_sma_50_pct=_safe_div(price - sma_50, sma_50) if sma_50 else None,
        dist_to_sma_150_pct=_safe_div(price - sma_150, sma_150) if sma_150 else None,
        dist_to_sma_200_pct=_safe_div(price - sma_200, sma_200) if sma_200 else None,
        dist_to_ema_8_pct=_safe_div(price - ema_8, ema_8) if ema_8 else None,
        dist_to_ema_21_pct=_safe_div(price - ema_21, ema_21) if ema_21 else None,
        sma_50_slope_21d_pct=ma_slope_pct(close, 50, 21),
        sma_200_slope_63d_pct=ma_slope_pct(close, 200, 63),
        ma_alignment=ma_alignment(
            {
                "price": price,
                "sma_20": sma_20,
                "sma_50": sma_50,
                "sma_150": sma_150,
                "sma_200": sma_200,
            }
        ),
        rsi_14=rsi(close, 14),
        atr_14=atr(high, low, close, 14),
        realized_vol_21d_annualized=realized_vol_annualized(close, 21),
        volume_ratio_20=volume_ratio(volume, 20) if not volume.empty else None,
    )
    atr_val = panel.atr_14
    if atr_val is not None and price > 0:
        panel.atr_14_pct = atr_val / price

    if not bench.empty:
        bench_close = bench["Close"].astype(float)
        panel.relative_strength_vs_spy_63d = relative_strength(close, bench_close, 63)
        panel.relative_strength_vs_spy_126d = relative_strength(close, bench_close, 126)

    return panel


def build_market_snapshot(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    *,
    as_of: datetime,
) -> MarketSnapshotPanel:
    """Compose the MarketSnapshotPanel from sliced ticker + benchmark frames."""
    df = slice_at_or_before(df, as_of)
    bench = slice_at_or_before(benchmark_df, as_of)

    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)

    if df.empty:
        return MarketSnapshotPanel(as_of=as_of)

    close = df["Close"].astype(float)
    open_ = df["Open"].astype(float) if "Open" in df else pd.Series(dtype=float)
    volume = df["Volume"].astype(float) if "Volume" in df else pd.Series(dtype=float)

    last_close = float(close.iloc[-1])
    last_bar_at = df.index[-1].to_pydatetime()
    if last_bar_at.tzinfo is None:
        last_bar_at = last_bar_at.replace(tzinfo=UTC)

    daily_volume = int(volume.iloc[-1]) if not volume.empty and pd.notna(volume.iloc[-1]) else None
    avg_v_20 = avg_volume(volume, 20) if not volume.empty else None
    avg_v_63 = avg_volume(volume, 63) if not volume.empty else None
    dollar_volume = (
        float(daily_volume * last_close)
        if daily_volume is not None
        else None
    )

    high_52, low_52 = high_low_52w(close)
    pct_off_high = _safe_div(last_close - high_52, high_52) if high_52 else None
    pct_off_low = _safe_div(last_close - low_52, low_52) if low_52 else None

    panel = MarketSnapshotPanel(
        as_of=as_of,
        last_close=last_close,
        last_bar_at=last_bar_at,
        daily_volume=daily_volume,
        avg_volume_20d=avg_v_20,
        avg_volume_63d=avg_v_63,
        dollar_volume=dollar_volume,
        high_52w=high_52,
        low_52w=low_52,
        pct_off_52w_high=pct_off_high,
        pct_off_52w_low=pct_off_low,
        return_1d=trailing_return(close, 1),
        return_5d=trailing_return(close, 5),
        return_21d=trailing_return(close, 21),
        return_63d=trailing_return(close, 63),
        return_126d=trailing_return(close, 126),
        return_252d=trailing_return(close, 252),
        gap_from_prev_close=gap_from_prev_close(open_, close) if not open_.empty else None,
    )

    if not bench.empty:
        bench_close = bench["Close"].astype(float)
        panel.spy_return_5d = trailing_return(bench_close, 5)
        panel.spy_return_21d = trailing_return(bench_close, 21)
        panel.spy_return_63d = trailing_return(bench_close, 63)
        if panel.return_5d is not None and panel.spy_return_5d is not None:
            panel.excess_return_5d = panel.return_5d - panel.spy_return_5d
        if panel.return_21d is not None and panel.spy_return_21d is not None:
            panel.excess_return_21d = panel.return_21d - panel.spy_return_21d
        if panel.return_63d is not None and panel.spy_return_63d is not None:
            panel.excess_return_63d = panel.return_63d - panel.spy_return_63d

    return panel


__all__ = [
    "TRADING_DAYS_PER_YEAR",
    "atr",
    "avg_volume",
    "build_indicator_panel",
    "build_market_snapshot",
    "ema",
    "gap_from_prev_close",
    "high_low_52w",
    "ma_alignment",
    "ma_slope_pct",
    "realized_vol_annualized",
    "relative_strength",
    "rsi",
    "slice_at_or_before",
    "sma",
    "trailing_return",
    "volume_ratio",
]
