"""A small time-to-live cache decorator.

`functools.lru_cache` never expires, which is wrong for market metadata:
a forward P/E derived from a live price goes stale within minutes, and an
unbounded cache in a long-lived FastAPI worker will serve the same blob
forever. This decorator adds expiry plus one thing `lru_cache` cannot give
us — `peek_fetched_at`, so a caller can stamp its output with the age of
the data it was built from.

Eviction is least-recently-used, not insertion-order: a cache hit via
`move_to_end()` refreshes an entry's position, so a frequently-read ticker
survives eviction when capacity is reached.

Arguments must be hashable (same failure mode as functools.lru_cache), since
they are used as dictionary keys.

Not thread-safe by design: the worst case under a race is a duplicate
upstream fetch, which is what the un-cached path did anyway.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

__all__ = ["ttl_cache"]


def _monotonic() -> float:
    """Indirection so tests can freeze the clock."""
    return time.monotonic()


def _make_key(args: tuple, kwargs: dict) -> tuple:
    if not kwargs:
        return args
    return args + tuple(sorted(kwargs.items()))


def ttl_cache(seconds: float, maxsize: int = 256) -> Callable:
    """Memoise a function for `seconds`, evicting least-recently-used beyond `maxsize`.

    Exceptions are never cached. The wrapper exposes `cache_clear()` and
    `peek_fetched_at(*args, **kwargs)`, which returns the UTC time the
    cached value was computed, or None when there is no live entry.
    """

    def decorator(fn: Callable) -> Callable:
        # key -> (stored_at_monotonic, fetched_at_utc, value)
        store: OrderedDict[tuple, tuple[float, datetime, Any]] = OrderedDict()

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = _make_key(args, kwargs)
            now = _monotonic()
            hit = store.get(key)
            if hit is not None and now - hit[0] < seconds:
                store.move_to_end(key)
                return hit[2]

            value = fn(*args, **kwargs)  # exceptions propagate, uncached
            store[key] = (now, datetime.now(tz=UTC), value)
            store.move_to_end(key)
            while len(store) > maxsize:
                store.popitem(last=False)
            return value

        def cache_clear() -> None:
            store.clear()

        def peek_fetched_at(*args: Any, **kwargs: Any) -> datetime | None:
            hit = store.get(_make_key(args, kwargs))
            if hit is None or _monotonic() - hit[0] >= seconds:
                return None
            return hit[1]

        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        wrapper.peek_fetched_at = peek_fetched_at  # type: ignore[attr-defined]
        wrapper.__name__ = getattr(fn, "__name__", "wrapped")
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator
