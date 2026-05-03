"""Tests for outcome metric computation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from app.backtest.outcomes import compute_outcome
from app.models import (
    DIRECTION_LONG,
    DIRECTION_SHORT,
    ENTRY_MARKET,
    ExtractedCall,
)


def _bars(start_dt: datetime, ohlc: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    base = start_dt.replace(tzinfo=None) if start_dt.tzinfo else start_dt
    idx = pd.DatetimeIndex([base + timedelta(days=i) for i in range(len(ohlc))], name="ts").tz_localize("UTC")
    return pd.DataFrame(
        ohlc, index=idx,
        columns=["Open", "High", "Low", "Close"],
    )


def _call(**kw) -> ExtractedCall:
    return ExtractedCall(
        ticker="NVDA",
        direction=kw.get("direction", DIRECTION_LONG),
        entry_type=ENTRY_MARKET,
        entry_price=kw.get("entry_price"),
        target_price=kw.get("target_price"),
        stop_price=kw.get("stop_price"),
        timeframe="swing",
    )


def test_long_positive_return():
    start = datetime(2026, 5, 4, tzinfo=UTC)
    bars = _bars(start, [(100, 102, 98, 101), (102, 105, 100, 104), (104, 108, 102, 107)])
    bench = _bars(start, [(500, 501, 499, 500), (500, 502, 499, 501), (501, 503, 500, 502)])
    res = compute_outcome(
        _call(direction=DIRECTION_LONG),
        fill_price=100.0,
        activation_at=start,
        daily_bars=bars,
        benchmark_bars=bench,
        horizon_end_at=start + timedelta(days=2),
        cost_bps=10,
    )
    assert res.exit_price == 107.0
    assert res.gross_return_pct == pytest.approx(0.07)
    assert res.return_pct == pytest.approx(0.07 - 0.001)
    # MFE = (108 - 100) / 100 = 0.08; MAE = (98 - 100) / 100 = -0.02 (signed)
    assert res.mfe == pytest.approx(0.08)
    assert res.mae == pytest.approx(-0.02)
    # SPY: 502/500 - 1 = 0.004
    assert res.benchmark_return_pct == pytest.approx(0.004)
    assert res.excess_return_pct == pytest.approx(res.return_pct - res.benchmark_return_pct)


def test_short_positive_return_when_price_drops():
    start = datetime(2026, 5, 4, tzinfo=UTC)
    bars = _bars(start, [(100, 102, 95, 96), (96, 97, 90, 91)])
    bench = _bars(start, [(500, 501, 499, 500), (500, 501, 499, 500)])
    res = compute_outcome(
        _call(direction=DIRECTION_SHORT),
        fill_price=100.0,
        activation_at=start,
        daily_bars=bars,
        benchmark_bars=bench,
        horizon_end_at=start + timedelta(days=1),
        cost_bps=10,
    )
    # Long-equivalent return = (91-100)/100 = -0.09; short flips sign → +0.09 gross
    assert res.gross_return_pct == pytest.approx(0.09)
    # MFE for short = lowest low - fill, sign-flipped → (90 - 100) / 100 → flipped = +0.10
    assert res.mfe == pytest.approx(0.10)
    # MAE for short = highest high - fill, sign-flipped → (102 - 100) / 100 → flipped = -0.02
    assert res.mae == pytest.approx(-0.02)


def test_hit_target_long():
    start = datetime(2026, 5, 4, tzinfo=UTC)
    bars = _bars(start, [(100, 105, 98, 104), (104, 112, 103, 110)])
    res = compute_outcome(
        _call(direction=DIRECTION_LONG, target_price=110.0, stop_price=95.0),
        fill_price=100.0,
        activation_at=start,
        daily_bars=bars,
        benchmark_bars=pd.DataFrame(),
        horizon_end_at=start + timedelta(days=1),
        cost_bps=0,
    )
    assert res.hit_target is True
    assert res.hit_stop is False


def test_hit_stop_long():
    start = datetime(2026, 5, 4, tzinfo=UTC)
    bars = _bars(start, [(100, 102, 94, 96)])
    res = compute_outcome(
        _call(direction=DIRECTION_LONG, target_price=110.0, stop_price=95.0),
        fill_price=100.0,
        activation_at=start,
        daily_bars=bars,
        benchmark_bars=pd.DataFrame(),
        horizon_end_at=start,
        cost_bps=0,
    )
    assert res.hit_stop is True
    assert res.hit_target is False


def test_no_target_no_stop_returns_none_for_those_fields():
    start = datetime(2026, 5, 4, tzinfo=UTC)
    bars = _bars(start, [(100, 102, 99, 101)])
    res = compute_outcome(
        _call(direction=DIRECTION_LONG),
        fill_price=100.0,
        activation_at=start,
        daily_bars=bars,
        benchmark_bars=pd.DataFrame(),
        horizon_end_at=start,
        cost_bps=0,
    )
    assert res.hit_target is None
    assert res.hit_stop is None


def test_empty_window_returns_nones():
    start = datetime(2026, 5, 4, tzinfo=UTC)
    bars = _bars(start, [(100, 102, 99, 101)])
    res = compute_outcome(
        _call(),
        fill_price=100.0,
        activation_at=start + timedelta(days=10),  # window past available bars
        daily_bars=bars,
        benchmark_bars=pd.DataFrame(),
        horizon_end_at=start + timedelta(days=15),
        cost_bps=10,
    )
    assert res.exit_price is None
    assert res.return_pct is None


# Local pytest import (the assertions above use approx())
import pytest  # noqa: E402
