"""Deep-mode orchestrator: 4 parallel analysts → Sonnet judge → EntryExitPlan.

Reuses the same `EntryExitPlan` schema as Quick mode; the only differences
are `mode="deep"`, `agent_trace` populated with one entry per analyst plus
the judge, and `cost_usd` summed across all five LLM calls.

Failure semantics: if an analyst fails, we proceed with the surviving
lenses. The judge sees a smaller `lenses_block` and writes a synthesis
appropriate to the partial coverage. If all four analysts fail, we raise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anthropic import Anthropic

from app.logging import get_logger
from app.research.agents.base import (
    AgentResult,
    run_agents_parallel,
)
from app.research.agents.contrarian import run_contrarian
from app.research.agents.fundamental import run_fundamental
from app.research.agents.judge import JudgeResult, run_judge
from app.research.agents.sentiment import run_sentiment
from app.research.agents.technical import run_technical
from app.research.context import ResearchPacket
from app.research.schema import EntryExitPlan, LensView

log = get_logger(__name__)


def _cross_lens_enabled() -> bool:
    """Read the Phase 2 cross-lens debate toggle. Patched by tests."""
    try:
        from app.config import load_project_settings
        return bool(load_project_settings().research.deep.cross_lens_round)
    except Exception:
        return False


@dataclass
class DeepResult:
    """Same shape as `QuickResult` (a `plan` plus a raw response trail).

    `analyst_results` is exposed for tests / cost-tracking; the orchestrator
    has already merged costs into `plan.cost_usd`.
    """

    plan: EntryExitPlan
    analyst_results: list[AgentResult]
    judge_result: JudgeResult


def run(
    packet: ResearchPacket,
    *,
    client: Anthropic | None = None,
) -> DeepResult:
    """Run Deep mode for `packet`. Synchronous (parallel under the hood).

    Latency: dominated by parallel Haiku calls (~12-18s) + Sonnet judge
    (~15-25s). Total ~30-45s. Cost ~$0.10. Same `(ticker, day, mode)` cache
    key as Quick mode but with `mode="deep"`.
    """
    # 1. Fan out the four analysts in parallel.
    runners = [
        lambda p=packet, c=client: run_technical(p, client=c),
        lambda p=packet, c=client: run_fundamental(p, client=c),
        lambda p=packet, c=client: run_sentiment(p, client=c),
        lambda p=packet, c=client: run_contrarian(p, client=c),
    ]
    analyst_results = run_agents_parallel(runners)

    lenses: list[LensView] = [
        ar.lens for ar in analyst_results if ar.lens is not None
    ]
    if not lenses:
        # Total analyst failure — refuse rather than serving a judge-only plan
        # that the user couldn't audit.
        errs = "; ".join(
            f"{ar.agent_name}: {ar.error}" for ar in analyst_results if ar.error
        )
        raise RuntimeError(f"All four Deep-mode analysts failed: {errs}")
    if len(lenses) < 4:
        log.warning(
            "research.deep.partial_lenses",
            n_succeeded=len(lenses),
            n_failed=4 - len(lenses),
            failures=[
                {"agent": ar.agent_name, "error": ar.error}
                for ar in analyst_results
                if ar.lens is None
            ],
        )

    # Phase 2 — Cross-lens debate: each analyst sees the other three's
    # round-1 reads and may revise. Toggle: research.deep.cross_lens_round.
    if _cross_lens_enabled() and len(lenses) >= 2:
        from app.research.agents.contrarian import run_contrarian_revision
        from app.research.agents.fundamental import run_fundamental_revision
        from app.research.agents.sentiment import run_sentiment_revision
        from app.research.agents.technical import run_technical_revision

        revision_map = {
            "quantitative": run_technical_revision,
            "fundamental": run_fundamental_revision,
            "sentiment_macro": run_sentiment_revision,
            "contrarian_risk": run_contrarian_revision,
        }
        revision_runners: list = []
        for ar in analyst_results:
            if ar.lens is None:
                continue
            others = [
                other.lens for other in analyst_results
                if other.lens is not None and other.agent_name != ar.agent_name
            ]
            runner = revision_map.get(ar.lens.name)
            if runner is None:
                continue
            revision_runners.append(
                lambda r=runner, o=others, rl=ar.lens, p=packet, c=client:
                    r(p, round_one_lens=rl, others=o, client=c)
            )

        if revision_runners:
            revision_results = run_agents_parallel(revision_runners)
            revised_by_name = {
                ar.agent_name: ar.lens
                for ar in revision_results
                if ar.lens is not None
            }
            # Replace round-1 lens with revised one (or keep round-1 on error).
            lenses = [
                revised_by_name.get(lens.name, lens) for lens in lenses
            ]
            # Track total round-2 cost/duration so the audit reflects it, and
            # propagate the revised lens onto analyst_results so downstream
            # (recording, judge trace) sees the revised view.
            for rr in revision_results:
                for ar in analyst_results:
                    if ar.agent_name == rr.agent_name and ar.lens is not None:
                        ar.cost_usd += rr.cost_usd
                        ar.duration_ms += rr.duration_ms
                        ar.lens = revised_by_name.get(ar.agent_name, ar.lens)
                        break
            log.info(
                "research.deep.round_two_complete",
                n_revised=len(revised_by_name),
                n_total=len(revision_runners),
            )

    # Persist lens snapshots for later accuracy scoring (Phase 1).
    # plan_id is None here — the cache layer assigns ids after the judge
    # synthesises. Recording is idempotent so a future "link plan_id"
    # backfill is a separate concern.
    from app.db import session_scope  # local import to avoid cycle at module load
    from app.research.lens_recording import record_lens_snapshots

    durations = [ar.duration_ms for ar in analyst_results if ar.lens is not None]
    costs = [ar.cost_usd for ar in analyst_results if ar.lens is not None]
    try:
        with session_scope() as recording_session:
            record_lens_snapshots(
                recording_session,
                plan_id=None,
                ticker=packet.ticker,
                as_of=packet.as_of,
                mode="deep",
                lenses=lenses,
                sources_used=packet.sources_used,
                durations_ms=durations,
                costs_usd=costs,
            )
    except Exception as e:
        log.warning("research.deep.lens_recording_failed", error=str(e))

    # 2. Sonnet judge synthesises the lenses + picks final levels.
    judge_result = run_judge(
        packet=packet,
        lenses=lenses,
        analyst_results=analyst_results,
        client=client,
    )

    return DeepResult(
        plan=judge_result.plan,
        analyst_results=analyst_results,
        judge_result=judge_result,
    )


__all__ = ["DeepResult", "run"]
