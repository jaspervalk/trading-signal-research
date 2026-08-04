"""Screener pipeline — fan out fetches, apply filters, return a ScreenResult.

Concurrency: `ThreadPoolExecutor` with a configurable worker count plus a
shared rate-limiting semaphore that paces yfinance hits to ~2 req/s by
default. The pipeline tolerates per-ticker fetch failures — a failed
ticker becomes an error row, never an exception.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.screener import cache, fetcher
from app.screener.creator_coverage import coverage_for
from app.screener.filters import build_filters, compute_flags
from app.screener.schema import (
    FilterResult,
    ScreenConfig,
    ScreenResult,
    ScreenRow,
    TickerMetrics,
)
from app.screener.universe import UniverseEntry

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Simple thread-safe rate limiter — releases one slot every `interval` seconds.


class RateLimiter:
    def __init__(self, requests_per_second: float = 2.0):
        self.interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_at = now + self.interval


# ---------------------------------------------------------------------------
# Public API


def run_screen(
    universe: list[UniverseEntry],
    *,
    config: ScreenConfig | None = None,
    session: Session | None = None,
    max_workers: int = 8,
    requests_per_second: float = 2.0,
    use_cache: bool = True,
    fetch_fn: Callable[[str], TickerMetrics] | None = None,
) -> ScreenResult:
    """Run the screener across `universe`.

    `fetch_fn` is injectable for tests — production callers leave it None
    and `fetcher.fetch_metrics` is used.

    `session` is optional. If provided, creator-coverage columns are
    populated from the database; if None, every ticker shows zero
    mentions (and the spec requires this to never be a penalty).
    """
    config = config or ScreenConfig()
    if config.sector:
        universe = [e for e in universe if e.sector.lower() == config.sector.lower()]

    rate = RateLimiter(requests_per_second)
    filters = build_filters(config)

    def _fetch_one(entry: UniverseEntry) -> tuple[UniverseEntry, TickerMetrics | None, str | None]:
        try:
            rate.acquire()
            if fetch_fn is not None:
                m = fetch_fn(entry.ticker)
            else:
                m = fetcher.fetch_metrics(entry.ticker, use_cache=use_cache)
            # Backfill sector/industry from universe CSV when yfinance omits it.
            if not m.sector and entry.sector:
                m.sector = entry.sector
            return entry, m, None
        except Exception as e:  # pragma: no cover — defensive surface
            log.warning("screener.fetch.error", ticker=entry.ticker, error=str(e))
            return entry, None, str(e)

    fetched: list[tuple[UniverseEntry, TickerMetrics | None, str | None]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_fetch_one, e) for e in universe]
        for fut in as_completed(futures):
            fetched.append(fut.result())

    # Creator coverage in one DB roundtrip (additive only).
    coverage: dict[str, tuple[int, float | None]] = {}
    if session is not None:
        coverage = coverage_for([e.ticker for e in universe], session=session)

    rows: list[ScreenRow] = []
    for entry, m, err in fetched:
        if err is not None or m is None:
            rows.append(
                ScreenRow(
                    metrics=TickerMetrics(ticker=entry.ticker, sector=entry.sector or None),
                    filters=[],
                    error=err or "no_metrics",
                )
            )
            continue
        n_mentions, avg_conf = coverage.get(entry.ticker, (0, None))
        m.creator_mentions = n_mentions
        m.avg_creator_confidence = avg_conf
        filter_results: list[FilterResult] = [f.evaluate(m) for f in filters]
        flags = compute_flags(m, config)
        rows.append(ScreenRow(metrics=m, filters=filter_results, flags=flags))

    # Order: tickers that passed at least one filter first (alpha within),
    # then the rest, errors last. **Critical** invariant: creator coverage
    # does NOT influence ordering at any point.
    def _sort_key(r: ScreenRow) -> tuple:
        return (
            1 if r.error else 0,
            0 if r.filters_passed else 1,
            -len(r.filters_passed),
            r.ticker,
        )

    rows.sort(key=_sort_key)

    return ScreenResult(
        as_of=datetime.now(tz=UTC),
        config=config,
        universe_size=len(universe),
        n_completed=sum(1 for r in rows if r.error is None),
        n_errors=sum(1 for r in rows if r.error is not None),
        rows=rows,
    )


def warm_cache_only(tickers: list[str], *, requests_per_second: float = 2.0) -> int:
    """Force-refresh the cache for `tickers`. Useful for nightly cron."""
    rate = RateLimiter(requests_per_second)
    n = 0
    for t in tickers:
        rate.acquire()
        try:
            fetcher.fetch_metrics(t, use_cache=False)
            n += 1
        except Exception as e:  # pragma: no cover
            log.warning("screener.warm.error", ticker=t, error=str(e))
    return n


# Re-export for callers
clear_cache = cache.clear


__all__ = ["RateLimiter", "clear_cache", "run_screen", "warm_cache_only"]
