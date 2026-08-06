"""Layer A screen runner: resolve, fetch, and score a list of tickers.

Wires `app.supply.edgar` (CIK resolution + companyfacts), `app.supply.cache`
(7-day disk cache), `app.supply.fundamentals` (margin history) and
`app.supply.metrics` (`compute_metrics` / `passes`) into one call:
`run_supply_screen`.

**A ticker never aborts the batch.** Every failure mode — no CIK, a fetch
exception, too little history to even build trailing-twelve-month figures,
missing market cap, missing PP&E — is caught at the point it occurs and
recorded on that ticker's `ScreenResult.reasons`. The loop always moves on.
Task 5 needs both passes AND failures to report coverage honestly, so this
module never silently drops a ticker; every input produces exactly one
`ScreenResult`.

**Sequential, not threaded.** `edgar._throttle()` is a module-global
timestamp with no lock: two threads could each read a stale
`_last_request_at`, both conclude they're clear to fire, and exceed SEC's
rate limit. A 130-ticker universe costs about 130 * 0.15s = ~20s of throttle
on a cold cache (near-zero once the 7-day cache is warm), which is not
enough win to justify a thread pool plus a lock around a mutable global —
the correctness risk (drawing an IP block from SEC) is not worth it for
maybe-half of 20 seconds.

**PP&E and cash are point-in-time balance-sheet figures, not durations.**
`edgar.quarterly_series` filters on quarter-length duration (80-100 days,
i.e. a `start` and `end`) by design (see its tests) — balance-sheet facts
report only an `end`, no `start`, and are excluded by that filter on
purpose. Resolving PP&E/cash through `quarterly_series` would therefore
always yield an empty series against real EDGAR data. `_latest_instant`
below is the balance-sheet equivalent: same concept-chain-with-earliest-
filing-wins discipline as `quarterly_series`, but keyed on the latest
period `end` rather than a duration window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from app.logging import get_logger
from app.supply import cache as edgar_cache
from app.supply.edgar import company_facts, quarterly_series, ticker_to_cik
from app.supply.fundamentals import (
    CASH,
    OPERATING_CASH_FLOW,
    PPE,
    build_margin_history,
)
from app.supply.metrics import (
    SupplyMetrics,
    Thresholds,
    compute_metrics,
    load_thresholds,
    passes,
)

log = get_logger(__name__)

# TTM figures are trailing-twelve-months: the last 4 reported quarters.
TTM_QUARTERS = 4


@dataclass(frozen=True)
class ScreenResult:
    """One ticker's outcome, whichever stage it stopped at.

    `metrics` and `passed` are only populated when a ticker made it all the
    way through CIK resolution, fetch, and fundamentals assembly. `reasons`
    always explains the outcome: either `passes()`'s full failing-criteria
    list, or a single resolve/fetch/history reason for a ticker that never
    reached scoring. `passed=False` with `metrics=None` and a `reasons`
    entry is the "recorded, not dropped" case the brief requires.
    """

    ticker: str
    cik: Optional[int] = None
    metrics: Optional[SupplyMetrics] = None
    passed: bool = False
    reasons: list[str] = field(default_factory=list)
    # Quarters dropped by `build_margin_history` for an arithmetically
    # impossible margin (see `app.supply.fundamentals` module docstring) —
    # carried here (rather than left buried in `MarginHistory`, which this
    # dataclass does not otherwise reference) so a caller can see how much
    # of a ticker's reported history was thrown out before trusting
    # `metrics.gm_percentile` / `margin_headroom_pp`. 0 for tickers that
    # never reached fundamentals assembly (no CIK, fetch failure, etc).
    dropped_implausible: int = 0


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _latest_instant(facts: dict[str, Any], concepts: list[str]) -> float | None:
    """Most recent point-in-time value for the first concept in `concepts`
    that reports any instant (balance-sheet) facts.

    Concepts are tried in preference order, same as `quarterly_series`; the
    first concept with any usable points owns the value outright (no
    cross-concept merge needed here — unlike the income-statement chains,
    we only need one current figure, not a continuous history). Within a
    concept, the latest period `end` wins, and if that period was reported
    in more than one filing, the earliest `filed` wins — still point-in-time
    correct.
    """
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    for concept in concepts:
        node = gaap.get(concept)
        if not node:
            continue
        by_end: dict[date, tuple[date, float]] = {}
        for raw in (node.get("units") or {}).get("USD") or []:
            end = _parse_date(raw.get("end", ""))
            filed = _parse_date(raw.get("filed", ""))
            value = raw.get("val")
            if end is None or filed is None or value is None:
                continue
            existing = by_end.get(end)
            if existing is not None and existing[0] <= filed:
                continue  # earliest filing wins for this period
            by_end[end] = (filed, float(value))
        if by_end:
            latest_end = max(by_end)
            return by_end[latest_end][1]
    return None


def _ttm_from_margin_history(margin_history) -> tuple[float, float] | None:
    """(ttm_gross_margin, ttm_revenue) from the most recent `TTM_QUARTERS`
    quarters, or `None` if there isn't enough history to sum a trailing
    twelve months at all. This is a lower, separate floor from
    `Thresholds.min_quarters_history` (which governs percentile validity,
    not TTM computability) — a ticker with 2 quarters of history can't
    produce a TTM figure at all, let alone a meaningful percentile.
    """
    revenues = margin_history.revenues[-TTM_QUARTERS:]
    margins = margin_history.gross_margins[-TTM_QUARTERS:]
    if len(revenues) < TTM_QUARTERS:
        return None
    ttm_revenue = sum(revenues)
    if ttm_revenue <= 0:
        return None
    ttm_gross_profit = sum(m * r for m, r in zip(margins, revenues))
    return ttm_gross_profit / ttm_revenue, ttm_revenue


def _lookup(mapping: dict[str, Any], ticker: str) -> Any:
    """Tickers may be passed upper- or mixed-case by the caller; try both."""
    if ticker in mapping:
        return mapping[ticker]
    return mapping.get(ticker.upper())


def _screen_one(
    ticker: str,
    cik_map: dict[str, int],
    market_caps: dict[str, float],
    analyst_counts: dict[str, Optional[int]],
    thresholds: Thresholds,
    *,
    cache_dir: Path,
    now: float | None,
) -> ScreenResult:
    cik = _lookup(cik_map, ticker)
    if cik is None:
        return ScreenResult(ticker=ticker, reasons=[f"no CIK found for ticker {ticker.upper()!r}"])

    facts = edgar_cache.get(cik, base_dir=cache_dir, now=now)
    if facts is None:
        try:
            facts = company_facts(cik)
        except Exception as e:  # noqa: BLE001 - one ticker's fetch failure must not sink the batch
            log.warning("supply_screen.fetch_failed", ticker=ticker, cik=cik, error=str(e))
            return ScreenResult(
                ticker=ticker, cik=cik, reasons=[f"companyfacts fetch failed: {e}"]
            )
        edgar_cache.put(cik, facts, base_dir=cache_dir, now=now)

    margin_history = build_margin_history(facts)
    ttm = _ttm_from_margin_history(margin_history)
    if ttm is None:
        return ScreenResult(
            ticker=ticker,
            cik=cik,
            reasons=[
                f"too little history to compute TTM figures: "
                f"{margin_history.quarters} quarters (need >= {TTM_QUARTERS})"
            ],
            dropped_implausible=margin_history.dropped_implausible,
        )
    ttm_gross_margin, ttm_revenue = ttm

    market_cap = _lookup(market_caps, ticker)
    if not market_cap:
        return ScreenResult(
            ticker=ticker,
            cik=cik,
            reasons=["missing or non-positive market cap"],
            dropped_implausible=margin_history.dropped_implausible,
        )

    ppe = _latest_instant(facts, PPE)
    if ppe is None:
        return ScreenResult(
            ticker=ticker,
            cik=cik,
            reasons=["no PP&E reported under any known concept"],
            dropped_implausible=margin_history.dropped_implausible,
        )

    cash = _latest_instant(facts, CASH) or 0.0

    ocf_points = quarterly_series(facts, OPERATING_CASH_FLOW)
    quarterly_ocf = [p.value for p in ocf_points]
    ttm_operating_cash_flow = sum(p.value for p in ocf_points[-TTM_QUARTERS:])

    analyst_count = _lookup(analyst_counts, ticker)

    metrics = compute_metrics(
        margin_history,
        ttm_gross_margin=ttm_gross_margin,
        ttm_revenue=ttm_revenue,
        market_cap=market_cap,
        ppe=ppe,
        cash=cash,
        ttm_operating_cash_flow=ttm_operating_cash_flow,
        quarterly_operating_cash_flows=quarterly_ocf,
        analyst_count=analyst_count,
        thresholds=thresholds,
    )
    ok, reasons = passes(metrics, thresholds)
    return ScreenResult(
        ticker=ticker,
        cik=cik,
        metrics=metrics,
        passed=ok,
        reasons=reasons,
        dropped_implausible=margin_history.dropped_implausible,
    )


def run_supply_screen(
    tickers: list[str],
    *,
    market_caps: dict[str, float],
    analyst_counts: dict[str, Optional[int]],
    thresholds: Thresholds | None = None,
    cache_dir: Path | None = None,
    now: float | None = None,
) -> list[ScreenResult]:
    """Run the Layer A screen over `tickers`.

    Returns one `ScreenResult` per input ticker, in input order — passes
    and failures both, never silently dropped, so Task 5 can report
    coverage (resolved / had enough history / passed) honestly.

    `market_caps` and `analyst_counts` are ticker-keyed lookups the caller
    supplies (yfinance, typically); a missing `analyst_count` entry is not
    a failure (see `app.supply.metrics` — it's a caveat), but a missing or
    non-positive `market_cap` is, since `earnings_torque` divides by it.

    `cache_dir` and `now` exist for tests: pass a `tmp_path` to keep a test
    run off the real `data/cache/edgar/` disk cache, and `now` to control
    TTL expiry deterministically instead of sleeping.
    """
    thresholds = thresholds or load_thresholds()
    resolved_cache_dir = cache_dir or edgar_cache.CACHE_DIR

    try:
        cik_map = ticker_to_cik()
    except Exception as e:  # noqa: BLE001 - an outage on the one shared lookup must not crash the batch
        log.warning("supply_screen.cik_map_failed", error=str(e))
        return [ScreenResult(ticker=t, reasons=[f"CIK map unavailable: {e}"]) for t in tickers]

    return [
        _screen_one(
            ticker,
            cik_map,
            market_caps,
            analyst_counts,
            thresholds,
            cache_dir=resolved_cache_dir,
            now=now,
        )
        for ticker in tickers
    ]


__all__ = ["ScreenResult", "run_supply_screen"]
