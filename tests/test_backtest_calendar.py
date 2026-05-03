"""Tests for the trading calendar helpers — minimal, just to confirm wiring."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.market.calendar import (
    horizon_to_trading_days,
    n_trading_days_after_close,
    next_session_open_after,
    to_market_tz,
)


def test_horizon_parsing():
    assert horizon_to_trading_days("1d") == 1
    assert horizon_to_trading_days("21d") == 21
    with pytest.raises(ValueError):
        horizon_to_trading_days("1w")


def test_to_market_tz_returns_eastern():
    dt = datetime(2026, 5, 4, 14, 30, tzinfo=UTC)
    eastern = to_market_tz(dt)
    # 14:30 UTC = 10:30 EDT
    assert eastern.hour == 10
    assert eastern.minute == 30


def test_next_session_open_after_skips_weekend():
    # Sat 2026-05-02 → next session is Mon 2026-05-04 09:30 ET = 13:30 UTC
    sat = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    nxt = next_session_open_after(sat)
    assert nxt.weekday() == 0  # Monday
    eastern = to_market_tz(nxt)
    assert (eastern.hour, eastern.minute) == (9, 30)


def test_n_trading_days_after_close_skips_weekends():
    # Mon 2026-05-04 14:00 UTC → 5 trading days later is the close on
    # the following Mon (2026-05-11) — i.e. Tue/Wed/Thu/Fri/Mon, 5 sessions strictly after.
    mon = datetime(2026, 5, 4, 14, 0, tzinfo=UTC)
    close = n_trading_days_after_close(mon, 5)
    eastern = to_market_tz(close)
    # 4pm ET regular close
    assert eastern.hour == 16
    assert eastern.weekday() == 0  # Monday
    assert eastern.day == 11
