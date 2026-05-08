"""Watchlist rerank orchestrator (Phase 3).

Reduced 2-lens panel (Quantitative + Contrarian-Risk) -> small Sonnet
rerank judge -> ScanRerankResult. Used by `tsr scan --rerank` to surface
"what's worth deeper research today" beyond rule-based scan.
"""

from __future__ import annotations

from anthropic import Anthropic

from app.logging import get_logger
from app.research.agents.base import AgentResult, run_agents_parallel
from app.research.agents.contrarian import run_contrarian
from app.research.agents.rerank_judge import run_rerank_judge
from app.research.agents.technical import run_technical
from app.research.context import ResearchPacket
from app.research.schema import LensView, ScanRerankResult

log = get_logger(__name__)


def run_rerank(
    packet: ResearchPacket,
    *,
    client: Anthropic | None = None,
) -> ScanRerankResult:
    """Run the reduced rerank panel for one ticker.

    On total analyst failure, returns rank='skip' WITHOUT invoking the judge
    (no point asking Sonnet to weigh nothing).
    """
    runners = [
        lambda p=packet, c=client: run_technical(p, client=c),
        lambda p=packet, c=client: run_contrarian(p, client=c),
    ]
    analyst_results = run_agents_parallel(runners)

    lenses: list[LensView] = [ar.lens for ar in analyst_results if ar.lens is not None]
    analyst_cost = sum(ar.cost_usd for ar in analyst_results)
    analyst_duration = max((ar.duration_ms for ar in analyst_results), default=0)

    if not lenses:
        errs = "; ".join(
            f"{ar.agent_name}: {ar.error}" for ar in analyst_results if ar.error
        )
        return ScanRerankResult(
            ticker=packet.ticker,
            as_of=packet.as_of,
            rank="skip",
            rationale="rerank skipped: both analysts failed",
            lenses=[],
            cost_usd=analyst_cost,
            duration_ms=analyst_duration,
            sources_used=list(packet.sources_used),
            error=f"both analysts failed: {errs}",
        )

    rank, rationale, judge_cost, judge_duration = run_rerank_judge(
        ticker=packet.ticker,
        as_of=packet.as_of,
        lenses=lenses,
        last_close=packet.candidate_levels.last_close if packet.candidate_levels else None,
        atr_14=packet.candidate_levels.atr_14 if packet.candidate_levels else None,
        client=client,
    )

    return ScanRerankResult(
        ticker=packet.ticker,
        as_of=packet.as_of,
        rank=rank,
        rationale=rationale,
        lenses=lenses,
        cost_usd=round(analyst_cost + judge_cost, 6),
        duration_ms=analyst_duration + judge_duration,
        sources_used=list(packet.sources_used),
        error=None,
    )


__all__ = ["run_rerank"]
