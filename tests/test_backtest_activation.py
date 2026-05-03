"""Tests for activation logic with synthetic price data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.backtest.activation import determine_activation
from app.models import (
    DIRECTION_LONG,
    DIRECTION_SHORT,
    ENTRY_LIMIT,
    ENTRY_MARKET,
    ENTRY_TRIGGER_ABOVE,
    ENTRY_TRIGGER_BELOW,
    ENTRY_UNSPECIFIED,
    ExtractedCall,
)


def _bars(start_dt: datetime, ohlc: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Build a tz-aware UTC daily DataFrame from a list of (open, high, low, close)."""
    base = start_dt.replace(tzinfo=None) if start_dt.tzinfo else start_dt
    idx = pd.DatetimeIndex([base + timedelta(days=i) for i in range(len(ohlc))], name="ts").tz_localize("UTC")
    return pd.DataFrame(
        ohlc, index=idx,
        columns=["Open", "High", "Low", "Close"],
    )


def _call(**kw) -> ExtractedCall:
    return ExtractedCall(
        ticker=kw.get("ticker", "NVDA"),
        direction=kw.get("direction", DIRECTION_LONG),
        entry_type=kw.get("entry_type", ENTRY_MARKET),
        entry_price=kw.get("entry_price"),
        target_price=kw.get("target_price"),
        stop_price=kw.get("stop_price"),
        timeframe=kw.get("timeframe", "swing"),
    )


def test_market_entry_uses_next_open():
    bars = _bars(
        datetime(2026, 5, 4, tzinfo=UTC),
        [(100, 102, 99, 101), (102, 104, 101, 103)],
    )
    res = determine_activation(_call(entry_type=ENTRY_MARKET), bars)
    assert res.activated is True
    assert res.fill_price == 100.0


def test_unspecified_treated_as_market():
    bars = _bars(datetime(2026, 5, 4, tzinfo=UTC), [(50, 51, 49, 50.5)])
    res = determine_activation(_call(entry_type=ENTRY_UNSPECIFIED), bars)
    assert res.activated is True
    assert res.fill_price == 50.0


def test_trigger_above_long_activates_when_high_crosses():
    # Day1: high 99 (below trigger 100). Day2: high 105 → activates.
    bars = _bars(
        datetime(2026, 5, 4, tzinfo=UTC),
        [(95, 99, 94, 98), (100, 105, 99, 104)],
    )
    res = determine_activation(
        _call(entry_type=ENTRY_TRIGGER_ABOVE, entry_price=100.0), bars
    )
    assert res.activated is True
    # Open == 100 == trigger → fill = max(open, trigger) = 100
    assert res.fill_price == 100.0


def test_trigger_above_long_gap_open_above_trigger():
    """If open gaps above trigger, fill at the open (worse than trigger)."""
    bars = _bars(
        datetime(2026, 5, 4, tzinfo=UTC),
        [(110, 112, 108, 111)],  # opens 110, trigger 100
    )
    res = determine_activation(
        _call(entry_type=ENTRY_TRIGGER_ABOVE, entry_price=100.0), bars
    )
    assert res.activated is True
    assert res.fill_price == 110.0


def test_trigger_above_never_hits_returns_inactive():
    bars = _bars(
        datetime(2026, 5, 4, tzinfo=UTC),
        [(95, 98, 94, 96), (96, 99, 95, 97), (97, 99.5, 96, 98)],
    )
    res = determine_activation(
        _call(entry_type=ENTRY_TRIGGER_ABOVE, entry_price=100.0), bars
    )
    assert res.activated is False


def test_trigger_below_short_activates_when_low_crosses():
    bars = _bars(
        datetime(2026, 5, 4, tzinfo=UTC),
        [(101, 102, 99.5, 100), (98, 99, 95, 96)],  # day2 low 95 < trigger 100
    )
    res = determine_activation(
        _call(direction=DIRECTION_SHORT, entry_type=ENTRY_TRIGGER_BELOW, entry_price=100.0),
        bars,
    )
    assert res.activated is True
    # Day1 also has low < 100 → activates on day1 first.
    assert res.fill_price == min(101.0, 100.0)  # = 100.0


def test_limit_long_activates_when_low_touches():
    # Limit long at 95. Day1 low is 96 (no fill), Day2 low is 94 → fills.
    bars = _bars(
        datetime(2026, 5, 4, tzinfo=UTC),
        [(100, 102, 96, 101), (101, 102, 94, 99)],
    )
    res = determine_activation(
        _call(entry_type=ENTRY_LIMIT, entry_price=95.0), bars
    )
    assert res.activated is True
    # Fill at the limit price (Day2 opens at 101 > 95, so fill = 95).
    assert res.fill_price == 95.0


def test_limit_requires_entry_price():
    bars = _bars(datetime(2026, 5, 4, tzinfo=UTC), [(100, 101, 99, 100)])
    res = determine_activation(_call(entry_type=ENTRY_LIMIT, entry_price=None), bars)
    assert res.activated is False


def test_no_bars_returns_inactive():
    bars = pd.DataFrame(columns=["Open", "High", "Low", "Close"])
    res = determine_activation(_call(), bars)
    assert res.activated is False


def test_window_caps_at_trigger_window_days():
    # Trigger only fires on day 12 — outside default 10-day window.
    bars = _bars(
        datetime(2026, 5, 4, tzinfo=UTC),
        [(95, 96, 94, 95)] * 11 + [(100, 105, 99, 104)],
    )
    res = determine_activation(
        _call(entry_type=ENTRY_TRIGGER_ABOVE, entry_price=100.0),
        bars,
        trigger_window_days=10,
    )
    assert res.activated is False
