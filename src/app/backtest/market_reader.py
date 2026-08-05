"""Leakage-controlled market data reader for the walk-forward harness.

A `MarketDataReader` is bound to a single `as_of` UTC timestamp. Every read
slices the underlying bar cache to what was *knowable* at `as_of` before
returning. A strategy that bypasses the reader to reach `get_daily_bars`
directly is a code-review hard-fail (ADR 0007 §"Bias prevention").

The knowability boundary is the last NYSE session close at or before `as_of`
— NOT `as_of` itself. Daily bars are stamped at their session date's
midnight, so a `bar_index <= as_of` filter admits the current session's bar
(and therefore its close, high and low) for any intraday `as_of`. Because
walk-forward rebalances snap to session *opens*, that naive filter leaked
the whole of the decision day into every decision. `_knowable_through`
anchors both reads on the last completed session instead.

V1 model:
- Reads from the existing `app.market.yfinance_client` parquet cache.
- For backtest dates, the cache may already contain bars later than `as_of`
  (the cache is forward-extending). The reader filters them out.
- The reader pre-loads the requested ticker's bars on first access and
  memoizes them for the lifetime of one as_of.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pandas as pd

from app.market.calendar import last_completed_session_close
from app.market.yfinance_client import get_daily_bars


def _to_utc_ts(dt: datetime) -> pd.Timestamp:
    if dt.tzinfo is None:
        return pd.Timestamp(dt).tz_localize("UTC")
    return pd.Timestamp(dt).tz_convert("UTC")


@dataclass
class MarketDataReader:
    """Bind once per as_of; reuse for all reads at that timestamp.

    Construct via `MarketDataReader.at(as_of)`. The harness builds one of
    these per rebalance datetime and passes it into `StrategyContext`.

    Args:
        as_of: cutoff timestamp; every read is sliced to bars on/before this.
        history_days: how much history to fetch from yfinance per ticker.
            Default 540 (~2y) covers SMA-200 + 252-day returns + RS-126.
        benchmark_ticker: defaults to SPY; used for `benchmark_bars`.
    """

    as_of: datetime
    history_days: int = 540
    benchmark_ticker: str = "SPY"
    _cache: dict[str, pd.DataFrame] = None  # type: ignore[assignment]
    _knowable_through: pd.Timestamp = field(init=False, repr=False, default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            self.as_of = self.as_of.replace(tzinfo=UTC)
        # Per-instance cache so we don't re-slice the same DataFrame repeatedly.
        self._cache = {}
        self._knowable_through = self._compute_knowable_through()

    def _compute_knowable_through(self) -> pd.Timestamp:
        """Session date (UTC-midnight-normalised) of the last close at/before
        `as_of`. Bars stamped after this were not knowable at `as_of`.

        Falls back to `as_of` itself if the calendar can't answer — that is
        the old, leakier behaviour, but it only triggers when the calendar is
        unavailable, and failing open here beats returning no bars at all.
        """
        try:
            close = last_completed_session_close(self.as_of)
        except Exception:
            close = None
        if close is None:
            return _to_utc_ts(self.as_of).normalize()
        return _to_utc_ts(close).normalize()

    @property
    def knowable_through(self) -> pd.Timestamp:
        """The last session date whose bar is visible at `as_of`. Exposed so
        callers can anchor horizon math on the same boundary the reads use.
        """
        return self._knowable_through

    @classmethod
    def at(
        cls,
        as_of: datetime,
        *,
        history_days: int = 540,
        benchmark_ticker: str = "SPY",
    ) -> "MarketDataReader":
        return cls(
            as_of=as_of, history_days=history_days, benchmark_ticker=benchmark_ticker
        )

    # ---------------------------------------------------------------------
    # Reads

    def daily_bars(self, ticker: str, *, lookback_days: int | None = None) -> pd.DataFrame:
        """Daily OHLCV for `ticker` over `[as_of − lookback_days, knowable_through]`.

        The upper bound is the last *completed* session, not `as_of`. At an
        intraday `as_of` the current session's bar exists in the cache but its
        close has not happened yet; including it is look-ahead (ADR 0003).

        Returns an empty frame if the ticker isn't tradeable in the window
        (yfinance returned nothing — usually means the symbol was delisted or
        not yet listed at `as_of`).
        """
        ld = lookback_days or self.history_days
        if ticker in self._cache:
            return self._slice(self._cache[ticker], ld)

        start = self.as_of - timedelta(days=self.history_days)
        try:
            df = get_daily_bars(ticker, start=start, end=self.as_of)
        except Exception:
            df = pd.DataFrame()
        # Slice the cache itself immediately. If the underlying cache contains
        # bars beyond the knowability boundary (it is forward-extending), they
        # are dropped here so they cannot leak via memoization.
        df = df[df.index.normalize() <= self._knowable_through] if not df.empty else df
        self._cache[ticker] = df
        return self._slice(df, ld)

    def benchmark_bars(self, *, lookback_days: int | None = None) -> pd.DataFrame:
        return self.daily_bars(self.benchmark_ticker, lookback_days=lookback_days)

    def forward_bars(self, ticker: str, *, forward_days: int = 60) -> pd.DataFrame:
        """Bars after the knowability boundary, used by the *simulator* to
        compute fills.

        Mirrors `daily_bars`: everything `daily_bars` can see ends at
        `knowable_through`, so the forward window starts at the very next
        session. The two are exact complements — no bar is visible to both,
        and no bar falls in the gap between them. At an intraday `as_of` the
        first forward bar is the *current* session, which is the correct
        thing to fill against: decide on yesterday's close, fill today.

        Hard rule: this is HARNESS-INTERNAL. A strategy that calls this is
        looking at the future and is leaking. The simulator (in
        `walkforward._simulate_decision`) is the only correct caller — it
        is part of the backtest infrastructure, not the strategy.
        """
        try:
            df = get_daily_bars(
                ticker,
                start=self._knowable_through.to_pydatetime(),
                end=self.as_of + timedelta(days=forward_days),
            )
        except Exception:
            return pd.DataFrame()
        if df.empty:
            return df
        return df[df.index.normalize() > self._knowable_through]

    def forward_benchmark_bars(self, *, forward_days: int = 60) -> pd.DataFrame:
        return self.forward_bars(self.benchmark_ticker, forward_days=forward_days)

    def is_tradeable(self, ticker: str, *, min_volume: int = 1_000) -> bool:
        """A ticker is tradeable at `as_of` if we have a recent bar with
        non-zero volume. Used to filter the universe per ADR 0007's
        survivorship rule.

        We require at least one bar within the last 14 days with volume
        ≥ `min_volume`. Tickers delisted before `as_of` won't have recent
        bars; tickers added after `as_of` won't appear in our slice.
        """
        df = self.daily_bars(ticker, lookback_days=14)
        if df.empty or "Volume" not in df:
            return False
        return bool((df["Volume"].fillna(0) >= min_volume).any())

    # ---------------------------------------------------------------------
    # Helpers

    def _slice(self, df: pd.DataFrame, lookback_days: int) -> pd.DataFrame:
        if df.empty:
            return df
        lo = _to_utc_ts(self.as_of - timedelta(days=lookback_days))
        return df[df.index >= lo]


__all__ = ["MarketDataReader"]
