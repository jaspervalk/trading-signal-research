"""Orchestrator — turn a ticker symbol into a `TickerResearchView`.

The single public entry point. Composes `IdentityCoverage`,
`MarketSnapshotPanel`, `IndicatorPanel`, `LevelsPanel`,
`SetupClassification`, `StyleFitPanel`, `DecisionSupportStatus`,
`EntryZoneCandidate`, and `TranscriptContext` into the final view.

Discipline:
- Universe-agnostic: works for any ticker yfinance can resolve. The
  `in_universe` field on `IdentityCoverage` reports whether the symbol
  appears in `configs/universe.csv` (used by extractor validation), but
  is NOT a precondition.
- Leakage-controlled: every panel takes the same `as_of`. The orchestrator
  pulls daily bars over a window ending at `as_of` and slices.
- DB-read-only: reads `TickerSignal` / `Claim` / `ExtractedCall` /
  `CreatorScorecard` rows for the transcript panel, but does not write.
- Failure mode: every panel degrades gracefully to "insufficient data"
  rather than raising. The only hard error is a yfinance failure that
  returns no bars — in that case the view is returned with empty
  identity/market/indicator panels and `data_sufficiency='insufficient'`.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.analysis.action import derive_action_signal
from app.analysis.entry import build_entry_zone
from app.analysis.indicators import (
    build_indicator_panel,
    build_market_snapshot,
    slice_at_or_before,
)
from app.analysis.levels import build_levels_panel
from app.analysis.schema import (
    IdentityCoverage,
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
    TickerResearchView,
)
from app.analysis.setup import classify_setup
from app.analysis.status import derive_status
from app.analysis.style import evaluate_style_fit
from app.analysis.transcript import build_transcript_context
from app.config import REPO_ROOT
from app.logging import get_logger
from app.market.yfinance_client import get_daily_bars
from app.ttl_cache import ttl_cache

log = get_logger(__name__)

_DEFAULT_HISTORY_DAYS = 540  # ~2y; enough for SMA-200, 252-bar returns, RS-126
_DEFAULT_BENCHMARK = "SPY"


# ---------------------------------------------------------------------------
# Universe lookup (cached, file-based)


@lru_cache(maxsize=1)
def _load_universe() -> set[str]:
    path = REPO_ROOT / "configs" / "universe.csv"
    if not path.exists():
        return set()
    out: set[str] = set()
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = row.get("ticker") or row.get("symbol") or row.get("Ticker")
            if t:
                out.add(t.strip().upper())
    return out


# ---------------------------------------------------------------------------
# yfinance metadata (best-effort, slow, cached in-process)

# Metadata TTL. forward_pe and friends are price-derived, so they go stale
# within minutes; the earnings date does not, so it gets a longer window.
METADATA_TTL_SECONDS = 900  # 15 minutes
EARNINGS_TTL_SECONDS = 21600  # 6 hours


def _yf_info(ticker: str) -> dict:
    """Raw yfinance .info fetch. Split out so tests can patch it."""
    import yfinance as yf

    info = yf.Ticker(ticker).get_info()
    # Newer yfinance returns dict; older returns dict-like. Normalize.
    return dict(info) if info else {}


@ttl_cache(seconds=METADATA_TTL_SECONDS)
def _resolve_metadata(ticker: str) -> dict:
    """Best-effort wrapper around yf.Ticker(...).info.

    Cached for METADATA_TTL_SECONDS. Failures are silent; the research view
    degrades gracefully when metadata is missing. Do not call on a hot path.
    """
    try:
        return _yf_info(ticker)
    except Exception as e:  # pragma: no cover — yfinance is flaky
        log.warning("research.metadata.error", ticker=ticker, error=str(e))
        return {}


@ttl_cache(seconds=EARNINGS_TTL_SECONDS)
def _resolve_next_earnings(ticker: str) -> datetime | None:
    """Best-effort fetch of the next earnings date via yfinance.

    yfinance exposes upcoming earnings via `Ticker(t).calendar`; the shape
    varies by version (DataFrame in older releases, dict in newer ones).
    We accept both. Returns None if nothing is available — the research
    view treats `days_to_next_earnings is None` as "unknown".
    """
    try:
        import yfinance as yf

        cal = yf.Ticker(ticker).calendar
    except Exception as e:  # pragma: no cover — yfinance is flaky
        log.warning("research.calendar.error", ticker=ticker, error=str(e))
        return None
    if cal is None:
        return None

    # Newer yfinance: dict with key "Earnings Date" → list[date]
    if isinstance(cal, dict):
        dates = cal.get("Earnings Date") or cal.get("earnings_date")
        if dates and len(dates) > 0:
            d = dates[0]
            if hasattr(d, "isoformat"):
                # date or datetime
                if not hasattr(d, "tzinfo"):
                    return datetime.combine(d, datetime.min.time(), tzinfo=UTC)
                return d if d.tzinfo else d.replace(tzinfo=UTC)
        return None

    # Older yfinance: pandas DataFrame with Earnings Date row.
    try:
        if "Earnings Date" in cal.index:
            d = cal.loc["Earnings Date"].iloc[0]
            if hasattr(d, "to_pydatetime"):
                d = d.to_pydatetime()
            return d if d.tzinfo else d.replace(tzinfo=UTC)
    except Exception:  # pragma: no cover
        return None
    return None


def _build_valuation(
    *,
    ticker: str,
    metadata: dict,
    as_of: datetime,
    fetch_metadata: bool,
    fetched_at: datetime | None = None,
) -> "ValuationPanel":  # noqa: F821 — forward ref on the type
    """Assemble the `ValuationPanel` from yfinance metadata.

    All extraction is defensive — yfinance returns inconsistent shapes
    across tickers. Missing fields stay None; the UI handles that.
    """
    from app.analysis.schema import ValuationPanel

    if not metadata:
        return ValuationPanel()

    next_earn = _resolve_next_earnings(ticker) if fetch_metadata else None
    days_to_earn: int | None = None
    if next_earn is not None:
        delta = (next_earn - as_of).total_seconds() / 86400
        # Negative deltas mean earnings already passed — yfinance.calendar
        # sometimes lists the most-recent print rather than the next one.
        # Surface negative values so the UI can show "X days post-earnings".
        days_to_earn = int(round(delta))

    return ValuationPanel(
        market_cap=_safe_float(metadata.get("marketCap")),
        forward_pe=_safe_float(metadata.get("forwardPE")),
        trailing_pe=_safe_float(metadata.get("trailingPE")),
        peg_ratio=_safe_float(metadata.get("pegRatio") or metadata.get("trailingPegRatio")),
        price_to_sales_ttm=_safe_float(metadata.get("priceToSalesTrailing12Months")),
        price_to_book=_safe_float(metadata.get("priceToBook")),
        enterprise_to_ebitda=_safe_float(metadata.get("enterpriseToEbitda")),
        earnings_growth_forward=_safe_float(metadata.get("earningsGrowth")),
        revenue_growth_yoy=_safe_float(metadata.get("revenueGrowth")),
        profit_margins=_safe_float(metadata.get("profitMargins")),
        float_shares=_safe_float(metadata.get("floatShares")),
        shares_outstanding=_safe_float(metadata.get("sharesOutstanding")),
        short_pct_of_float=_safe_float(metadata.get("shortPercentOfFloat")),
        held_pct_institutions=_safe_float(metadata.get("heldPercentInstitutions")),
        beta=_safe_float(metadata.get("beta")),
        dividend_yield=_safe_float(metadata.get("dividendYield")),
        days_to_next_earnings=days_to_earn,
        next_earnings_date=next_earn,
        sector=metadata.get("sector"),
        industry=metadata.get("industry"),
        fetched_at=fetched_at,
    )


def _safe_float(value: object) -> float | None:
    """Coerce yfinance values (which can be int, float, str, None, NaN) to float|None."""
    if value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


# ---------------------------------------------------------------------------
# Public API


def build_ticker_research_view(
    ticker: str,
    *,
    session: Session | None = None,
    as_of: datetime | None = None,
    history_days: int = _DEFAULT_HISTORY_DAYS,
    benchmark_ticker: str = _DEFAULT_BENCHMARK,
    fetch_metadata: bool = True,
) -> TickerResearchView:
    """Build the full research view for `ticker`.

    Args:
        ticker: symbol; case-insensitive. Need not be in `configs/universe.csv`.
        session: optional SQLAlchemy session for transcript context. If None,
            we skip the transcript-DB read and return a stub TranscriptContext
            (still useful for ad-hoc tickers without DB access).
        as_of: cutoff timestamp. Defaults to "now" UTC. All compute paths
            slice at or before this timestamp.
        history_days: how much history to request from yfinance.
        benchmark_ticker: benchmark for relative-strength + excess returns.
        fetch_metadata: whether to call `yf.Ticker(...).info`. Disable on
            slow networks or tests.
    """
    ticker = ticker.upper()
    if as_of is None:
        as_of = datetime.now(tz=UTC)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)

    # 1. Fetch bars (always; the rest depends on this).
    start = as_of - timedelta(days=history_days)
    try:
        bars = get_daily_bars(ticker, start=start, end=as_of)
    except Exception as e:
        log.warning("research.bars.error", ticker=ticker, error=str(e))
        bars = pd.DataFrame()

    try:
        bench_bars = get_daily_bars(benchmark_ticker, start=start, end=as_of)
    except Exception as e:
        log.warning("research.benchmark.error", ticker=benchmark_ticker, error=str(e))
        bench_bars = pd.DataFrame()

    bars = slice_at_or_before(bars, as_of)
    bench_bars = slice_at_or_before(bench_bars, as_of)
    n_bars = len(bars)

    # 2. Identity panel
    universe = _load_universe()
    in_universe = ticker in universe
    metadata = _resolve_metadata(ticker) if fetch_metadata else {}
    metadata_fetched_at = _resolve_metadata.peek_fetched_at(ticker) if fetch_metadata else None
    identity = _build_identity(
        ticker=ticker,
        bars=bars,
        as_of=as_of,
        in_universe=in_universe,
        metadata=metadata,
        session=session,
    )

    # 2b. Valuation panel (best-effort, always emitted; empty on cache miss).
    valuation = _build_valuation(
        ticker=ticker,
        metadata=metadata,
        as_of=as_of,
        fetch_metadata=fetch_metadata,
        fetched_at=metadata_fetched_at,
    )

    # 3. Compute the technical panels.
    indicators = build_indicator_panel(bars, bench_bars, as_of=as_of)
    market = build_market_snapshot(bars, bench_bars, as_of=as_of)
    levels = build_levels_panel(bars, as_of=as_of)

    # 4. Setup classification + style fit + status + entry zone.
    setup = classify_setup(indicators, levels, market, n_bars=n_bars)
    style_fit = evaluate_style_fit(setup, indicators, levels)
    status = derive_status(setup, style_fit, indicators, market, levels, identity)
    entry_zone = build_entry_zone(setup, indicators, levels, market)

    # 5. Transcript context (only if a session was provided).
    if session is not None:
        transcript = build_transcript_context(session, ticker, setup, as_of=as_of)
    else:
        transcript = _stub_transcript()

    # 6. Implied action label — derived view over status, never independent.
    action = derive_action_signal(status, setup, entry_zone, transcript)

    return TickerResearchView(
        ticker=ticker,
        as_of=as_of,
        identity=identity,
        market=market,
        valuation=valuation,
        indicators=indicators,
        levels=levels,
        setup=setup,
        style_fit=style_fit,
        status=status,
        action=action,
        entry_zone=entry_zone,
        transcript=transcript,
    )


# ---------------------------------------------------------------------------
# Internal helpers


def _build_identity(
    *,
    ticker: str,
    bars: pd.DataFrame,
    as_of: datetime,
    in_universe: bool,
    metadata: dict,
    session: Session | None,
) -> IdentityCoverage:
    n_bars = len(bars)
    last_bar_at = (
        bars.index[-1].to_pydatetime() if n_bars > 0 else None
    )
    if last_bar_at is not None and last_bar_at.tzinfo is None:
        last_bar_at = last_bar_at.replace(tzinfo=UTC)
    freshness_days = (
        int((as_of - last_bar_at).total_seconds() // (24 * 3600))
        if last_bar_at is not None
        else None
    )

    has_signals = has_calls = has_claims = False
    if session is not None:
        from sqlalchemy import select

        from app.models import (
            CLAIM_STATUS_ACCEPTED,
            Claim,
            ExtractedCall,
            TickerSignal,
        )

        has_signals = (
            session.execute(
                select(TickerSignal.id).where(TickerSignal.ticker == ticker).limit(1)
            ).first()
            is not None
        )
        has_calls = (
            session.execute(
                select(ExtractedCall.id)
                .where(ExtractedCall.ticker == ticker)
                .where(ExtractedCall.status == "accepted")
                .limit(1)
            ).first()
            is not None
        )
        has_claims = (
            session.execute(
                select(Claim.id)
                .where(Claim.ticker == ticker)
                .where(Claim.status == CLAIM_STATUS_ACCEPTED)
                .limit(1)
            ).first()
            is not None
        )

    warnings: list[str] = []
    if n_bars == 0:
        warnings.append("yfinance returned no bars — ticker may be invalid or delisted.")
    elif n_bars < 200:
        warnings.append(
            f"Only {n_bars} bars loaded; the long-trend stack (SMA-200) "
            "and 252-day returns will be marked unavailable."
        )
    if freshness_days is not None and freshness_days > 7:
        warnings.append(
            f"Most recent bar is {freshness_days}d old — data may be stale."
        )

    name = metadata.get("longName") or metadata.get("shortName")
    asset_type = metadata.get("quoteType")
    exchange = metadata.get("fullExchangeName") or metadata.get("exchange")
    sector = metadata.get("sector")

    return IdentityCoverage(
        ticker=ticker,
        name=name,
        asset_type=asset_type,
        exchange=exchange,
        sector=sector,
        in_universe=in_universe,
        has_transcript_signals=has_signals,
        has_extracted_calls=has_calls,
        has_extracted_claims=has_claims,
        n_bars_loaded=n_bars,
        enough_history_for_full_analysis=n_bars >= 252,
        data_freshness_days=freshness_days,
        missing_data_warnings=warnings,
    )


def _stub_transcript():
    from app.analysis.schema import TranscriptContext

    return TranscriptContext(
        has_data=False,
        n_signals=0,
        n_calls=0,
        n_claims=0,
        coverage_status="absent",
        summary="Transcript context skipped (no DB session passed).",
        notes=["Pass a SQLAlchemy session to enable transcript-side analysis."],
    )


__all__ = ["build_ticker_research_view"]
