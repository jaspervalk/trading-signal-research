"""Quick-mode entry/exit research.

A single Claude call. Receives a `ResearchPacket` (technicals + recent
claims + deterministic candidate levels) and returns an `EntryExitPlan`
with structured zones + qualitative reasoning.

Hallucination guards:
- LLM picks among the deterministic candidate levels; it cannot invent a
  level that isn't on the list.
- The LLM may scale a chosen level by ±15% with rationale; wider edits
  are clamped to the boundary on parse and the rationale is preserved.
- `confidence` is bounded by the upstream `DecisionSupportStatus.confidence`:
  if the rubric says "low", the plan's confidence is at most "medium".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from anthropic import Anthropic

from app.analysis.schema import CONFIDENCE_LEVELS
from app.config import load_env
from app.logging import get_logger
from app.research.context import ResearchPacket
from app.research.schema import (
    AgentNote,
    CONFIDENCE,
    EntryExitPlan,
    LENS_NAMES,
    LensView,
    TIMEFRAMES,
    ZoneBand,
    blended_risk_reward,
)

log = get_logger(__name__)

# Anthropic Haiku 4.5 pricing in USD per million tokens.
HAIKU_PRICE_IN = 1.0
HAIKU_PRICE_OUT = 5.0

LEVEL_EDIT_TOLERANCE = 0.15  # LLM may scale a chosen level by ±15%

QUICK_TOOL_NAME = "submit_entry_exit_plan"
QUICK_TOOL_DESCRIPTION = (
    "Submit a structured entry/exit research plan for the ticker. "
    "Pick numeric levels from the candidate lists provided in the user message — "
    "do not invent levels. You may scale a chosen level by at most ±15% with rationale."
)


SYSTEM_PROMPT = """\
You are a disciplined swing-trading analyst working from a structured research \
packet. Your job is to produce one entry/exit plan and a 4-lens analyst panel, \
NOT a recommendation. The final user pulls the trigger.

Hard rules:
1. NUMERIC LEVELS: Pick from the candidate_levels lists in the user message. \
   You may scale any chosen level by at most ±15% with rationale. Do not \
   invent levels not anchored to the candidates.
2. CONFIDENCE: bounded by the upstream rubric. If rubric_status_confidence is \
   'low', your `confidence` may not exceed 'medium'.
3. TIMEFRAME: pick from {1-3d, 5-15d, 2-6w} based on the setup_type and ATR.
4. EVIDENCE: bull_case / bear_case / key_risks each get 2-4 short bullet \
   points grounded in the packet. Use specific numbers from the packet \
   when relevant. Do not invent news or earnings dates.
5. NEVER use 'buy' / 'sell' / 'recommendation' / 'guarantee' / 'will'. Use \
   'research zone', 'invalidation reference', 'consider', 'may'.
6. RATIONALE on each ZoneBand should be ≤ 25 words and reference what made \
   you choose this candidate over the others.

LENSES — ALWAYS emit ALL FOUR (quantitative, fundamental, sentiment_macro, \
contrarian_risk). Each is INDEPENDENT and rates the trade through its own \
discipline. Do not let one lens crowd out another.
- quantitative: statistical edge, momentum/mean-reversion regime, ATR-relative \
  positioning, factor exposure (size, value, momentum, vol).
- fundamental: forward earnings, revenue/margin trajectory, valuation context \
  vs sector / vs growth (use the valuation block in the packet). Do NOT mix \
  this into R/R math; it stays a separate axis.
- sentiment_macro: creator claims (provided in packet), positioning / flows, \
  macro regime, near-term catalyst proximity (earnings, sector news).
- contrarian_risk: what could go wrong, crowded-trade signals, single \
  contradicting indicator that would falsify the bull case, downside scenario \
  in R units.

Each lens: `direction` ('bullish'/'bearish'/'neutral'), `conviction` \
(low/medium/high), one-line `summary`, 2-4 supporting `points`. The four lenses \
together should let the user compare competing reads side-by-side, not collapse \
them into a verdict.
"""


USER_TEMPLATE = """\
Ticker: {ticker}
As of: {as_of}

Status (deterministic): {status} (confidence: {status_confidence})
Setup: {setup_type} (confidence: {setup_confidence})
Action label (derived): {action_label} — {action_derivation}
Style fit: {primary_style}

Snapshot:
- last_close: {last_close}
- ATR(14): {atr_14}
- ATR(14)%: {atr_14_pct}
- RSI(14): {rsi_14}
- 5d / 21d / 63d returns: {return_5d} / {return_21d} / {return_63d}
- pct off 52w high: {pct_off_52w_high}
- pct off 52w low: {pct_off_52w_low}

Levels:
- nearest support: {nearest_support}
- nearest resistance: {nearest_resistance}
- recent_high_63d: {recent_high_63d}
- base_low: {base_low}
- pullback from recent high: {pullback_pct_from_recent_high}
- breakout distance: {breakout_distance_pct}

VALUATION (context for the Fundamental lens — does NOT feed into R/R math):
{valuation_block}

CANDIDATE LEVELS (pick from these, ±15% scaling allowed with rationale):
{candidate_levels_block}

RECENT CLAIMS ({n_claims}, last 30d, accepted only):
{claims_block}

Produce one entry/exit plan via submit_entry_exit_plan, including ALL FOUR \
lens views (quantitative, fundamental, sentiment_macro, contrarian_risk).
"""


def quick_tool_input_schema() -> dict[str, Any]:
    """JSON schema for the Anthropic tool. Strict — extra fields rejected."""
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
    lens = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "direction", "conviction", "summary", "points"],
        "properties": {
            "name": {"type": "string", "enum": list(LENS_NAMES)},
            "direction": {
                "type": "string",
                "enum": ["bullish", "bearish", "neutral"],
            },
            "conviction": {"type": "string", "enum": list(CONFIDENCE)},
            "summary": {"type": "string"},
            "points": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 4,
            },
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
            "lenses",
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
                "maxItems": 6,
            },
            "bear_case": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 6,
            },
            "key_risks": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 6,
            },
            "lenses": {
                "type": "array",
                "items": lens,
                "minItems": 4,
                "maxItems": 4,
            },
            "note": {"type": "string"},
        },
    }


@dataclass
class QuickResult:
    plan: EntryExitPlan
    raw_response: dict[str, Any]


class _ClientLike(Protocol):
    """Anthropic-shaped interface — kept minimal so tests can inject."""

    def messages(self) -> Any: ...


MIN_RISK_ATR_MULTIPLE = 0.75  # invalidation must be ≥ 0.75×ATR from entry low


def run(
    packet: ResearchPacket,
    *,
    client: Anthropic | None = None,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 4000,
) -> QuickResult:
    """Run quick research. Returns the plan + raw model response.

    Tests inject a `client` whose `messages.create()` returns a canned
    Anthropic-shape response. Production constructs its own client from
    `ANTHROPIC_API_KEY`.
    """
    if client is None:
        env = load_env()
        if not env.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set; cannot run quick research without one."
            )
        client = Anthropic(api_key=env.anthropic_api_key)

    user_msg = _format_user_message(packet)
    started = time.monotonic()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "name": QUICK_TOOL_NAME,
                "description": QUICK_TOOL_DESCRIPTION,
                "input_schema": quick_tool_input_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": QUICK_TOOL_NAME},
        messages=[{"role": "user", "content": user_msg}],
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    raw_input = _extract_tool_input(response)
    cost = _compute_cost(response)
    plan = _build_plan(
        packet=packet,
        raw=raw_input,
        cost_usd=cost,
        duration_ms=duration_ms,
    )
    raw_dict = response.model_dump() if hasattr(response, "model_dump") else dict(response)
    return QuickResult(plan=plan, raw_response=raw_dict)


# ---------------------------------------------------------------------------
# Prompt formatting


def _format_user_message(p: ResearchPacket) -> str:
    v = p.view
    candidate_levels_block = _format_candidate_levels(p)
    claims_block = _format_claims(p)
    valuation_block = _format_valuation(p)
    return USER_TEMPLATE.format(
        ticker=p.ticker,
        as_of=p.as_of.isoformat(),
        status=v.status.status,
        status_confidence=v.status.confidence,
        setup_type=v.setup.setup_type,
        setup_confidence=v.setup.confidence,
        action_label=v.action.label,
        action_derivation=v.action.derivation,
        primary_style=v.style_fit.primary_style or "—",
        last_close=_fmt(v.market.last_close),
        atr_14=_fmt(v.indicators.atr_14),
        atr_14_pct=_fmt_pct(v.indicators.atr_14_pct),
        rsi_14=_fmt(v.indicators.rsi_14, 0),
        return_5d=_fmt_pct(v.market.return_5d),
        return_21d=_fmt_pct(v.market.return_21d),
        return_63d=_fmt_pct(v.market.return_63d),
        pct_off_52w_high=_fmt_pct(v.market.pct_off_52w_high),
        pct_off_52w_low=_fmt_pct(v.market.pct_off_52w_low),
        nearest_support=_fmt(v.levels.nearest_support),
        nearest_resistance=_fmt(v.levels.nearest_resistance),
        recent_high_63d=_fmt(v.levels.recent_high_63d),
        base_low=_fmt(v.levels.base_low),
        pullback_pct_from_recent_high=_fmt_pct(v.levels.pullback_pct_from_recent_high),
        breakout_distance_pct=_fmt_pct(v.levels.breakout_distance_pct),
        valuation_block=valuation_block,
        candidate_levels_block=candidate_levels_block,
        n_claims=len(p.recent_claims),
        claims_block=claims_block or "(none in lookback window)",
    )


def _format_valuation(p: ResearchPacket) -> str:
    val = p.view.valuation
    parts: list[str] = []
    if val.sector or val.industry:
        parts.append(f"sector={val.sector or '—'} · industry={val.industry or '—'}")
    if val.market_cap is not None:
        parts.append(f"market_cap={_fmt_dollar_volume(val.market_cap)}")
    if val.forward_pe is not None:
        parts.append(f"forward_PE={val.forward_pe:.1f}")
    if val.trailing_pe is not None:
        parts.append(f"trailing_PE={val.trailing_pe:.1f}")
    if val.peg_ratio is not None:
        parts.append(f"PEG={val.peg_ratio:.2f}")
    if val.price_to_sales_ttm is not None:
        parts.append(f"P/S_ttm={val.price_to_sales_ttm:.1f}")
    if val.earnings_growth_forward is not None:
        parts.append(f"earnings_growth_fwd={val.earnings_growth_forward * 100:+.1f}%")
    if val.revenue_growth_yoy is not None:
        parts.append(f"revenue_growth_yoy={val.revenue_growth_yoy * 100:+.1f}%")
    if val.profit_margins is not None:
        parts.append(f"profit_margin={val.profit_margins * 100:+.1f}%")
    if val.short_pct_of_float is not None:
        parts.append(f"short_pct_float={val.short_pct_of_float * 100:.1f}%")
    if val.float_shares is not None:
        parts.append(f"float={_fmt_dollar_volume(val.float_shares)}")
    if val.beta is not None:
        parts.append(f"beta={val.beta:.2f}")
    if val.days_to_next_earnings is not None:
        parts.append(f"days_to_next_earnings={val.days_to_next_earnings}")
    return " · ".join(parts) if parts else "(no valuation data — likely an ETF or pre-IPO ticker)"


def _fmt_dollar_volume(value: float) -> str:
    if value >= 1e12:
        return f"${value / 1e12:.1f}T"
    if value >= 1e9:
        return f"${value / 1e9:.1f}B"
    if value >= 1e6:
        return f"${value / 1e6:.1f}M"
    return f"${value:.0f}"


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


def _format_claims(p: ResearchPacket) -> str:
    if not p.recent_claims:
        return ""
    parts: list[str] = []
    for c in p.recent_claims:
        creator = c.creator_name or "unknown"
        parts.append(
            f"- [{c.claim_type}/{c.claim_class}/{c.polarity}] "
            f"{c.summary} ({creator}, {c.posted_at.date()}, conf={c.final_confidence:.2f})"
        )
    return "\n".join(parts)


def _fmt(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.2f}%"


# ---------------------------------------------------------------------------
# Response parsing


def _extract_tool_input(response: Any) -> dict[str, Any]:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == QUICK_TOOL_NAME:
            return block.input  # type: ignore[no-any-return]
    raise RuntimeError(f"No {QUICK_TOOL_NAME} tool_use block in response.")


def _compute_cost(response: Any) -> float:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0.0
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    return (in_tok / 1_000_000) * HAIKU_PRICE_IN + (out_tok / 1_000_000) * HAIKU_PRICE_OUT


def _build_plan(
    *,
    packet: ResearchPacket,
    raw: dict[str, Any],
    cost_usd: float,
    duration_ms: int,
) -> EntryExitPlan:
    """Assemble the validated EntryExitPlan, clamping levels and confidence."""
    cl = packet.candidate_levels

    entry_zone = _clamp_zone(raw["entry_zone"], _all_entry_candidates(cl))
    pullback_entry_zone = (
        _clamp_zone(raw["pullback_entry_zone"], _all_entry_candidates(cl))
        if "pullback_entry_zone" in raw and raw["pullback_entry_zone"]
        else None
    )
    exit_zone_primary = _clamp_zone(raw["exit_zone_primary"], cl.primary_exit_candidates)
    exit_zone_runner = (
        _clamp_zone(raw["exit_zone_runner"], cl.runner_exit_candidates)
        if "exit_zone_runner" in raw and raw["exit_zone_runner"]
        else None
    )
    invalidation = _clamp_invalidation(raw["invalidation"], cl.invalidation_candidates)
    # Enforce minimum risk: stop must be ≥ MIN_RISK_ATR_MULTIPLE × ATR below entry low.
    # Without this, an LLM choosing a stop inside / barely below the entry zone
    # produces nonsense R/R (we observed AAPL coming back with R/R 87 once
    # because risk was $0.30 on a $6 ATR stock).
    atr = cl.atr_14
    if atr is not None and atr > 0:
        min_distance = MIN_RISK_ATR_MULTIPLE * atr
        floor = entry_zone.low - min_distance
        if invalidation > floor:
            log.info(
                "research.quick.invalidation_floor",
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

    lenses = _build_lenses(raw.get("lenses"))

    note = AgentNote(
        agent="quick",
        confidence=confidence,
        bull_points=list(raw.get("bull_case", [])),
        bear_points=list(raw.get("bear_case", [])),
        note=raw.get("note", ""),
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
        mode="quick",
        cost_usd=round(cost_usd, 6),
        duration_ms=duration_ms,
        sources_used=sources,
        agent_trace=[note],
    )


def _build_lenses(raw: list[dict[str, Any]] | None) -> list[LensView]:
    """Validate and build LensView list. Tolerates a missing lenses block
    by returning an empty list (lets older / Phase 2 tests pass without
    forcing every fixture to populate four lenses).

    NB: Anthropic does NOT server-side-enforce tool input schemas — the
    model can omit `lenses` even when the schema marks it required. We log
    when this happens so the cache layer can decide to re-run; older
    cached plans without lenses simply render without the panel.
    """
    if not raw:
        log.warning("research.quick.lenses_missing_from_response")
        return []
    out: list[LensView] = []
    for entry in raw:
        try:
            out.append(
                LensView(
                    name=str(entry.get("name", "")),
                    direction=str(entry.get("direction", "neutral")),
                    conviction=str(entry.get("conviction", "low")),
                    summary=str(entry.get("summary", "")),
                    points=[str(p) for p in entry.get("points", [])],
                )
            )
        except Exception as e:  # pragma: no cover — schema validates upstream
            log.warning("research.quick.lens_parse_failed", error=str(e), entry=entry)
    return out


def _all_entry_candidates(cl: Any) -> list[ZoneBand]:
    out: list[ZoneBand] = []
    if cl.breakout_entry:
        out.append(cl.breakout_entry)
    if cl.pullback_entry:
        out.append(cl.pullback_entry)
    return out


def _clamp_zone(raw: dict[str, Any], candidates: list[ZoneBand]) -> ZoneBand:
    """Force `raw.low` / `raw.high` within ±LEVEL_EDIT_TOLERANCE of the
    nearest candidate. If `candidates` is empty, accept the raw band as-is.
    """
    z = ZoneBand(
        low=float(raw["low"]),
        high=float(raw["high"]),
        method=str(raw.get("method", "")),
        rationale=str(raw.get("rationale", "")),
    )
    if not candidates:
        return z
    nearest = _nearest_candidate(z, candidates)
    z.low = _clamp_value(z.low, nearest.low)
    z.high = _clamp_value(z.high, nearest.high)
    if z.low > z.high:
        z.low, z.high = z.high, z.low
    return z


def _nearest_candidate(z: ZoneBand, candidates: list[ZoneBand]) -> ZoneBand:
    midpoint = (z.low + z.high) / 2
    return min(candidates, key=lambda c: abs((c.low + c.high) / 2 - midpoint))


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
    """Confidence ≤ rubric_confidence. CONFIDENCE_LEVELS = ('low','medium','high')."""
    if raw_confidence not in CONFIDENCE:
        raw_confidence = "low"
    if rubric_confidence not in CONFIDENCE_LEVELS:
        rubric_confidence = "low"
    levels = list(CONFIDENCE)  # ('low', 'medium', 'high')
    return levels[min(levels.index(raw_confidence), levels.index(rubric_confidence))]


def _risk_reward(entry: ZoneBand, exit_: ZoneBand, invalidation: float) -> float:
    risk = entry.low - invalidation
    if risk <= 0:
        return 0.0
    reward = exit_.low - entry.high
    if reward <= 0:
        return 0.0
    return round(reward / risk, 2)


__all__ = ["QuickResult", "quick_tool_input_schema", "run"]
