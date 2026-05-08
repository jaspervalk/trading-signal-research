"""Persist a `TickerResearchView` as a `ResearchSnapshot` row.

Every research-view computation (CLI or API) optionally writes one of these
rows. Append-only — multiple snapshots per ticker accumulate so the
dashboard can render "how this ticker's setup evolved over time."

Failure mode: if persistence fails (DB unavailable, schema drift), we log
and swallow. The research view still returns; the snapshot is best-effort.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.analysis.schema import TickerResearchView
from app.logging import get_logger
from app.models import ResearchSnapshot

log = get_logger(__name__)


def persist_snapshot(
    view: TickerResearchView,
    *,
    session: Session,
) -> int | None:
    """Write a `ResearchSnapshot` for `view`. Returns row id or None on error."""
    try:
        row = _row_from_view(view)
        session.add(row)
        session.flush()
        return int(row.id)
    except Exception as e:  # pragma: no cover — best-effort
        log.warning(
            "research.snapshot.persist_failed",
            ticker=view.ticker,
            error=str(e),
        )
        return None


def _row_from_view(view: TickerResearchView) -> ResearchSnapshot:
    return ResearchSnapshot(
        ticker=view.ticker,
        as_of=view.as_of,
        status=view.status.status,
        status_confidence=view.status.confidence,
        setup_type=view.setup.setup_type,
        setup_confidence=view.setup.confidence,
        primary_style=view.style_fit.primary_style,
        last_close=view.market.last_close,
        pct_off_52w_high=view.market.pct_off_52w_high,
        return_5d=view.market.return_5d,
        return_21d=view.market.return_21d,
        return_63d=view.market.return_63d,
        ma_alignment=view.indicators.ma_alignment,
        rsi_14=view.indicators.rsi_14,
        atr_14_pct=view.indicators.atr_14_pct,
        sma_50_slope_21d_pct=view.indicators.sma_50_slope_21d_pct,
        relative_strength_vs_spy_63d=view.indicators.relative_strength_vs_spy_63d,
        nearest_support=view.levels.nearest_support,
        nearest_resistance=view.levels.nearest_resistance,
        pullback_pct_from_recent_high=view.levels.pullback_pct_from_recent_high,
        breakout_distance_pct=view.levels.breakout_distance_pct,
        entry_zone_available=view.entry_zone.available,
        entry_trigger=view.entry_zone.setup_trigger_level,
        entry_zone_low=view.entry_zone.candidate_research_zone_low,
        entry_zone_high=view.entry_zone.candidate_research_zone_high,
        invalidation_reference=view.entry_zone.invalidation_reference,
        risk_reward_estimate=view.entry_zone.risk_reward_estimate,
        transcript_n_calls=view.transcript.n_calls,
        transcript_n_claims=view.transcript.n_claims,
        transcript_n_creators=view.transcript.n_distinct_creators,
        transcript_polarity_30d=view.transcript.net_polarity_30d,
        transcript_coverage_status=view.transcript.coverage_status,
        transcript_confirms=view.transcript.confirms_or_contradicts,
    )


__all__ = ["persist_snapshot"]
