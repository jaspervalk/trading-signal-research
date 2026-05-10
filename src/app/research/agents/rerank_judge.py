"""Sonnet rerank judge for watchlist scan reranking (Phase 3).

Takes 2 lens reads (Quant + Contrarian) + minimal ticker context, picks
a research-priority rank in {high, medium, low, skip} with a <=25-word
rationale.

Distinct from the entry/exit `judge.py` (which picks numeric levels).
This judge picks ONE categorical decision; the ticker header is small
because the rank-decision is dominated by the lens reads, not by raw
indicators.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from anthropic import Anthropic

from app.config import load_env
from app.logging import get_logger
from app.research.agents.base import (
    SONNET_PRICE_IN,
    SONNET_PRICE_OUT,
    _compute_cost,
    _extract_tool_input,
)
from app.research.schema import LensView, RANKS

log = get_logger(__name__)


RERANK_TOOL_NAME = "submit_rerank"


SYSTEM_PROMPT = """\
You are reranking watchlist tickers for research priority. You see TWO \
lens reads (Quantitative + Contrarian-Risk) and a minimal price header \
for ONE ticker. Output ONE rank in {high, medium, low, skip} plus a \
<=25-word rationale.

Rank semantics:
- high: setup is compelling on Quant AND Contrarian doesn't kill it.
- medium: Quant is bullish but Contrarian flags real risk; deserves \
  research with a tighter risk plan.
- low: Quant is mixed / weak; Contrarian sees nothing acute. Background \
  watch.
- skip: not worth research time today (Quant negative, or Contrarian \
  flags something disqualifying like crowded mania, broken thesis).

Rules:
- Use BOTH lenses. If they disagree, the right rank is usually 'medium', \
  never 'high'.
- Rationale <=25 words. Reference WHICH lens drove the rank.
- Contrarian flags must be ACUTE risks: crowded-trade signals, broken \
  thesis, near-term binary catalysts (earnings <=5 days), valuation \
  extremes that diverge from sector. DO NOT downgrade on calendar \
  trivia ('earnings in 80 days') or macro-platitudes ('rates could rise'). \
  If Contrarian's only flags are routine, treat as 'low' or 'high', not \
  'medium' or 'skip'.
- NEVER use 'buy' / 'sell' / 'recommendation'. Use 'research', 'consider', \
  'avoid'.
- Submit via submit_rerank.
"""


USER_TEMPLATE = """\
Ticker: {ticker}
As of: {as_of}
Last close: {last_close}
ATR(14): {atr_14}

LENS READS:
{lenses_block}

Submit rank + rationale via submit_rerank.
"""


def rerank_judge_tool_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["rank", "rationale"],
        "properties": {
            "rank": {"type": "string", "enum": list(RANKS)},
            "rationale": {"type": "string"},
        },
    }


def run_rerank_judge(
    *,
    ticker: str,
    as_of: datetime,
    lenses: list[LensView],
    last_close: float | None,
    atr_14: float | None,
    client: Anthropic | None = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 500,
) -> tuple[str, str, float, int]:
    """Call Sonnet to pick the rank. Returns (rank, rationale, cost, duration_ms).

    On failure (no API key, exception, missing tool block), returns
    ("skip", "rerank judge failed: <reason>", 0.0, 0). Caller can decide
    whether to surface the error or just store rank='skip'.
    """
    if client is None:
        env = load_env()
        if not env.anthropic_api_key:
            return ("skip", "ANTHROPIC_API_KEY not set", 0.0, 0)
        client = Anthropic(api_key=env.anthropic_api_key)

    user_msg = USER_TEMPLATE.format(
        ticker=ticker,
        as_of=as_of.isoformat(),
        last_close=f"{last_close:.2f}" if last_close is not None else "—",
        atr_14=f"{atr_14:.2f}" if atr_14 is not None else "—",
        lenses_block=_format_lenses(lenses),
    )

    started = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,
            system=SYSTEM_PROMPT,
            tools=[
                {
                    "name": RERANK_TOOL_NAME,
                    "description": "Submit research-priority rank for one ticker.",
                    "input_schema": rerank_judge_tool_input_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": RERANK_TOOL_NAME},
            messages=[{"role": "user", "content": user_msg}],
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        cost = _compute_cost(
            response, price_in=SONNET_PRICE_IN, price_out=SONNET_PRICE_OUT
        )
        raw = _extract_tool_input(response, tool_name=RERANK_TOOL_NAME)
        if raw is None:
            return ("skip", f"no {RERANK_TOOL_NAME} block in response", cost, duration_ms)

        rank = raw.get("rank", "skip")
        if rank not in RANKS:
            rank = "skip"
        rationale = str(raw.get("rationale", ""))[:200]  # belt-and-braces cap
        return (rank, rationale, cost, duration_ms)
    except Exception as e:
        log.warning("research.rerank.judge_failed", error=str(e))
        return ("skip", f"rerank judge failed: {e}", 0.0, int((time.monotonic() - started) * 1000))


def _format_lenses(lenses: list[LensView]) -> str:
    if not lenses:
        return "(no lens reads)"
    parts: list[str] = []
    for lv in lenses:
        parts.append(f"- [{lv.name}] direction={lv.direction} · conviction={lv.conviction}")
        parts.append(f"    summary: {lv.summary}")
        for p in lv.points:
            parts.append(f"    · {p}")
    return "\n".join(parts)


__all__ = [
    "RERANK_TOOL_NAME",
    "rerank_judge_tool_input_schema",
    "run_rerank_judge",
]
