"""Judge agent — synthesises the four analyst lenses into a final
`EntryExitPlan` (Sonnet 4.6).

The judge does NOT re-litigate each lens; it accepts each analyst's read
as authoritative within that analyst's discipline. Its job is to:

1. Pick the final numeric levels from the deterministic candidates (same
   ±15% clamp + minimum-risk-distance guard as Quick mode).
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
from app.research.schema import (
    AgentNote,
    CONFIDENCE,
    EntryExitPlan,
    LensView,
    TIMEFRAMES,
    ZoneBand,
    blended_risk_reward,
)

log = get_logger(__name__)


# Same level-clamp + minimum-risk constants as Quick mode. Reused so Quick
# and Deep emit comparable numeric levels for the same setup.
LEVEL_EDIT_TOLERANCE = 0.15
MIN_RISK_ATR_MULTIPLE = 0.75

JUDGE_TOOL_NAME = "submit_synthesis"
JUDGE_TOOL_DESCRIPTION = (
    "Submit the synthesised entry/exit plan after weighing the four lens "
    "outputs. Pick numeric levels from the candidate lists; ±15% scaling "
    "with rationale is allowed but no fresh inventions."
)


SYSTEM_PROMPT = """\
You are the Judge on a four-lens swing-trading research panel. You receive \
four independent lens reads (Quantitative, Fundamental, Sentiment-Macro, \
Contrarian-Risk) plus the deterministic candidate levels. You produce ONE \
synthesised plan.

Hard rules:
1. NUMERIC LEVELS: Pick from the candidate_levels lists in the user message. \
   You may scale any chosen level by at most ±15% with rationale. Do not \
   invent levels that are not anchored to the candidates.
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
6. RATIONALE on each ZoneBand should be ≤ 25 words.
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

CANDIDATE LEVELS (pick from these, ±15% scaling allowed with rationale):
{candidate_levels_block}

LENS PANEL — four independent reads:
{lenses_block}

Synthesise via submit_synthesis. Pick the final entry / pullback / exit / \
runner / invalidation, decide confidence + timeframe, and write 3-5 bullets \
each of bull_case / bear_case / key_risks.
"""


def judge_tool_input_schema() -> dict[str, Any]:
    """Strict schema for the synthesis tool. Mirrors Quick mode's plan
    schema minus the per-lens block (lenses come from the analysts).
    """
    zone = {
        "type": "object",
        "additionalProperties": False,
        "required": ["low", "high", "method", "rationale"],
        "properties": {
            "low": {"type": "number"},
            "high": {"type": "number"},
            "method": {"type": "string"},
            "rationale": {"type": "string"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "entry_zone",
            "exit_zone_primary",
            "invalidation",
            "confidence",
            "timeframe",
            "bull_case",
            "bear_case",
            "key_risks",
        ],
        "properties": {
            "entry_zone": zone,
            "pullback_entry_zone": zone,
            "exit_zone_primary": zone,
            "exit_zone_runner": zone,
            "invalidation": {"type": "number"},
            "confidence": {"type": "string", "enum": list(CONFIDENCE)},
            "timeframe": {"type": "string", "enum": list(TIMEFRAMES)},
            "bull_case": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 8,
            },
            "bear_case": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 8,
            },
            "key_risks": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 8,
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
) -> JudgeResult:
    """Call Sonnet to synthesise. Returns a fully-built `EntryExitPlan`."""
    if client is None:
        env = load_env()
        if not env.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set; cannot run judge.")
        client = Anthropic(api_key=env.anthropic_api_key)

    user_msg = _format_user_message(packet, lenses)
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


def _format_user_message(packet: ResearchPacket, lenses: list[LensView]) -> str:
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
        candidate_levels_block=_format_candidate_levels(packet),
        lenses_block=_format_lenses(lenses),
    )


def _format_candidate_levels(p: ResearchPacket) -> str:
    cl = p.candidate_levels
    parts: list[str] = []
    if cl.breakout_entry:
        parts.append(f"- breakout_entry: {_format_zone(cl.breakout_entry)}")
    if cl.pullback_entry:
        parts.append(f"- pullback_entry: {_format_zone(cl.pullback_entry)}")
    if cl.primary_exit_candidates:
        parts.append("- primary_exit_candidates:")
        for z in cl.primary_exit_candidates:
            parts.append(f"    • {_format_zone(z)}")
    if cl.runner_exit_candidates:
        parts.append("- runner_exit_candidates:")
        for z in cl.runner_exit_candidates:
            parts.append(f"    • {_format_zone(z)}")
    if cl.invalidation_candidates:
        parts.append(
            "- invalidation_candidates: "
            + ", ".join(f"{x:.2f}" for x in cl.invalidation_candidates)
        )
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


# ---------------------------------------------------------------------------
# Plan assembly (mirrors Quick mode's clamping for parity)


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

    entry_zone = _clamp_zone(raw["entry_zone"], _all_entry_candidates(cl))
    pullback_entry_zone = (
        _clamp_zone(raw["pullback_entry_zone"], _all_entry_candidates(cl))
        if raw.get("pullback_entry_zone")
        else None
    )
    exit_zone_primary = _clamp_zone(raw["exit_zone_primary"], cl.primary_exit_candidates)
    exit_zone_runner = (
        _clamp_zone(raw["exit_zone_runner"], cl.runner_exit_candidates)
        if raw.get("exit_zone_runner")
        else None
    )
    invalidation = _clamp_invalidation(raw["invalidation"], cl.invalidation_candidates)

    # Minimum-risk-distance guard (same as Quick).
    atr = cl.atr_14
    if atr is not None and atr > 0:
        floor = entry_zone.low - MIN_RISK_ATR_MULTIPLE * atr
        if invalidation > floor:
            log.info(
                "research.judge.invalidation_floor",
                raw=invalidation,
                floored=floor,
                atr=atr,
                entry_low=entry_zone.low,
            )
            invalidation = floor

    confidence = _bound_confidence(
        raw_confidence=raw["confidence"],
        rubric_confidence=packet.view.status.confidence,
    )

    rr_primary = _risk_reward(entry_zone, exit_zone_primary, invalidation)
    rr_runner = (
        _risk_reward(entry_zone, exit_zone_runner, invalidation)
        if exit_zone_runner is not None
        else None
    )
    rr_blended = blended_risk_reward(rr_primary=rr_primary, rr_runner=rr_runner)

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
            bull_points=list(raw.get("bull_case", [])),
            bear_points=list(raw.get("bear_case", [])),
            note=raw.get("note", ""),
        )
    )

    sources = list(packet.sources_used)
    if packet.view.valuation.forward_pe is not None or packet.view.valuation.market_cap is not None:
        sources.append("fundamentals")

    return EntryExitPlan(
        ticker=packet.ticker,
        as_of=packet.as_of,
        entry_zone=entry_zone,
        pullback_entry_zone=pullback_entry_zone,
        exit_zone_primary=exit_zone_primary,
        exit_zone_runner=exit_zone_runner,
        invalidation=invalidation,
        risk_reward_primary=rr_primary,
        risk_reward_runner=rr_runner,
        plan_r_r_blended=rr_blended,
        confidence=confidence,
        timeframe=raw["timeframe"],
        bull_case=list(raw.get("bull_case", [])),
        bear_case=list(raw.get("bear_case", [])),
        key_risks=list(raw.get("key_risks", [])),
        lenses=lenses,
        mode="deep",
        cost_usd=round(cost_usd, 6),
        duration_ms=duration_ms,
        sources_used=sources,
        agent_trace=trace,
    )


def _all_entry_candidates(cl: Any) -> list[ZoneBand]:
    out: list[ZoneBand] = []
    if cl.breakout_entry:
        out.append(cl.breakout_entry)
    if cl.pullback_entry:
        out.append(cl.pullback_entry)
    return out


def _clamp_zone(raw: dict[str, Any], candidates: list[ZoneBand]) -> ZoneBand:
    z = ZoneBand(
        low=float(raw["low"]),
        high=float(raw["high"]),
        method=str(raw.get("method", "")),
        rationale=str(raw.get("rationale", "")),
    )
    if not candidates:
        return z
    nearest = min(
        candidates,
        key=lambda c: abs((c.low + c.high) / 2 - (z.low + z.high) / 2),
    )
    z.low = _clamp_value(z.low, nearest.low)
    z.high = _clamp_value(z.high, nearest.high)
    if z.low > z.high:
        z.low, z.high = z.high, z.low
    return z


def _clamp_value(value: float, anchor: float) -> float:
    if anchor == 0:
        return value
    lo = anchor * (1 - LEVEL_EDIT_TOLERANCE)
    hi = anchor * (1 + LEVEL_EDIT_TOLERANCE)
    return max(lo, min(hi, value))


def _clamp_invalidation(value: float, candidates: list[float]) -> float:
    if not candidates:
        return float(value)
    nearest = min(candidates, key=lambda c: abs(c - value))
    return _clamp_value(float(value), nearest)


def _bound_confidence(*, raw_confidence: str, rubric_confidence: str) -> str:
    if raw_confidence not in CONFIDENCE:
        raw_confidence = "low"
    if rubric_confidence not in CONFIDENCE_LEVELS:
        rubric_confidence = "low"
    levels = list(CONFIDENCE)
    return levels[min(levels.index(raw_confidence), levels.index(rubric_confidence))]


def _risk_reward(entry: ZoneBand, exit_: ZoneBand, invalidation: float) -> float:
    risk = entry.low - invalidation
    if risk <= 0:
        return 0.0
    reward = exit_.low - entry.high
    if reward <= 0:
        return 0.0
    return round(reward / risk, 2)


__all__ = ["JudgeResult", "judge_tool_input_schema", "run_judge"]
