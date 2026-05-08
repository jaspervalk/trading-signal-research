"""Leakage-controlled market data reader for the walk-forward harness.

A `MarketDataReader` is bound to a single `as_of` UTC timestamp. Every read
slices the underlying bar cache to `bar_time <= as_of` before returning. A
strategy that bypasses the reader to reach `get_daily_bars` directly is a
code-review hard-fail (ADR 0007 §"Bias prevention").

V1 model:
- Reads from the existing `app.market.yfinance_client` parquet cache.
- For backtest dates, the cache may already contain bars later than `as_of`
  (the cache is forward-extending). The reader filters them out.
- The reader pre-loads the requested ticker's bars on first access and
  memoizes them for the lifetime of one as_of.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd

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

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            self.as_of = self.as_of.replace(tzinfo=UTC)
        # Per-instance cache so we don't re-slice the same DataFrame repeatedly.
        self._cache = {}

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
        """Daily OHLCV for `ticker` over `[as_of − lookback_days, as_of]`.

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
        # Slice the cache itself to as_of immediately. If the underlying
        # cache contains bars later than as_of (forward-extending), they are
        # dropped here so they cannot leak via memoization.
        df = df[df.index <= _to_utc_ts(self.as_of)] if not df.empty else df
        self._cache[ticker] = df
        return self._slice(df, ld)

    def benchmark_bars(self, *, lookback_days: int | None = None) -> pd.DataFrame:
        return self.daily_bars(self.benchmark_ticker, lookback_days=lookback_days)

    def forward_bars(self, ticker: str, *, forward_days: int = 60) -> pd.DataFrame:
        """Bars with `bar_time > as_of`, used by the *simulator* to compute fills.

        Hard rule: this is HARNESS-INTERNAL. A strategy that calls this is
        looking at the future and is leaking. The simulator (in
        `walkforward._simulate_decision`) is the only correct caller — it
        is part of the backtest infrastructure, not the strategy.
        """
        try:
            df = get_daily_bars(
                ticker,
                start=self.as_of,
                end=self.as_of + timedelta(days=forward_days),
            )
        except Exception:
            return pd.DataFrame()
        if df.empty:
            return df
        return df[df.index > _to_utc_ts(self.as_of)]

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
