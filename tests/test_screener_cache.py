"""Cache: fresh hit, expired miss, malformed-file miss."""

from __future__ import annotations

import json
import time
from pathlib import Path

from app.screener import cache


def test_put_then_get_within_ttl(tmp_path: Path):
    cache.put("AAPL", {"info": {"sector": "Tech"}}, base_dir=tmp_path, now=1000.0)
    payload = cache.get("AAPL", base_dir=tmp_path, now=1000.0 + 60.0)
    assert payload == {"info": {"sector": "Tech"}}


def test_expired_cache_returns_none(tmp_path: Path):
    cache.put("MSFT", {"info": {"sector": "Tech"}}, base_dir=tmp_path, now=1000.0)
    older_than_ttl = 1000.0 + (cache.DEFAULT_TTL_HOURS * 3600.0) + 1.0
    assert cache.get("MSFT", base_dir=tmp_path, now=older_than_ttl) is None


def test_malformed_cache_returns_none(tmp_path: Path):
    (tmp_path / "BAD.json").write_text("{not json")
    assert cache.get("BAD", base_dir=tmp_path) is None


def test_cache_is_case_insensitive_on_filename(tmp_path: Path):
    cache.put("aapl", {"info": {}}, base_dir=tmp_path, now=1000.0)
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    # And both casings find it
    assert cache.get("AAPL", base_dir=tmp_path, now=1000.0) is not None
    assert cache.get("aapl", base_dir=tmp_path, now=1000.0) is not None


def test_clear_removes_all(tmp_path: Path):
    cache.put("AAPL", {}, base_dir=tmp_path)
    cache.put("MSFT", {}, base_dir=tmp_path)
    n = cache.clear(base_dir=tmp_path)
    assert n == 2
    assert list(tmp_path.glob("*.json")) == []


def test_cache_persists_dict_payload(tmp_path: Path):
    cache.put("AAPL", {"info": {"sector": "Technology"}}, base_dir=tmp_path, now=1.0)
    raw = json.loads((tmp_path / "AAPL.json").read_text())
    assert raw["_cached_at"] == 1.0
    assert raw["payload"] == {"info": {"sector": "Technology"}}


def test_explicit_now_used_for_freshness(tmp_path: Path):
    # Ensure now arg overrides time.time() for deterministic tests
    cache.put("AAPL", {"k": 1}, base_dir=tmp_path, now=time.time())
    # Far-future now → expired
    assert cache.get("AAPL", base_dir=tmp_path, now=time.time() + 1e9) is None
