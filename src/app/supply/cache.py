"""7-day disk cache for EDGAR companyfacts.

One JSON file per CIK under `data/cache/edgar/CIK{cik:010d}.json` — zero-
padded to match SEC's own companyfacts file naming
(`CIK0000723125.json`-style). Entries expire after `ttl_hours` (7 days by
default): filings do not change intraday, and a single companyfacts document
can carry 600+ concepts, so caching is what makes a 100+ ticker screen run
tolerable instead of re-downloading everything on every invocation.

Shape mirrors `app.screener.cache` deliberately, for consistency across the
two disk caches in the codebase. Cache misses, expirations, and read
failures are all silent — the caller re-fetches.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from app.config import REPO_ROOT
from app.logging import get_logger

log = get_logger(__name__)

CACHE_DIR = REPO_ROOT / "data" / "cache" / "edgar"
DEFAULT_TTL_HOURS = 24 * 7


def _cache_path(cik: int, *, base_dir: Path = CACHE_DIR) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"CIK{cik:010d}.json"


def get(
    cik: int,
    *,
    ttl_hours: float = DEFAULT_TTL_HOURS,
    base_dir: Path = CACHE_DIR,
    now: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Return cached companyfacts for `cik` if present and fresh, else None."""
    path = _cache_path(cik, base_dir=base_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("edgar.cache.read_error", cik=cik, error=str(e))
        return None
    cached_at = raw.get("_cached_at")
    if not isinstance(cached_at, (int, float)):
        return None
    age_hours = ((now or time.time()) - cached_at) / 3600.0
    if age_hours > ttl_hours:
        return None
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload


def put(
    cik: int,
    payload: dict[str, Any],
    *,
    base_dir: Path = CACHE_DIR,
    now: Optional[float] = None,
) -> None:
    """Write companyfacts payload to disk for `cik`."""
    path = _cache_path(cik, base_dir=base_dir)
    body = {"_cached_at": now or time.time(), "payload": payload}
    path.write_text(json.dumps(body, default=str))


def clear(*, base_dir: Path = CACHE_DIR) -> int:
    """Remove every cache entry. Returns the count deleted."""
    if not base_dir.exists():
        return 0
    n = 0
    for p in base_dir.glob("*.json"):
        p.unlink()
        n += 1
    return n


__all__ = ["CACHE_DIR", "DEFAULT_TTL_HOURS", "clear", "get", "put"]
