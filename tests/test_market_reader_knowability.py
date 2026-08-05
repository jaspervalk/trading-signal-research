"""The knowability boundary: what a reader bound to `as_of` may and may not see.

Daily bars are stamped at their session date's midnight UTC, but the close
they carry only exists at ~20:00/21:00 UTC. A `bar_index <= as_of` filter
therefore hands out the current session's close for any intraday `as_of` —
which is exactly the look-ahead ADR 0003 forbids, and it bit hardest in the
walk-forward harness, whose rebalances snap to session *opens*.

These tests pin the boundary shut. They use a synthetic bar frame so they
never touch the network.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from app.backtest import market_reader as mr
from app.backtest.market_reader import MarketDataReader

# Mon 2026-05-25 is Memorial Day (NYSE closed), so this run of sessions is
# Tue 05-26 .. Fri 05-29 — it also exercises the holiday path.
SESSION_DATES = [
    "2026-05-20",
    "2026-05-21",
    "2026-05-22",
    "2026-05-26",
    "2026-05-27",
    "2026-05-28",
    "2026-05-29",
]


@pytest.fixture
def bars() -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d, tz="UTC") for d in SESSION_DATES])
    return pd.DataFrame(
        {
            "Open": range(10, 10 + len(idx)),
            "High": range(11, 11 + len(idx)),
            "Low": range(9, 9 + len(idx)),
            "Close": range(10, 10 + len(idx)),
            "Volume": [1_000_000] * len(idx),
        },
        index=idx,
    )


@pytest.fixture
def patched(monkeypatch, bars):
    """Serve the synthetic frame for any (start, end) request."""

    def fake_get_daily_bars(ticker, *, start=None, end=None):
        return bars.copy()

    monkeypatch.setattr(mr, "get_daily_bars", fake_get_daily_bars)
    return bars


def _dates(df: pd.DataFrame) -> list[str]:
    return [str(i.date()) for i in df.index]


@pytest.mark.parametrize(
    "as_of, last_visible",
    [
        # Pre-open (04:00 ET): the session has not opened, let alone closed.
        (datetime(2026, 5, 28, 8, 0, tzinfo=UTC), "2026-05-27"),
        # Mid-session (12:28 ET): today's close has not printed yet.
        (datetime(2026, 5, 28, 16, 28, tzinfo=UTC), "2026-05-27"),
        # One minute before the close still cannot see the close.
        (datetime(2026, 5, 28, 19, 59, tzinfo=UTC), "2026-05-27"),
        # After the close, today's bar is fair game.
        (datetime(2026, 5, 28, 20, 30, tzinfo=UTC), "2026-05-28"),
        # Over a weekend, Friday remains the last completed session.
        (datetime(2026, 5, 30, 12, 0, tzinfo=UTC), "2026-05-29"),
        # Memorial Day: the last completed session is the Friday before.
        (datetime(2026, 5, 25, 18, 0, tzinfo=UTC), "2026-05-22"),
    ],
)
def test_daily_bars_stop_at_last_completed_session(patched, as_of, last_visible):
    reader = MarketDataReader.at(as_of)
    visible = _dates(reader.daily_bars("FAKE"))
    assert visible[-1] == last_visible
    assert all(d <= last_visible for d in visible)


def test_intraday_as_of_does_not_leak_todays_close(patched, bars):
    """The regression this file exists for."""
    mid_session = datetime(2026, 5, 28, 16, 28, tzinfo=UTC)
    reader = MarketDataReader.at(mid_session)

    todays_close = float(bars.loc[pd.Timestamp("2026-05-28", tz="UTC"), "Close"])
    seen = reader.daily_bars("FAKE")["Close"].tolist()

    assert todays_close not in seen


def test_forward_bars_start_where_daily_bars_stop(patched):
    """Exact complements: no bar visible to both, no bar lost between them."""
    for as_of in (
        datetime(2026, 5, 28, 8, 0, tzinfo=UTC),
        datetime(2026, 5, 28, 16, 28, tzinfo=UTC),
        datetime(2026, 5, 28, 20, 30, tzinfo=UTC),
    ):
        reader = MarketDataReader.at(as_of)
        history = set(_dates(reader.daily_bars("FAKE")))
        forward = set(_dates(reader.forward_bars("FAKE", forward_days=30)))

        assert not (history & forward), f"overlap at {as_of}"
        assert history | forward == set(SESSION_DATES), f"gap at {as_of}"


def test_intraday_decision_fills_against_the_current_session(patched):
    """Decide on yesterday's close, fill today — not tomorrow."""
    reader = MarketDataReader.at(datetime(2026, 5, 28, 13, 30, tzinfo=UTC))
    assert _dates(reader.forward_bars("FAKE", forward_days=30))[0] == "2026-05-28"


def test_knowable_through_is_exposed_for_horizon_anchoring(patched):
    reader = MarketDataReader.at(datetime(2026, 5, 28, 8, 0, tzinfo=UTC))
    assert reader.knowable_through == pd.Timestamp("2026-05-27", tz="UTC")


def test_memoized_frame_is_not_reused_across_the_boundary(patched):
    """The cache is per-reader; two readers at different as_ofs must not
    share a slice. Guards the memoization path in `daily_bars`.
    """
    early = MarketDataReader.at(datetime(2026, 5, 27, 8, 0, tzinfo=UTC))
    late = MarketDataReader.at(datetime(2026, 5, 29, 8, 0, tzinfo=UTC))

    early.daily_bars("FAKE")  # populate
    assert _dates(early.daily_bars("FAKE"))[-1] == "2026-05-26"
    assert _dates(late.daily_bars("FAKE"))[-1] == "2026-05-28"
