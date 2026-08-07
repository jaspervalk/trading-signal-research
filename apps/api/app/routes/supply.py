"""Supply-constraint screener endpoints.

Makes `tsr supply-screen` (Layer A: gross-margin-compression candidates
against their own history, `app.supply.screen`) and the Layer B constraint
registry (`app.supply.constraints`) visible and re-runnable from the
dashboard, instead of CLI-only.

Sourcing is deliberately identical to the CLI: `_fetch_supply_market_data`
(imported from `app.cli`, the reference implementation) resolves market cap
and analyst coverage from yfinance `.info` through the same 24h disk cache
under `data/cache/supply_yf/`, and `run_supply_screen` resolves each ticker
to a SEC CIK and pulls EDGAR companyfacts through the same 7-day disk cache
under `data/cache/edgar/`. This router adds no new sourcing logic — it just
exposes the CLI's own pipeline over HTTP.

Every response here is an explicit Pydantic model, materialised field by
field from the underlying dataclasses (`ScreenResult`, `SupplyMetrics`,
`Constraint`, `MarginHistory`) rather than passed through as a
`response_model` over the dataclass itself — mixed dataclass/pydantic
nesting has bitten this project before with silently-dropped fields, so
nothing here is implicit.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.cli import _fetch_supply_market_data, _supply_screen_sort_key
from app.logging import get_logger
from app.screener.universe import load_universe
from app.supply import cache as edgar_cache
from app.supply.concentration import DEFAULT_TOP_N, compute_end_market_concentration
from app.supply.constraints import load_constraints
from app.supply.edgar import company_facts, ticker_to_cik
from app.supply.fundamentals import build_margin_history
from app.supply.screen import run_supply_screen

log = get_logger(__name__)

router = APIRouter(prefix="/supply", tags=["supply"])


# ---------------------------------------------------------------------------
# Response models


class SupplyMetricsOut(BaseModel):
    """Every Layer A metric, visible together — a score with hidden inputs
    is a black box (see the screener's `app.supply.metrics` module docstring
    on why `quarters_of_history` and truncated-history percentiles matter)."""

    quarters_of_history: int
    sufficient_history: bool
    gm_percentile: Optional[float] = None
    margin_headroom_pp: Optional[float] = None
    earnings_torque: Optional[float] = None
    gm_volatility_pp: Optional[float] = None
    capital_intensity: float
    survivability_quarters: Optional[float] = None
    analyst_count: Optional[int] = None
    coverage_multiplier: float
    caveats: list[str]


class SupplyTriggerOut(BaseModel):
    """Layer C gross-margin inflection status (`app.supply.triggers`). Only
    `firing`/`confirmed` (`draws_attention`) are meant to catch a reader's
    eye — `armed` stays quiet, per the brief."""

    status: str
    expansion_streak: int
    prior_decline_pp: Optional[float] = None
    detail: str
    draws_attention: bool


class SupplyScreenRowOut(BaseModel):
    ticker: str
    sector: Optional[str] = None
    # Deliberately coarser than `sector` — see app.supply.concentration
    # module docstring. Reporting only; never affects `passed`/ordering.
    end_market: Optional[str] = None
    cik: Optional[int] = None
    passed: bool
    reasons: list[str]
    # Quarters `build_margin_history` dropped for an arithmetically
    # impossible margin — visible per row so a percentile over truncated
    # history doesn't look trustworthy when it isn't.
    dropped_implausible: int
    metrics: Optional[SupplyMetricsOut] = None
    trigger: Optional[SupplyTriggerOut] = None


class SupplyScreenCoverage(BaseModel):
    universe: int
    resolved: int
    sufficient_history: int
    passed: int


class EndMarketConcentrationOut(BaseModel):
    """Reporting only — see app.supply.concentration module docstring. Never
    weights, filters, or reorders `rows` above."""

    top_n: int
    rows_considered: int
    rows_classified: int
    dominant_end_market: Optional[str] = None
    dominant_count: int
    breakdown: dict[str, int]
    summary: str


class SupplyScreenResponse(BaseModel):
    as_of: datetime
    coverage: SupplyScreenCoverage
    concentration: EndMarketConcentrationOut
    rows: list[SupplyScreenRowOut]


class ConstraintExposureOut(BaseModel):
    ticker: str
    revenue_exposure_pct: float
    exposure_source: str
    is_pure_play: bool


class ConstraintOut(BaseModel):
    id: str
    market: str
    # Optional: a constraint may document real capacity destruction with no
    # credible PROJECTED deficit (see app.supply.constraints.Constraint) —
    # e.g. the TiO2 entry, where confidence=low precisely because the
    # deficit and counter-evidence haven't resolved into a number.
    deficit_pct: Optional[float] = None
    deficit_source: str
    deficit_horizon: str
    expansion_lead_months: Optional[float] = None
    demand_driver: str
    capacity_history: str
    # Evidence that argues AGAINST the thesis above, in the same entry.
    # Optional — most entries don't need one.
    counter_evidence: Optional[str] = None
    confidence: str
    last_reviewed: date
    is_stale: bool
    exposures: list[ConstraintExposureOut]


class MarginHistoryPoint(BaseModel):
    quarter_end: date
    gross_margin: float


class MarginHistoryResponse(BaseModel):
    ticker: str
    cik: Optional[int] = None
    quarters: int
    dropped_implausible: int
    points: list[MarginHistoryPoint]
    current_gross_margin: Optional[float] = None
    historical_peak_gross_margin: Optional[float] = None
    headroom_pp: Optional[float] = None


# ---------------------------------------------------------------------------
# Helpers


def _metrics_out(metrics) -> Optional[SupplyMetricsOut]:  # noqa: ANN001
    if metrics is None:
        return None
    return SupplyMetricsOut(
        quarters_of_history=metrics.quarters_of_history,
        sufficient_history=metrics.sufficient_history,
        gm_percentile=metrics.gm_percentile,
        margin_headroom_pp=metrics.margin_headroom_pp,
        earnings_torque=metrics.earnings_torque,
        gm_volatility_pp=metrics.gm_volatility_pp,
        capital_intensity=metrics.capital_intensity,
        survivability_quarters=metrics.survivability_quarters,
        analyst_count=metrics.analyst_count,
        coverage_multiplier=metrics.coverage_multiplier,
        caveats=list(metrics.caveats),
    )


def _trigger_out(trigger) -> Optional[SupplyTriggerOut]:  # noqa: ANN001
    if trigger is None:
        return None
    return SupplyTriggerOut(
        status=trigger.status,
        expansion_streak=trigger.expansion_streak,
        prior_decline_pp=trigger.prior_decline_pp,
        detail=trigger.detail,
        draws_attention=trigger.draws_attention,
    )


def _run_screen(limit: int) -> SupplyScreenResponse:
    """Same pipeline as `tsr supply-screen`: resolve the universe, fetch
    market caps / analyst counts (cached), run Layer A, rank by
    earnings_torque descending. `limit` only truncates the returned rows —
    coverage counts are computed over the full universe first, same
    discipline as the CLI's own coverage line."""
    from app.config import REPO_ROOT
    from app.supply.metrics import load_thresholds

    universe_path = REPO_ROOT / load_thresholds().universe_path
    universe = load_universe(universe_path)
    if not universe:
        raise HTTPException(500, f"screen universe is empty ({universe_path})")

    tickers = [e.ticker for e in universe]
    sector_by_ticker = {e.ticker.upper(): (e.sector or None) for e in universe}
    end_market_by_ticker = {e.ticker.upper(): (e.end_market or None) for e in universe}

    market_caps, analyst_counts = _fetch_supply_market_data(tickers)
    results = run_supply_screen(
        tickers, market_caps=market_caps, analyst_counts=analyst_counts
    )

    resolved = [r for r in results if r.cik is not None]
    had_history = [
        r for r in resolved if r.metrics is not None and r.metrics.sufficient_history
    ]
    passed = [r for r in results if r.passed]

    ordered = sorted(results, key=_supply_screen_sort_key)
    shown = ordered[:limit]

    # REPORTING ONLY — see app.supply.concentration module docstring. Reads
    # off the full ranked order, never affects `shown`/`rows` below.
    concentration = compute_end_market_concentration(
        [r.ticker for r in ordered], end_market_by_ticker, top_n=DEFAULT_TOP_N
    )

    rows = [
        SupplyScreenRowOut(
            ticker=r.ticker,
            sector=sector_by_ticker.get(r.ticker.upper()),
            end_market=end_market_by_ticker.get(r.ticker.upper()),
            cik=r.cik,
            passed=r.passed,
            reasons=r.reasons,
            dropped_implausible=r.dropped_implausible,
            metrics=_metrics_out(r.metrics),
            trigger=_trigger_out(r.trigger),
        )
        for r in shown
    ]

    return SupplyScreenResponse(
        as_of=datetime.now(timezone.utc),
        coverage=SupplyScreenCoverage(
            universe=len(results),
            resolved=len(resolved),
            sufficient_history=len(had_history),
            passed=len(passed),
        ),
        concentration=EndMarketConcentrationOut(
            top_n=concentration.top_n,
            rows_considered=concentration.rows_considered,
            rows_classified=concentration.rows_classified,
            dominant_end_market=concentration.dominant_end_market,
            dominant_count=concentration.dominant_count,
            breakdown=concentration.breakdown,
            summary=concentration.summary_line,
        ),
        rows=rows,
    )


# Guards POST /supply/screen/refresh against concurrent runs — it purges the
# EDGAR disk cache and re-hits SEC for every resolved ticker sequentially,
# so two in flight at once would double the SEC load for nothing.
_refresh_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Routes


@router.get("/screen", response_model=SupplyScreenResponse)
def get_screen(limit: int = Query(50, ge=1, le=500)) -> SupplyScreenResponse:
    """Run the Layer A supply-constraint screen and return ranked candidates.

    Fast with a warm 7-day EDGAR cache; minutes on a cold one (SEC-throttled,
    sequential — see `app.supply.screen` module docstring). Ranked by
    `earnings_torque` descending, same as `tsr supply-screen`.
    """
    return _run_screen(limit)


@router.post("/screen/refresh", response_model=SupplyScreenResponse)
def refresh_screen(limit: int = Query(50, ge=1, le=500)) -> SupplyScreenResponse:
    """The "do the research again" button: purge the EDGAR disk cache for
    the universe and recompute from scratch.

    Slow (minutes) and hits SEC's companyfacts endpoint once per resolved
    ticker — guarded against concurrent runs; a refresh already in flight
    returns 409 rather than stacking a second SEC-hammering run on top.
    """
    if not _refresh_lock.acquire(blocking=False):
        raise HTTPException(
            409, "a screen refresh is already running — wait for it to finish"
        )
    try:
        n = edgar_cache.clear()
        log.info("supply.screen_refresh.cache_cleared", n=n)
        return _run_screen(limit)
    finally:
        _refresh_lock.release()


@router.get("/constraints", response_model=list[ConstraintOut])
def get_constraints() -> list[ConstraintOut]:
    """The Layer B constraint registry: each entry with `is_stale` and its
    ticker exposures — see `configs/supply_constraints.yaml`."""
    constraints = load_constraints()
    return [
        ConstraintOut(
            id=c.id,
            market=c.market,
            deficit_pct=c.deficit_pct,
            deficit_source=c.deficit_source,
            deficit_horizon=c.deficit_horizon,
            expansion_lead_months=c.expansion_lead_months,
            demand_driver=c.demand_driver,
            capacity_history=c.capacity_history,
            counter_evidence=c.counter_evidence,
            confidence=c.confidence,
            last_reviewed=c.last_reviewed,
            is_stale=c.is_stale,
            exposures=[
                ConstraintExposureOut(
                    ticker=e.ticker,
                    revenue_exposure_pct=e.revenue_exposure_pct,
                    exposure_source=e.exposure_source,
                    is_pure_play=e.is_pure_play,
                )
                for e in c.exposures
            ],
        )
        for c in constraints
    ]


@router.get("/margin-history/{ticker}", response_model=MarginHistoryResponse)
def get_margin_history(ticker: str) -> MarginHistoryResponse:
    """Quarterly gross-margin series for one ticker, plus its historical
    peak and current value — the candidate-detail chart. No price data."""
    ticker = ticker.upper()
    try:
        cik_map = ticker_to_cik()
    except Exception as e:  # noqa: BLE001 - surface as a clean 502, not a 500
        raise HTTPException(502, f"CIK lookup failed: {e}") from e

    cik = cik_map.get(ticker)
    if cik is None:
        raise HTTPException(404, f"no CIK found for ticker {ticker!r}")

    facts = edgar_cache.get(cik)
    if facts is None:
        try:
            facts = company_facts(cik)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"EDGAR companyfacts fetch failed: {e}") from e
        edgar_cache.put(cik, facts)

    history = build_margin_history(facts)
    points = [
        MarginHistoryPoint(quarter_end=end, gross_margin=gm)
        for end, gm in zip(history.ends, history.gross_margins)
    ]
    current = history.gross_margins[-1] if history.gross_margins else None
    peak = max(history.gross_margins) if history.gross_margins else None
    headroom = (
        (peak - current) * 100 if peak is not None and current is not None else None
    )

    return MarginHistoryResponse(
        ticker=ticker,
        cik=cik,
        quarters=history.quarters,
        dropped_implausible=history.dropped_implausible,
        points=points,
        current_gross_margin=current,
        historical_peak_gross_margin=peak,
        headroom_pp=headroom,
    )
