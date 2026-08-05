"""Judge agent — synthesises the four analyst lenses into a final
`EntryExitPlan` (Sonnet 4.6).

The judge does NOT re-litigate each lens; it accepts each analyst's read
as authoritative within that analyst's discipline. Its job is to:

1. Pick the final levels by *reference* (entry by kind; exits and
   invalidation by integer index into the deterministic candidate
   lists). The system resolves picks against the candidates verbatim —
   no scaling, no clamping, no ±15% drift. R/R run-to-run variance for
   a given packet is bounded by the index-pick step alone (and Quick
   mode's temperature=0 keeps that step deterministic too).
2. Decide a final `confidence` and `timeframe` weighed across lenses
   (with the upstream rubric as a hard ceiling — same as Quick).
3. Write the consolidated bull / bear / risks prose, sourcing from each
   lens (cite analyst names where relevant).

The four `LensView` objects from the analysts are passed through verbatim
into the final `EntryExitPlan.lenses`; the judge's job is synthesis, not
rewriting.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from anthropic import Anthropic

from app.analysis.digest import PanelDigest
from app.analysis.schema import CONFIDENCE_LEVELS
from app.config import load_env
from app.logging import get_logger
from app.research.agents.base import (
    SONNET_PRICE_IN,
    SONNET_PRICE_OUT,
    AgentResult,
    _compute_cost,
    _extract_tool_input,
)
from app.research.context import ResearchPacket
from app.research.rr_distribution import compute_rr_distribution, risk_reward
from app.research.schema import (
    AgentNote,
    CONFIDENCE,
    CandidateLevels,
    EntryExitPlan,
    LensView,
    MAX_CASE_ITEMS,
    MAX_POINT_CHARS,
    Picks,
    TIMEFRAMES,
    ZoneBand,
    blended_risk_reward,
)

log = get_logger(__name__)


JUDGE_TOOL_NAME = "submit_synthesis"
JUDGE_TOOL_DESCRIPTION = (
    "Submit the synthesised entry/exit plan after weighing the four lens "
    "outputs. Pick levels by REFERENCE: `entry_kind` is 'breakout' or "
    "'pullback' (matching the candidate_levels block in the user message); "
    "`primary_exit_index`, `runner_exit_index` (optional), and "
    "`invalidation_index` are 0-based indices into the corresponding "
    "candidate lists. Provide a short rationale per pick. You do NOT emit "
    "raw price numbers — the system resolves your picks against the "
    "deterministic candidates."
)


SYSTEM_PROMPT = """\
You are the Judge on a four-lens swing-trading research panel. You receive \
four independent lens reads (Quantitative, Fundamental, Sentiment-Macro, \
Contrarian-Risk) plus the deterministic candidate levels. You produce ONE \
synthesised plan.

Hard rules:
1. NUMERIC LEVELS: You DO NOT emit raw prices. Pick by reference: \
   `entry_kind` is 'breakout' or 'pullback'; `primary_exit_index`, \
   `runner_exit_index` (optional), `invalidation_index` are 0-based \
   indices into the candidate_levels lists in the user message. The \
   system resolves your picks against the deterministic candidates.
2. CONFIDENCE: bounded by the upstream rubric. If rubric_status_confidence is \
   'low', your `confidence` may not exceed 'medium'. The lens convictions \
   advise; the rubric ceilings.
3. WEIGH THE LENSES. If three lenses are bullish and Contrarian-Risk is \
   bearish, the right read is often 'medium conviction with a tight invalidation' \
   — not 'high conviction' (that ignores risk) or 'low conviction' (that \
   over-weights one dissenter).
4. BULL / BEAR / KEY-RISKS: 3-5 bullets each. Each bullet should ideally \
   reference WHICH lens raised it ('per Quant: ...', 'per Fundamental: ...').
5. NEVER use 'buy' / 'sell' / 'recommendation' / 'guarantee' / 'will'. Use \
   'research zone', 'consider', 'may'.
6. RATIONALE on each pick should be ≤ 25 words.
7. VALUATION. The user message carries a valuation block with explicit units. \
   Where it is present, reconcile it against the technical read: say plainly \
   whether the price already embeds the bull case. Cite the specific field \
   and number you are relying on (e.g. "forward_pe 41 vs peers.median_forward_pe \
   18"). Never restate a number without naming its field. If no peer block is \
   present, say the multiple cannot be benchmarked rather than comparing it to \
   a remembered average. If the block says valuation is unavailable, say so \
   rather than inferring it.
"""


USER_TEMPLATE = """\
Ticker: {ticker}
As of: {as_of}

Status (deterministic): {status} (rubric_status_confidence: {status_confidence})
Setup: {setup_type} (confidence: {setup_confidence})
Action label (derived): {action_label}
Style fit: {primary_style}

Snapshot:
- last_close: {last_close}
- ATR(14): {atr_14}
- pct off 52w high / low: {pct_off_52w_high} / {pct_off_52w_low}

Valuation (units stated per line; do not assume a convention):
{valuation_block}

CANDIDATE LEVELS (pick BY INDEX / KIND — the system resolves your picks):
{candidate_levels_block}

LENS PANEL — four independent reads:
{lenses_block}

Synthesise via submit_synthesis. Pick the final entry kind, primary exit \
index, optional runner exit index, and invalidation index; decide \
confidence + timeframe; and write 3-5 bullets each of bull_case / \
bear_case / key_risks.
"""


def judge_tool_input_schema() -> dict[str, Any]:
    """Strict schema. The judge picks levels by *reference* — entry by kind,
    exits and invalidation by index. No raw numbers; no scaling. Lenses
    come from the analysts and are passed through; this schema is
    intentionally identical to Quick mode's MINUS the lens block.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "entry_kind",
            "entry_rationale",
            "primary_exit_index",
            "primary_exit_rationale",
            "invalidation_index",
            "invalidation_rationale",
            "confidence",
            "timeframe",
            "bull_case",
            "bear_case",
            "key_risks",
        ],
        "properties": {
            "entry_kind": {"type": "string", "enum": ["breakout", "pullback"]},
            "entry_rationale": {"type": "string", "maxLength": MAX_POINT_CHARS},
            "primary_exit_index": {"type": "integer", "minimum": 0},
            "primary_exit_rationale": {"type": "string", "maxLength": MAX_POINT_CHARS},
            "runner_exit_index": {"type": "integer", "minimum": 0},
            "runner_exit_rationale": {"type": "string", "maxLength": MAX_POINT_CHARS},
            "invalidation_index": {"type": "integer", "minimum": 0},
            "invalidation_rationale": {"type": "string", "maxLength": MAX_POINT_CHARS},
            "confidence": {"type": "string", "enum": list(CONFIDENCE)},
            "timeframe": {"type": "string", "enum": list(TIMEFRAMES)},
            "bull_case": {
                "type": "array",
                "items": {"type": "string", "maxLength": MAX_POINT_CHARS},
                "minItems": 1,
                "maxItems": MAX_CASE_ITEMS,
            },
            "bear_case": {
                "type": "array",
                "items": {"type": "string", "maxLength": MAX_POINT_CHARS},
                "minItems": 1,
                "maxItems": MAX_CASE_ITEMS,
            },
            "key_risks": {
                "type": "array",
                "items": {"type": "string", "maxLength": MAX_POINT_CHARS},
                "minItems": 1,
                "maxItems": MAX_CASE_ITEMS,
            },
            "note": {"type": "string"},
        },
    }


@dataclass
class JudgeResult:
    plan: EntryExitPlan
    cost_usd: float
    duration_ms: int
    raw: dict[str, Any]


def run_judge(
    *,
    packet: ResearchPacket,
    lenses: list[LensView],
    analyst_results: list[AgentResult],
    client: Anthropic | None = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 3500,
    digest: PanelDigest | None = None,
) -> JudgeResult:
    """Call Sonnet to synthesise. Returns a fully-built `EntryExitPlan`."""
    if client is None:
        env = load_env()
        if not env.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set; cannot run judge.")
        client = Anthropic(api_key=env.anthropic_api_key)

    user_msg = _format_user_message(packet, lenses, digest=digest)
    started = time.monotonic()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "name": JUDGE_TOOL_NAME,
                "description": JUDGE_TOOL_DESCRIPTION,
                "input_schema": judge_tool_input_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": JUDGE_TOOL_NAME},
        messages=[{"role": "user", "content": user_msg}],
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    cost = _compute_cost(
        response, price_in=SONNET_PRICE_IN, price_out=SONNET_PRICE_OUT
    )
    raw = _extract_tool_input(response, tool_name=JUDGE_TOOL_NAME)
    if raw is None:
        raise RuntimeError(f"No {JUDGE_TOOL_NAME} tool_use block in response.")

    # Sum analyst costs + judge cost = total Deep cost.
    total_cost = cost + sum(a.cost_usd for a in analyst_results)
    total_duration = duration_ms + max(
        (a.duration_ms for a in analyst_results), default=0
    )

    plan = _build_plan(
        packet=packet,
        raw=raw,
        lenses=lenses,
        analyst_results=analyst_results,
        cost_usd=total_cost,
        duration_ms=total_duration,
    )
    return JudgeResult(plan=plan, cost_usd=cost, duration_ms=duration_ms, raw=raw)


# ---------------------------------------------------------------------------
# Prompt formatting


def _format_user_message(
    packet: ResearchPacket,
    lenses: list[LensView],
    *,
    digest: PanelDigest | None = None,
) -> str:
    v = packet.view
    return USER_TEMPLATE.format(
        ticker=packet.ticker,
        as_of=packet.as_of.isoformat(),
        status=v.status.status,
        status_confidence=v.status.confidence,
        setup_type=v.setup.setup_type,
        setup_confidence=v.setup.confidence,
        action_label=v.action.label,
        primary_style=v.style_fit.primary_style or "—",
        last_close=_fmt(v.market.last_close),
        atr_14=_fmt(v.indicators.atr_14),
        pct_off_52w_high=_fmt_pct(v.market.pct_off_52w_high),
        pct_off_52w_low=_fmt_pct(v.market.pct_off_52w_low),
        valuation_block=_valuation_block_for(digest),
        candidate_levels_block=_format_candidate_levels(packet),
        lenses_block=_format_lenses(lenses),
    )


def _valuation_block_for(digest: PanelDigest | None) -> str:
    """Render the digest's valuation measures, naming what was unavailable.

    A reader that sees only present fields cannot tell a missing multiple
    from a healthy one, so absent fields are listed explicitly.
    """
    if digest is None:
        return "(valuation context not available for this run)"

    block = digest.valuation_block()

    peer_rows = [m for m in digest.measures if m.field.startswith("peers.")]
    if peer_rows:
        lines = [
            "",
            "Peer group (use these as the comparison anchor — do not"
            " substitute a remembered sector average):",
        ]
        for m in peer_rows:
            name = m.field.split(".", 1)[1]
            rendered = f"{m.value:,.4g}" if isinstance(m.value, float) else str(m.value)
            lines.append(f"- {name}: {rendered} ({m.unit})")
        block = block + "\n" + "\n".join(lines)

    absent = [
        f.split(".", 1)[1] for f in digest.missing if f.startswith("valuation.")
    ]
    if absent:
        block = f"{block}\n(unavailable, do not infer: {', '.join(absent)})"
    return block


def _format_candidate_levels(p: ResearchPacket) -> str:
    cl = p.candidate_levels
    parts: list[str] = []
    if cl.breakout_entry:
        parts.append(f"- entry_kind=breakout: {_format_zone(cl.breakout_entry)}")
    if cl.pullback_entry:
        parts.append(f"- entry_kind=pullback: {_format_zone(cl.pullback_entry)}")
    if cl.primary_exit_candidates:
        parts.append("- primary_exit_candidates (pick by primary_exit_index):")
        for i, z in enumerate(cl.primary_exit_candidates):
            parts.append(f"    [{i}] {_format_zone(z)}")
    if cl.runner_exit_candidates:
        parts.append("- runner_exit_candidates (pick by runner_exit_index, optional):")
        for i, z in enumerate(cl.runner_exit_candidates):
            parts.append(f"    [{i}] {_format_zone(z)}")
    if cl.invalidation_candidates:
        parts.append("- invalidation_candidates (pick by invalidation_index):")
        for i, x in enumerate(cl.invalidation_candidates):
            parts.append(f"    [{i}] {x:.2f}")
    return "\n".join(parts) if parts else "(no deterministic candidates available)"


def _format_zone(z: ZoneBand) -> str:
    return f"[{z.low:.2f} – {z.high:.2f}] · {z.method}"


def _format_lenses(lenses: list[LensView]) -> str:
    if not lenses:
        return "(no lens data — all analysts failed)"
    parts: list[str] = []
    for lens in lenses:
        parts.append(
            f"- [{lens.name}] direction={lens.direction} · conviction={lens.conviction}"
        )
        parts.append(f"    summary: {lens.summary}")
        for p in lens.points:
            parts.append(f"    · {p}")
    return "\n".join(parts)


def _fmt(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _fmt_pct(value) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.2f}%"


def _as_list(value: Any) -> list[str]:
    """Wrap a bare string into [string]; pass lists through. Defends against
    the same str→list[char] artifact that the schema's field_validator
    catches; used here for AgentNote fields that aren't Pydantic-validated.
    """
    if isinstance(value, str):
        return [value]
    if value is None:
        return []
    return [str(v) for v in value]


# ---------------------------------------------------------------------------
# Plan assembly — categorical picks resolved against deterministic candidates.


def _build_plan(
    *,
    packet: ResearchPacket,
    raw: dict[str, Any],
    lenses: list[LensView],
    analyst_results: list[AgentResult],
    cost_usd: float,
    duration_ms: int,
) -> EntryExitPlan:
    cl = packet.candidate_levels
    picks = _resolve_picks(raw, cl)

    entry_zone = _entry_zone(cl, picks.entry_kind)
    exit_zone_primary = cl.primary_exit_candidates[picks.primary_index]
    exit_zone_runner = (
        cl.runner_exit_candidates[picks.runner_index]
        if picks.runner_index is not None
        else None
    )
    # Bug 3 (2026-05-28): import the same post-LLM snap used by Quick mode so
    # Deep's judge can't return a long-side stop inside or above the entry zone.
    from app.research.quick import _resolve_invalidation
    invalidation = _resolve_invalidation(cl=cl, picks=picks, entry_zone=entry_zone)

    confidence = _bound_confidence(
        raw_confidence=raw["confidence"],
        rubric_confidence=packet.view.status.confidence,
    )

    rr_primary = risk_reward(entry_zone, exit_zone_primary, invalidation)
    rr_runner = (
        risk_reward(entry_zone, exit_zone_runner, invalidation)
        if exit_zone_runner is not None
        else None
    )
    rr_blended = blended_risk_reward(rr_primary=rr_primary, rr_runner=rr_runner)

    distribution = compute_rr_distribution(cl, picks)

    # Audit trail: one AgentNote per analyst + the judge.
    trace: list[AgentNote] = []
    for ar in analyst_results:
        if ar.lens is None:
            trace.append(
                AgentNote(
                    agent=ar.agent_name,
                    confidence="low",
                    note=f"FAILED: {ar.error or 'no lens emitted'}",
                )
            )
        else:
            trace.append(
                AgentNote(
                    agent=ar.agent_name,
                    confidence=ar.lens.conviction,
                    bull_points=[
                        p for p in ar.lens.points if ar.lens.direction == "bullish"
                    ],
                    bear_points=[
                        p for p in ar.lens.points if ar.lens.direction == "bearish"
                    ],
                    note=ar.lens.summary,
                )
            )
    trace.append(
        AgentNote(
            agent="judge",
            confidence=confidence,
            bull_points=_as_list(raw.get("bull_case", [])),
            bear_points=_as_list(raw.get("bear_case", [])),
            note=raw.get("note", ""),
        )
    )

    sources = list(packet.sources_used)
    if (
        packet.view.valuation.forward_pe is not None
        or packet.view.valuation.market_cap is not None
    ):
        sources.append("fundamentals")

    return EntryExitPlan(
        ticker=packet.ticker,
        as_of=packet.as_of,
        entry_zone=entry_zone,
        pullback_entry_zone=None,
        exit_zone_primary=exit_zone_primary,
        exit_zone_runner=exit_zone_runner,
        invalidation=invalidation,
        risk_reward_primary=rr_primary,
        risk_reward_runner=rr_runner,
        plan_r_r_blended=rr_blended,
        r_r_distribution=distribution,
        confidence=confidence,
        timeframe=raw["timeframe"],
        bull_case=raw.get("bull_case", []),
        bear_case=raw.get("bear_case", []),
        key_risks=raw.get("key_risks", []),
        lenses=lenses,
        mode="deep",
        cost_usd=round(cost_usd, 6),
        duration_ms=duration_ms,
        sources_used=sources,
        agent_trace=trace,
    )


def _resolve_picks(raw: dict[str, Any], cl: CandidateLevels) -> Picks:
    """Validate + clamp categorical picks. Falls back to whatever's available."""
    entry_kind = raw.get("entry_kind", "breakout")
    if entry_kind not in ("breakout", "pullback"):
        entry_kind = "breakout"
    if entry_kind == "breakout" and cl.breakout_entry is None:
        entry_kind = "pullback"
    elif entry_kind == "pullback" and cl.pullback_entry is None:
        entry_kind = "breakout"
    if cl.breakout_entry is None and cl.pullback_entry is None:
        raise RuntimeError("No entry candidates available; cannot build plan.")

    primary_idx = _clamp_index(
        raw.get("primary_exit_index", 0), len(cl.primary_exit_candidates)
    )
    runner_raw = raw.get("runner_exit_index")
    if runner_raw is None or not cl.runner_exit_candidates:
        runner_idx: int | None = None
    else:
        runner_idx = _clamp_index(runner_raw, len(cl.runner_exit_candidates))
    inv_idx = _clamp_index(
        raw.get("invalidation_index", 0), len(cl.invalidation_candidates)
    )

    return Picks(
        entry_kind=entry_kind,
        primary_index=primary_idx,
        runner_index=runner_idx,
        invalidation_index=inv_idx,
    )


def _entry_zone(cl: CandidateLevels, entry_kind: str) -> ZoneBand:
    if entry_kind == "breakout" and cl.breakout_entry is not None:
        return cl.breakout_entry
    if cl.pullback_entry is not None:
        return cl.pullback_entry
    raise RuntimeError("No matching entry candidate")


def _clamp_index(value: int, n: int) -> int:
    if n <= 0:
        raise RuntimeError("No candidates to pick from.")
    return max(0, min(int(value), n - 1))


def _bound_confidence(*, raw_confidence: str, rubric_confidence: str) -> str:
    if raw_confidence not in CONFIDENCE:
        raw_confidence = "low"
    if rubric_confidence not in CONFIDENCE_LEVELS:
        rubric_confidence = "low"
    levels = list(CONFIDENCE)
    return levels[min(levels.index(raw_confidence), levels.index(rubric_confidence))]


__all__ = ["JudgeResult", "judge_tool_input_schema", "run_judge"]
