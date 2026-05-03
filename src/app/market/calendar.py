"""Trading-calendar helpers for the XNYS exchange.

Wraps `pandas_market_calendars`. All inputs/outputs are tz-aware. The "market
clock" is `America/New_York` — but anything we hand to the rest of the
codebase comes back as UTC to keep the storage convention consistent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal

from app.config import load_project_settings

MARKET_TZ = ZoneInfo("America/New_York")


@lru_cache(maxsize=1)
def _calendar():  # type: ignore[no-untyped-def]
    name = load_project_settings().backtest.market_calendar
    return mcal.get_calendar(name)


def to_market_tz(dt_utc: datetime) -> datetime:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    return dt_utc.astimezone(MARKET_TZ)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _schedule(start: datetime, end: datetime) -> pd.DataFrame:
    cal = _calendar()
    s = cal.schedule(
        start_date=(start - timedelta(days=1)).date().isoformat(),
        end_date=(end + timedelta(days=1)).date().isoformat(),
    )
    if s.empty:
        return s
    if s["market_open"].dt.tz is None:
        s["market_open"] = s["market_open"].dt.tz_localize(UTC)
    if s["market_close"].dt.tz is None:
        s["market_close"] = s["market_close"].dt.tz_localize(UTC)
    return s


def next_session_open_after(dt_utc: datetime) -> datetime:
    """First regular-session market_open >= dt_utc, returned in UTC.

    If `dt_utc` is during regular trading hours of a session, that session's
    open IS in the past — we return the NEXT session's open.
    """
    dt_utc = to_utc(dt_utc)
    # Look ~30 days forward to safely cover holiday weekends.
    sched = _schedule(dt_utc, dt_utc + timedelta(days=30))
    after = sched[sched["market_open"] > dt_utc]
    if after.empty:
        raise ValueError(f"no trading session found after {dt_utc!r}")
    return after["market_open"].iloc[0].to_pydatetime()


def sessions_after(dt_utc: datetime, n: int) -> list[pd.Timestamp]:
    """Return up to n session-open timestamps strictly after dt_utc.

    For backtest horizons we typically want SESSION DATES (not opens) so we
    can step trading days correctly.
    """
    dt_utc = to_utc(dt_utc)
    if n <= 0:
        return []
    # Need enough lookahead to cover n sessions (252 trading days/year ≈ 1.4 cal days/session).
    sched = _schedule(dt_utc, dt_utc + timedelta(days=int(n * 1.6) + 14))
    after = sched[sched["market_open"] > dt_utc].head(n)
    return list(after["market_open"])


def n_trading_days_after_close(activation_at_utc: datetime, n: int) -> datetime:
    """Return the close-of-day for the Nth trading session strictly after
    `activation_at_utc`. Used to define horizon windows.

    Convention: "5d horizon" means the close 5 trading days after activation,
    NOT including the activation day itself.
    """
    activation_at_utc = to_utc(activation_at_utc)
    sched = _schedule(activation_at_utc, activation_at_utc + timedelta(days=int(n * 1.6) + 14))
    # Sessions strictly after activation_at — meaning sessions that opened
    # AFTER the activation moment.
    after = sched[sched["market_open"] > activation_at_utc]
    if len(after) < n:
        raise ValueError(
            f"only {len(after)} sessions available after {activation_at_utc!r}, need {n}"
        )
    return after["market_close"].iloc[n - 1].to_pydatetime()


def horizon_to_trading_days(horizon: str) -> int:
    """Map '1d'|'3d'|'5d'|'21d' → integer trading days."""
    if not horizon.endswith("d"):
        raise ValueError(f"unsupported horizon format: {horizon!r}")
    return int(horizon[:-1])
