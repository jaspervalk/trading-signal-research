"""Unit tests for src/app/analysis/levels.py.

Tests use synthetic OHLC frames with planted swing pivots.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from app.analysis.levels import (
    breakout_distance_pct,
    build_levels_panel,
    consolidation_range_pct,
    find_swing_highs,
    find_swing_lows,
    nearest_above,
    nearest_below,
    pullback_pct_from_recent_high,
)


def _frame(highs: list[float], lows: list[float], closes: list[float] | None = None) -> pd.DataFrame:
    n = len(highs)
    closes = closes or [(h + l) / 2 for h, l in zip(highs, lows)]
    idx = pd.date_range(start="2025-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [1_000_000] * n,
        },
        index=idx,
    )


def test_find_swing_highs_picks_central_peak():
    # Plant a clean peak at index 5 inside a 11-bar window.
    highs = [10, 11, 12, 13, 14, 20, 14, 13, 12, 11, 10]
    df = _frame(highs, [h - 2 for h in highs])
    swings = find_swing_highs(df["High"], window=5)
    assert len(swings) == 1
    assert swings[0][1] == pytest.approx(20.0)


def test_find_swing_lows_picks_central_trough():
    lows = [10, 9, 8, 7, 6, 1, 6, 7, 8, 9, 10]
    df = _frame([l + 2 for l in lows], lows)
    swings = find_swing_lows(df["Low"], window=5)
    assert len(swings) == 1
    assert swings[0][1] == pytest.approx(1.0)


def test_find_swing_highs_ignores_edges():
    """The first and last `window` bars are excluded — we can't confirm pivots there."""
    highs = [50, 49, 48, 47, 46]
    df = _frame(highs, [h - 1 for h in highs])
    swings = find_swing_highs(df["High"], window=2)
    # All bars are at edges; nothing returned.
    assert swings == []


def test_nearest_above_below_helpers():
    candidates = [10, 20, 30, 40]
    assert nearest_above(25, candidates) == 30
    assert nearest_below(25, candidates) == 20
    assert nearest_above(50, candidates) is None
    assert nearest_below(5, candidates) is None


def test_pullback_pct_positive_when_below_recent_high():
    closes = [100] * 50 + [120] + [108]  # 120 is the recent high; current at 108
    df = _frame([c + 1 for c in closes], [c - 1 for c in closes], closes)
    pb = pullback_pct_from_recent_high(df["Close"], lookback=52)
    assert pb is not None
    assert pb == pytest.approx((120 - 108) / 120, rel=1e-6)


def test_breakout_distance_pct_positive_when_above_recent_high():
    # Excludes the last bar from the lookback window, so a fresh high reads positive.
    closes = [100] * 60 + [115] + [120]
    df = _frame([c + 1 for c in closes], [c - 1 for c in closes], closes)
    bd = breakout_distance_pct(df["Close"], lookback=60)
    assert bd is not None
    assert bd > 0  # 120 is above the prior 60-bar window's max (115)


def test_breakout_distance_pct_negative_when_below_recent_high():
    closes = [100, 110, 108, 105, 103]
    df = _frame([c + 1 for c in closes], [c - 1 for c in closes], closes)
    bd = breakout_distance_pct(df["Close"], lookback=4)
    assert bd is not None
    assert bd < 0


def test_consolidation_range_pct_low_for_tight_range():
    closes = [100.0] * 25
    highs = [101.0] * 25
    lows = [99.0] * 25
    df = _frame(highs, lows, closes)
    cr = consolidation_range_pct(df["High"], df["Low"], n=20)
    assert cr is not None
    assert cr == pytest.approx(2.0 / 100.0, rel=1e-6)  # (101 - 99) / midprice 100


def test_build_levels_panel_finds_support_and_resistance():
    rng = np.random.default_rng(0)
    n = 200
    base = 100 + np.cumsum(rng.normal(0, 0.5, n))
    # Plant a known swing high and swing low using the simple central-peak trick.
    base[60] = float(base[60]) + 30  # high
    base[120] = float(base[120]) - 30  # low
    closes = list(base)
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    highs[60] += 5
    lows[120] -= 5

    df = _frame(highs, lows, closes)
    panel = build_levels_panel(df, as_of=df.index[-1].to_pydatetime())

    # Both lists should contain something — the planted pivots, plus any others.
    assert panel.swing_highs
    assert panel.swing_lows
    assert panel.consolidation_range_pct is not None


def test_build_levels_panel_empty_input_returns_empty_panel():
    panel = build_levels_panel(pd.DataFrame(), as_of=datetime(2025, 1, 1, tzinfo=UTC))
    assert panel.swing_highs == []
    assert panel.nearest_resistance is None
    assert panel.nearest_support is None
