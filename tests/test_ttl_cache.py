"""TTL cache: expiry, fetch-time introspection, and clearing."""

from __future__ import annotations

from datetime import UTC, datetime

from app.ttl_cache import ttl_cache


def test_caches_within_ttl():
    calls = []

    @ttl_cache(seconds=100)
    def f(x):
        calls.append(x)
        return x * 2

    assert f(3) == 6
    assert f(3) == 6
    assert calls == [3]


def test_expires_after_ttl(monkeypatch):
    calls = []
    clock = [1000.0]

    @ttl_cache(seconds=100)
    def f(x):
        calls.append(x)
        return x * 2

    monkeypatch.setattr("app.ttl_cache._monotonic", lambda: clock[0])
    f(3)
    clock[0] += 101
    f(3)
    assert calls == [3, 3]


def test_distinct_args_cached_separately():
    calls = []

    @ttl_cache(seconds=100)
    def f(x):
        calls.append(x)
        return x

    f("a")
    f("b")
    f("a")
    assert calls == ["a", "b"]


def test_peek_fetched_at_returns_utc_aware_time():
    @ttl_cache(seconds=100)
    def f(x):
        return x

    assert f.peek_fetched_at("NVDA") is None
    f("NVDA")
    stamp = f.peek_fetched_at("NVDA")
    assert isinstance(stamp, datetime)
    assert stamp.tzinfo is not None
    assert stamp.tzinfo.utcoffset(stamp) == UTC.utcoffset(stamp)


def test_peek_fetched_at_is_none_after_expiry(monkeypatch):
    clock = [1000.0]

    @ttl_cache(seconds=10)
    def f(x):
        return x

    monkeypatch.setattr("app.ttl_cache._monotonic", lambda: clock[0])
    f("NVDA")
    assert f.peek_fetched_at("NVDA") is not None
    clock[0] += 11
    assert f.peek_fetched_at("NVDA") is None


def test_cache_clear_drops_everything():
    calls = []

    @ttl_cache(seconds=100)
    def f(x):
        calls.append(x)
        return x

    f(1)
    f.cache_clear()
    f(1)
    assert calls == [1, 1]
    assert f.peek_fetched_at(1) is not None


def test_maxsize_evicts_oldest():
    @ttl_cache(seconds=100, maxsize=2)
    def f(x):
        return x

    f(1)
    f(2)
    f(3)
    assert f.peek_fetched_at(1) is None
    assert f.peek_fetched_at(3) is not None


def test_exceptions_are_not_cached():
    calls = []

    @ttl_cache(seconds=100)
    def f(x):
        calls.append(x)
        raise ValueError("boom")

    for _ in range(2):
        try:
            f(1)
        except ValueError:
            pass
    assert calls == [1, 1]
