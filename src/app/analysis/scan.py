"""Multi-ticker research scan.

`scan_tickers([...])` runs the existing research orchestrator over a list
of tickers and returns a ranked list of view-summaries. The killer-app
shape: one terminal command (or one API call) shows you the status of
your watchlist at a glance, sorted by quality.

Ranking — the rule, transparent:

    research_candidate (high)   →  rank 0
    research_candidate (medium) →  rank 1
    watch (medium/high)         →  rank 2
    extended_risk               →  rank 3
    wait_for_setup              →  rank 4
    skip_for_now                →  rank 5
    insufficient_data           →  rank 6
    (errors)                    →  rank 99

Within a rank bucket, ties break by `setup_confidence` then by
`primary_style != None`. No magic composite — the rubric in
`status.rubric` is the source of truth and is included in each summary.

Cost note: each ticker fetches yfinance bars + benchmark on cold cache,
which is ~1-2s per fresh ticker. Warm cache is ~50ms. The scan is
*sequential* in V1 to keep yfinance happy — no pmap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.analysis.research import build_ticker_research_view
from app.analysis.schema import (
    DECISION_SUPPORT_STATUSES,
    SETUP_TYPES,
)
from app.analysis.snapshot import persist_snapshot
from app.logging import get_logger

log = get_logger(__name__)


_STATUS_RANK = {
    "research_candidate": 0,
    "watch": 1,
    "extended_risk": 2,
    "wait_for_setup": 3,
    "skip_for_now": 4,
    "insufficient_data": 5,
}
_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def _sort_key(summary: "ScanRow") -> tuple:
    return (
        _STATUS_RANK.get(summary.status, 99),
        _CONFIDENCE_RANK.get(summary.status_confidence, 9),
        _CONFIDENCE_RANK.get(summary.setup_confidence, 9),
        0 if summary.primary_style and summary.primary_style != "not_suitable_now" else 1,
        summary.ticker,
    )


# ---------------------------------------------------------------------------
# Output shape (what the CLI + API both render against)


class ScanRow(BaseModel):
    """One row of a scan result. Compact — designed for tables."""

    ticker: str
    status: str
    status_confidence: str
    setup_type: str
    setup_confidence: str
    primary_style: Optional[str]
    summary: str  # one-line plain-English
    last_close: Optional[float]
    pct_off_52w_high: Optional[float]
    return_5d: Optional[float]
    return_21d: Optional[float]
    rsi_14: Optional[float]
    atr_14_pct: Optional[float]
    relative_strength_vs_spy_63d: Optional[float]
    pullback_pct_from_recent_high: Optional[float]
    breakout_distance_pct: Optional[float]
    entry_zone_available: bool
    entry_trigger: Optional[float]
    invalidation_reference: Optional[float]
    risk_reward_estimate: Optional[float]
    transcript_n_calls: int
    transcript_n_claims: int
    transcript_polarity_30d: Optional[float]
    transcript_confirms: str
    n_bars_loaded: int
    in_universe: bool
    error: Optional[str] = None  # filled when the per-ticker analysis raised


class ScanResult(BaseModel):
    as_of: datetime
    tickers_requested: int
    rows: list[ScanRow]
    n_research_candidates: int
    n_watch: int
    n_skip: int
    n_errors: int


# ---------------------------------------------------------------------------
# Public API


def scan_tickers(
    tickers: list[str],
    *,
    session: Session,
    persist: bool = True,
    fetch_metadata: bool = False,
    history_days: int = 540,
) -> ScanResult:
    """Run research views for `tickers`, return a sorted ScanResult.

    Args:
        tickers: list of symbols (case-insensitive). Duplicates collapsed.
        session: required SQLAlchemy session — used both for transcript
            context AND for persisting snapshots when `persist=True`.
        persist: write a `ResearchSnapshot` per ticker. Default on so daily
            scans build the time-series history.
        fetch_metadata: pass through to research orchestrator. Default off
            because yfinance .info is slow and flaky.
        history_days: pass through.
    """
    as_of = datetime.now(tz=UTC)
    seen: set[str] = set()
    deduped: list[str] = []
    for t in tickers:
        u = t.upper().strip()
        if u and u not in seen:
            seen.add(u)
            deduped.append(u)

    rows: list[ScanRow] = []
    for ticker in deduped:
        try:
            view = build_ticker_research_view(
                ticker,
                session=session,
                as_of=as_of,
                history_days=history_days,
                fetch_metadata=fetch_metadata,
            )
            if persist:
                persist_snapshot(view, session=session)
            rows.append(_row_from_view(view))
        except Exception as e:  # pragma: no cover — defensive surface
            log.warning("research.scan.error", ticker=ticker, error=str(e))
            rows.append(_error_row(ticker, str(e)))

    rows.sort(key=_sort_key)

    return ScanResult(
        as_of=as_of,
        tickers_requested=len(deduped),
        rows=rows,
        n_research_candidates=sum(1 for r in rows if r.status == "research_candidate"),
        n_watch=sum(1 for r in rows if r.status == "watch"),
        n_skip=sum(1 for r in rows if r.status == "skip_for_now"),
        n_errors=sum(1 for r in rows if r.error is not None),
    )


# ---------------------------------------------------------------------------
# Helpers


def _row_from_view(view) -> ScanRow:  # noqa: ANN001
    return ScanRow(
        ticker=view.ticker,
        status=view.status.status,
        status_confidence=view.status.confidence,
        setup_type=view.setup.setup_type,
        setup_confidence=view.setup.confidence,
        primary_style=view.style_fit.primary_style,
        summary=view.status.summary,
        last_close=view.market.last_close,
        pct_off_52w_high=view.market.pct_off_52w_high,
        return_5d=view.market.return_5d,
        return_21d=view.market.return_21d,
        rsi_14=view.indicators.rsi_14,
        atr_14_pct=view.indicators.atr_14_pct,
        relative_strength_vs_spy_63d=view.indicators.relative_strength_vs_spy_63d,
        pullback_pct_from_recent_high=view.levels.pullback_pct_from_recent_high,
        breakout_distance_pct=view.levels.breakout_distance_pct,
        entry_zone_available=view.entry_zone.available,
        entry_trigger=view.entry_zone.setup_trigger_level,
        invalidation_reference=view.entry_zone.invalidation_reference,
        risk_reward_estimate=view.entry_zone.risk_reward_estimate,
        transcript_n_calls=view.transcript.n_calls,
        transcript_n_claims=view.transcript.n_claims,
        transcript_polarity_30d=view.transcript.net_polarity_30d,
        transcript_confirms=view.transcript.confirms_or_contradicts,
        n_bars_loaded=view.identity.n_bars_loaded,
        in_universe=view.identity.in_universe,
    )


def _error_row(ticker: str, msg: str) -> ScanRow:
    return ScanRow(
        ticker=ticker,
        status="insufficient_data",
        status_confidence="low",
        setup_type="insufficient_data",
        setup_confidence="low",
        primary_style=None,
        summary=f"scan failed: {msg}",
        last_close=None,
        pct_off_52w_high=None,
        return_5d=None,
        return_21d=None,
        rsi_14=None,
        atr_14_pct=None,
        relative_strength_vs_spy_63d=None,
        pullback_pct_from_recent_high=None,
        breakout_distance_pct=None,
        entry_zone_available=False,
        entry_trigger=None,
        invalidation_reference=None,
        risk_reward_estimate=None,
        transcript_n_calls=0,
        transcript_n_claims=0,
        transcript_polarity_30d=None,
        transcript_confirms="unknown",
        n_bars_loaded=0,
        in_universe=False,
        error=msg,
    )


__all__ = ["ScanResult", "ScanRow", "scan_tickers"]
