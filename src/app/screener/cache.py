"""24-hour disk cache for screener data.

One JSON file per ticker under `data/cache/screener/{TICKER}.json`. Entries
expire after `ttl_hours` (24 by default). Cache misses, expirations, and
read failures are all silent — the caller re-fetches.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from app.config import REPO_ROOT
from app.logging import get_logger

log = get_logger(__name__)

CACHE_DIR = REPO_ROOT / "data" / "cache" / "screener"
DEFAULT_TTL_HOURS = 24


def _cache_path(ticker: str, *, base_dir: Path = CACHE_DIR) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    safe = ticker.upper().replace("/", "_").replace(".", "_")
    return base_dir / f"{safe}.json"


def get(
    ticker: str,
    *,
    ttl_hours: float = DEFAULT_TTL_HOURS,
    base_dir: Path = CACHE_DIR,
    now: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Return cached payload for `ticker` if present and fresh, else None."""
    path = _cache_path(ticker, base_dir=base_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("screener.cache.read_error", ticker=ticker, error=str(e))
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
    ticker: str,
    payload: dict[str, Any],
    *,
    base_dir: Path = CACHE_DIR,
    now: Optional[float] = None,
) -> None:
    """Write payload to disk for `ticker`."""
    path = _cache_path(ticker, base_dir=base_dir)
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
