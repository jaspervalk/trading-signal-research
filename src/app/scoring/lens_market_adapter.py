"""Production MarketProvider adapter — wraps the existing leakage-controlled
`MarketDataReader` so lens-outcome scoring uses the same price plumbing as
per-call backtests (yfinance parquet cache + XNYS calendar, ADR 0003 + 0007).

API mapping
-----------
The codebase's `MarketDataReader` (src/app/backtest/market_reader.py) is
bound to a single `as_of` and exposes:
- `daily_bars(ticker)`            → history up to & including `as_of`
- `forward_bars(ticker, ...)`     → bars strictly after `as_of`

It does NOT expose a "return over N trading days" helper directly, so this
adapter computes returns the way `compute_outcome` does:

1. Take the entry close = last close at or before `as_of` from `daily_bars`.
2. Step N trading days forward via `n_trading_days_after_close(as_of, N)` to
   find the horizon-end close timestamp.
3. Pick the close at (or before) that timestamp from the combined
   history+forward bar series.

`spy_return` reuses the same path with the benchmark ticker. `regime_for`
classifies SPY's 21-trading-day return at `as_of` per ADR 0007:
  > +2% → up, < -2% → down, else flat.

Each call constructs a fresh `MarketDataReader` keyed on `as_of`. The
underlying yfinance client memoises bars in an on-disk parquet cache, so
repeated lookups for the same ticker over consecutive snapshots are cheap.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from app.backtest.market_reader import MarketDataReader
from app.market.calendar import (
    horizon_to_trading_days,
    last_completed_session_close,
    n_trading_days_after_close,
)
from app.scoring.lens_outcomes import HORIZON_DAYS


def _to_utc_ts(dt: datetime) -> pd.Timestamp:
    if dt.tzinfo is None:
        return pd.Timestamp(dt).tz_localize("UTC")
    return pd.Timestamp(dt).tz_convert("UTC")


class CachedMarketAdapter:
    """Production `MarketProvider` for `compute_lens_outcomes`.

    Constructed with no args; each lookup builds a `MarketDataReader.at(as_of)`
    on demand. The yfinance client's parquet cache makes this cheap — repeated
    `(ticker, as_of)` lookups hit disk, not the network.
    """

    def __init__(self, *, history_days: int = 540, benchmark_ticker: str = "SPY"):
        self._history_days = history_days
        self._benchmark_ticker = benchmark_ticker

    # ------------------------------------------------------------------ #
    # MarketProvider Protocol

    def ticker_return(
        self, ticker: str, as_of: datetime, horizon: str
    ) -> float | None:
        return self._compute_return(ticker, as_of, horizon)

    def spy_return(self, as_of: datetime, horizon: str) -> float | None:
        return self._compute_return(self._benchmark_ticker, as_of, horizon)

    def regime_for(self, as_of: datetime) -> str:
        ret_21d = self._compute_return(self._benchmark_ticker, as_of, "21d")
        if ret_21d is None:
            return "flat"
        if ret_21d > 0.02:
            return "up"
        if ret_21d < -0.02:
            return "down"
        return "flat"

    # ------------------------------------------------------------------ #
    # Internals

    def _compute_return(
        self, ticker: str, as_of: datetime, horizon: str
    ) -> float | None:
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)

        n_days = horizon_to_trading_days(horizon)

        # Anchor on the last completed session, not on `as_of`. A lens that
        # ran at 08:00 UTC (pre-open) knew yesterday's close and nothing more,
        # so its "1 day later" is today's close — measured from yesterday's.
        # Anchoring on `as_of` instead both leaked the current session's close
        # into the entry price and silently dropped the 1d horizon entirely
        # for every pre-open snapshot.
        anchor_close = last_completed_session_close(as_of)
        if anchor_close is None:
            return None

        try:
            horizon_end = n_trading_days_after_close(anchor_close, n_days)
        except ValueError:
            return None

        # If horizon hasn't elapsed yet, no realised return.
        if horizon_end > datetime.now(tz=timezone.utc):
            return None

        reader = MarketDataReader.at(as_of, history_days=self._history_days)

        history = reader.daily_bars(ticker)
        if history.empty:
            return None
        # Entry close = last close at/before as_of.
        entry_close = float(history["Close"].iloc[-1])
        if entry_close == 0.0:
            return None

        # Forward window: stretch a bit past horizon_end to be safe (holidays).
        forward = reader.forward_bars(
            ticker, forward_days=int(HORIZON_DAYS[horizon] * 1.6) + 14
        )
        if forward.empty:
            return None

        end_ts = _to_utc_ts(horizon_end)
        on_or_before = forward[forward.index <= end_ts]
        if on_or_before.empty:
            return None
        exit_close = float(on_or_before["Close"].iloc[-1])

        return (exit_close - entry_close) / entry_close


__all__ = ["CachedMarketAdapter"]
