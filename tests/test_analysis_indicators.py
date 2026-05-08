"""Unit tests for src/app/analysis/indicators.py.

Synthetic data only — every test runs in milliseconds and asserts exact
numerical results where possible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.analysis.indicators import (
    atr,
    build_indicator_panel,
    build_market_snapshot,
    ema,
    high_low_52w,
    ma_alignment,
    ma_slope_pct,
    realized_vol_annualized,
    relative_strength,
    rsi,
    slice_at_or_before,
    sma,
    trailing_return,
    volume_ratio,
)


def _df_from_closes(closes: list[float], *, start: str = "2025-01-01") -> pd.DataFrame:
    """Helper: build a minimal OHLCV frame with daily UTC timestamps."""
    idx = pd.date_range(start=start, periods=len(closes), freq="D", tz="UTC")
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Close": closes,
            "Volume": [1_000_000 + i * 1000 for i in range(len(closes))],
        },
        index=idx,
    )
    df.index.name = "ts"
    return df


# ---------------------------------------------------------------------------
# slice_at_or_before


def test_slice_at_or_before_drops_future_bars():
    df = _df_from_closes([1, 2, 3, 4, 5])
    cutoff = datetime(2025, 1, 3, tzinfo=UTC)
    out = slice_at_or_before(df, cutoff)
    assert len(out) == 3
    assert out["Close"].iloc[-1] == 3


def test_slice_at_or_before_handles_naive_datetime():
    df = _df_from_closes([10, 20, 30])
    out = slice_at_or_before(df, datetime(2025, 1, 2))
    assert len(out) == 2


def test_slice_at_or_before_empty_input():
    out = slice_at_or_before(pd.DataFrame(), datetime(2025, 1, 1, tzinfo=UTC))
    assert out.empty


# ---------------------------------------------------------------------------
# Moving averages


def test_sma_returns_none_when_too_few_bars():
    s = pd.Series([1.0, 2.0, 3.0])
    assert sma(s, 5) is None


def test_sma_matches_known_value():
    s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    assert sma(s, 5) == pytest.approx(30.0)
    assert sma(s, 3) == pytest.approx(40.0)


def test_ema_matches_pandas_implementation():
    s = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0])
    expected = float(s.ewm(span=4, adjust=False).mean().iloc[-1])
    assert ema(s, 4) == pytest.approx(expected)


def test_ma_slope_pct_positive_for_uptrend():
    # Linear up: every value 1% higher than the previous.
    s = pd.Series([100 * (1.01 ** i) for i in range(60)])
    slope = ma_slope_pct(s, ma_window=20, lookback=21)
    assert slope is not None and slope > 0


def test_ma_slope_pct_negative_for_downtrend():
    s = pd.Series([100 * (0.99 ** i) for i in range(60)])
    slope = ma_slope_pct(s, ma_window=20, lookback=21)
    assert slope is not None and slope < 0


def test_ma_slope_pct_returns_none_when_insufficient_history():
    s = pd.Series([1.0, 2.0, 3.0])
    assert ma_slope_pct(s, ma_window=20, lookback=21) is None


# ---------------------------------------------------------------------------
# ma_alignment


def test_ma_alignment_bullish_when_price_above_50_above_200():
    out = ma_alignment({"price": 100, "sma_50": 95, "sma_200": 90})
    assert out == "bullish_stack"


def test_ma_alignment_bearish_when_price_below_50_below_200():
    out = ma_alignment({"price": 80, "sma_50": 90, "sma_200": 100})
    assert out == "bearish_stack"


def test_ma_alignment_mixed_when_price_above_50_below_200():
    out = ma_alignment({"price": 95, "sma_50": 90, "sma_200": 100})
    assert out == "mixed"


def test_ma_alignment_insufficient_when_sma_missing():
    out = ma_alignment({"price": 100, "sma_50": None, "sma_200": 90})
    assert out == "insufficient"


def test_ma_alignment_ignores_intermediate_mas():
    """sma_20 and sma_150 don't gate the stack classification."""
    out = ma_alignment(
        {
            "price": 100,
            "sma_20": 99,
            "sma_50": 95,
            "sma_150": 97,  # out of order
            "sma_200": 90,
        }
    )
    assert out == "bullish_stack"


# ---------------------------------------------------------------------------
# RSI


def test_rsi_returns_none_when_too_few_bars():
    s = pd.Series([100.0, 101.0])
    assert rsi(s, 14) is None


def test_rsi_in_valid_range_for_synthetic_uptrend():
    s = pd.Series([100 + i for i in range(30)])  # monotonic up
    val = rsi(s, 14)
    assert val is not None
    assert val == pytest.approx(100.0, abs=1e-6)


def test_rsi_low_for_synthetic_downtrend():
    s = pd.Series([100 - i for i in range(30)])  # monotonic down
    val = rsi(s, 14)
    assert val is not None
    assert val < 20  # heavy selling → very low RSI


# ---------------------------------------------------------------------------
# ATR


def test_atr_returns_none_when_too_few_bars():
    h = pd.Series([1.0, 2.0])
    l = pd.Series([0.5, 1.5])
    c = pd.Series([0.7, 1.7])
    assert atr(h, l, c, 14) is None


def test_atr_matches_simple_case():
    # Constant 2.0 daily range, no gap → ATR(2) = 2.0
    closes = [100, 100, 100, 100, 100]
    highs = [101, 101, 101, 101, 101]
    lows = [99, 99, 99, 99, 99]
    a = atr(pd.Series(highs), pd.Series(lows), pd.Series(closes), n=2)
    assert a == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Realized vol


def test_realized_vol_annualized_for_known_series():
    # log returns of a known stdev ≈ 0.01 → annualized ≈ 0.01 * sqrt(252) ≈ 0.158
    np.random.seed(42)
    log_rets = np.random.normal(loc=0, scale=0.01, size=100)
    prices = 100 * np.exp(np.cumsum(log_rets))
    out = realized_vol_annualized(pd.Series(prices), n=21)
    assert out is not None
    assert 0.10 < out < 0.30  # within a sane range


# ---------------------------------------------------------------------------
# Volume ratio


def test_volume_ratio_returns_none_when_insufficient_bars():
    v = pd.Series([1000, 2000, 3000])
    assert volume_ratio(v, n=20) is None


def test_volume_ratio_calculates_correctly():
    # 19 bars of 1000, 1 last bar of 2000 → ratio ≈ 2000 / mean
    v = pd.Series([1000] * 20 + [2000])
    out = volume_ratio(v, n=20)
    assert out is not None
    assert out > 1.5


# ---------------------------------------------------------------------------
# Returns


def test_trailing_return_basic():
    s = pd.Series([100.0, 110.0])
    assert trailing_return(s, 1) == pytest.approx(0.10)


def test_trailing_return_returns_none_when_insufficient_history():
    s = pd.Series([100.0])
    assert trailing_return(s, 1) is None


# ---------------------------------------------------------------------------
# 52-week high/low


def test_high_low_52w_uses_last_252_bars():
    closes = [100 + i for i in range(252)]  # rising over 252 days
    h, l = high_low_52w(pd.Series(closes))
    assert h == pytest.approx(351.0)
    assert l == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Relative strength vs benchmark


def test_relative_strength_positive_when_outperforming():
    t = pd.Series([100 * (1.005 ** i) for i in range(80)])
    b = pd.Series([100 * (1.001 ** i) for i in range(80)])
    rs = relative_strength(t, b, 63)
    assert rs is not None and rs > 0


def test_relative_strength_returns_none_when_insufficient_overlap():
    t = pd.Series([100, 101])
    b = pd.Series([100, 100])
    assert relative_strength(t, b, 63) is None


# ---------------------------------------------------------------------------
# Composite builders — leakage discipline


def test_build_indicator_panel_respects_as_of():
    df = _df_from_closes([100 + i for i in range(300)])
    bench = _df_from_closes([100 + 0.5 * i for i in range(300)])
    cutoff = df.index[100]  # cut at the 101st bar
    panel = build_indicator_panel(df, bench, as_of=cutoff.to_pydatetime())
    # SMA_200 needs 200 bars; we only gave it 101 — should be None.
    assert panel.sma_200 is None
    # SMA_50 should be computed.
    assert panel.sma_50 is not None


def test_build_indicator_panel_handles_empty_df():
    panel = build_indicator_panel(
        pd.DataFrame(),
        pd.DataFrame(),
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert panel.sma_50 is None
    assert panel.ma_alignment == "insufficient"


def test_build_market_snapshot_returns_consistent_excess_returns():
    # Synthetic: ticker up 10% over 21 bars; benchmark up 5%. Excess = +5%.
    closes = [100 * (1.10 ** (i / 21)) for i in range(50)]
    bench_closes = [100 * (1.05 ** (i / 21)) for i in range(50)]
    df = _df_from_closes(closes)
    bench = _df_from_closes(bench_closes)
    cutoff = df.index[-1].to_pydatetime()
    panel = build_market_snapshot(df, bench, as_of=cutoff)
    assert panel.return_21d is not None
    assert panel.spy_return_21d is not None
    assert panel.excess_return_21d == pytest.approx(
        panel.return_21d - panel.spy_return_21d
    )
