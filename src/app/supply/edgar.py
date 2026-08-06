"""SEC EDGAR XBRL companyfacts access.

Three things about this data cost real correctness if ignored, all verified
against live filings on 2026-08-06.

**Tags migrate.** Lumentum's cost line is `CostOfRevenue` until 2017 and
`CostOfGoodsAndServicesSold` after; Micron's `PropertyPlantAndEquipmentNet`
stops in 2020. A single-concept lookup silently truncates history, and a
percentile computed over a truncated history is wrong without looking wrong.
Every metric therefore reads through an ordered chain of concepts.

**The same period is reported many times.** A 10-Q carries the current quarter
and the prior-year comparative, so Micron returns 287 raw GrossProfit points
for about 52 distinct quarters. We deduplicate on period end and keep the
EARLIEST filing, which is also what makes the series point-in-time: a restated
figure is not what was knowable at the time.

**Duration identifies the period type.** Quarterly facts span 80-100 days,
annual about 365, and balance-sheet facts have no start at all.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import requests

from app.logging import get_logger

log = get_logger(__name__)

# SEC requires a declared identity and rate-limits to 10 requests/second.
# Anonymous or aggressive clients are blocked by IP.
SEC_USER_AGENT = "trading-signal-research jaspermvalk@gmail.com"
_HEADERS = {"User-Agent": SEC_USER_AGENT}
_MIN_INTERVAL_S = 0.15  # ~6.7 req/s, comfortably inside the limit

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

QUARTER_MIN_DAYS = 80
QUARTER_MAX_DAYS = 100

_last_request_at = 0.0


@dataclass(frozen=True)
class FactPoint:
    """One reported figure for one period, as first filed."""

    end: date
    filed: date
    value: float


def _throttle() -> None:
    global _last_request_at
    wait = _MIN_INTERVAL_S - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _get_json(url: str) -> dict[str, Any]:
    _throttle()
    response = requests.get(url, headers=_HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def ticker_to_cik() -> dict[str, int]:
    """Map uppercase ticker to CIK. One request; callers should cache."""
    payload = _get_json(TICKER_MAP_URL)
    return {
        str(row["ticker"]).upper(): int(row["cik_str"])
        for row in payload.values()
        if row.get("ticker")
    }


def company_facts(cik: int) -> dict[str, Any]:
    """Full companyfacts document for one filer. Large; cache it."""
    return _get_json(FACTS_URL.format(cik=cik))


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _concept_quarterly_points(node: dict[str, Any]) -> dict[date, FactPoint]:
    """Quarter-length points for a single concept, deduplicated on period end
    keeping the earliest filing.
    """
    points: dict[date, FactPoint] = {}
    for raw in (node.get("units") or {}).get("USD") or []:
        start = _parse_date(raw.get("start", ""))
        end = _parse_date(raw.get("end", ""))
        filed = _parse_date(raw.get("filed", ""))
        if start is None or end is None or filed is None:
            continue
        days = (end - start).days
        if days < QUARTER_MIN_DAYS or days > QUARTER_MAX_DAYS:
            continue
        value = raw.get("val")
        if value is None:
            continue

        existing = points.get(end)
        if existing is not None and existing.filed <= filed:
            continue  # earliest filing wins within this concept
        points[end] = FactPoint(end=end, filed=filed, value=float(value))
    return points


def quarterly_series(facts: dict[str, Any], concepts: list[str]) -> list[FactPoint]:
    """Quarterly points for the first concept in `concepts` covering each period.

    The chain is ordered by preference: an earlier concept owns outright any
    period it reports. A later concept in the chain may only fill periods no
    earlier concept covered — this is how eras using different tags stitch
    into one continuous series without double counting a period that both
    concepts happen to report.
    """
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    by_end: dict[date, FactPoint] = {}

    for concept in concepts:
        node = gaap.get(concept)
        if not node:
            continue
        for end, point in _concept_quarterly_points(node).items():
            if end not in by_end:
                by_end[end] = point

    return sorted(by_end.values(), key=lambda p: p.end)


__all__ = [
    "FactPoint",
    "SEC_USER_AGENT",
    "company_facts",
    "quarterly_series",
    "ticker_to_cik",
]
