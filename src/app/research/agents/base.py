"""Shared agent infrastructure for Deep mode.

- `AgentResult` — what a single analyst returns
- `run_agent()` — make one tool-use Claude call, return parsed `LensView`
- `run_agents_parallel()` — fan out N analysts via ThreadPoolExecutor
- Pricing constants for cost tracking
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from anthropic import Anthropic

from app.config import load_env
from app.logging import get_logger
from app.research.schema import CONFIDENCE, LENS_NAMES, LensView

log = get_logger(__name__)

# Anthropic pricing in USD per million tokens.
HAIKU_PRICE_IN = 1.0
HAIKU_PRICE_OUT = 5.0
SONNET_PRICE_IN = 3.0
SONNET_PRICE_OUT = 15.0


@dataclass
class AgentResult:
    """Output of one analyst agent.

    The `lens` is the structured opinion; `cost_usd` and `duration_ms` are
    threaded into `EntryExitPlan` for the audit footer. `error` is set
    when the agent failed; in that case `lens` is None and the orchestrator
    proceeds with the remaining analysts.
    """

    agent_name: str
    lens: LensView | None
    cost_usd: float
    duration_ms: int
    raw: dict[str, Any] | None = None
    error: str | None = None


def _lens_tool_input_schema(name: str) -> dict[str, Any]:
    """JSON schema for an analyst's `submit_lens` tool call."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["direction", "conviction", "summary", "points"],
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["bullish", "bearish", "neutral"],
            },
            "conviction": {"type": "string", "enum": list(CONFIDENCE)},
            "summary": {
                "type": "string",
                "description": "One-line headline read (≤ 25 words).",
            },
            "points": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 4,
                "description": "2-4 supporting bullet points grounded in your context only.",
            },
        },
    }


def _client_or_default(client: Anthropic | None) -> Anthropic:
    if client is not None:
        return client
    env = load_env()
    if not env.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set; cannot run agents without one."
        )
    return Anthropic(api_key=env.anthropic_api_key)


def _compute_cost(response: Any, *, price_in: float, price_out: float) -> float:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0.0
    in_tok = getattr(usage, "input_tokens", 0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    return (in_tok / 1_000_000) * price_in + (out_tok / 1_000_000) * price_out


def run_agent(
    *,
    agent_name: str,
    system_prompt: str,
    user_message: str,
    client: Anthropic | None = None,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 1500,
) -> AgentResult:
    """Run one analyst agent. Always returns an `AgentResult`; failures
    are captured as `error` rather than raised so the orchestrator can
    keep going with partial coverage.
    """
    if agent_name not in LENS_NAMES:
        raise ValueError(f"Unknown agent_name {agent_name}; expected one of {LENS_NAMES}")

    client = _client_or_default(client)
    tool_name = "submit_lens"
    tools = [
        {
            "name": tool_name,
            "description": (
                f"Submit your {agent_name} lens read on the trade setup. "
                "Use ONLY the context provided — do not invent data."
            ),
            "input_schema": _lens_tool_input_schema(agent_name),
        }
    ]

    started = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=tools,
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as e:
        log.warning("research.deep.agent_call_failed", agent=agent_name, error=str(e))
        return AgentResult(
            agent_name=agent_name,
            lens=None,
            cost_usd=0.0,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=str(e),
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    cost = _compute_cost(response, price_in=HAIKU_PRICE_IN, price_out=HAIKU_PRICE_OUT)

    raw = _extract_tool_input(response, tool_name=tool_name)
    if raw is None:
        return AgentResult(
            agent_name=agent_name,
            lens=None,
            cost_usd=cost,
            duration_ms=duration_ms,
            error="no tool_use block in response",
        )

    try:
        lens = LensView(
            name=agent_name,
            direction=str(raw.get("direction", "neutral")),
            conviction=str(raw.get("conviction", "low")),
            summary=str(raw.get("summary", "")),
            points=[str(p) for p in raw.get("points", [])],
        )
    except Exception as e:
        log.warning("research.deep.lens_parse_failed", agent=agent_name, error=str(e))
        return AgentResult(
            agent_name=agent_name,
            lens=None,
            cost_usd=cost,
            duration_ms=duration_ms,
            raw=raw,
            error=f"lens parse failed: {e}",
        )

    return AgentResult(
        agent_name=agent_name,
        lens=lens,
        cost_usd=cost,
        duration_ms=duration_ms,
        raw=raw,
    )


def run_agents_parallel(
    runners: list[Callable[[], AgentResult]],
    *,
    max_workers: int = 4,
) -> list[AgentResult]:
    """Run a list of zero-arg agent runners concurrently.

    Each callable should return an `AgentResult`; exceptions are caught and
    converted to error results so the orchestrator never crashes mid-run.
    Order of returned results matches the input order.
    """
    if not runners:
        return []
    results: list[AgentResult | None] = [None] * len(runners)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(r): i for i, r in enumerate(runners)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:  # pragma: no cover — runner already wraps
                log.warning("research.deep.runner_crashed", idx=idx, error=str(e))
                results[idx] = AgentResult(
                    agent_name=f"agent_{idx}",
                    lens=None,
                    cost_usd=0.0,
                    duration_ms=0,
                    error=str(e),
                )
    return [r for r in results if r is not None]


def _extract_tool_input(response: Any, *, tool_name: str) -> dict[str, Any] | None:
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input  # type: ignore[no-any-return]
    return None


__all__ = [
    "AgentResult",
    "HAIKU_PRICE_IN",
    "HAIKU_PRICE_OUT",
    "SONNET_PRICE_IN",
    "SONNET_PRICE_OUT",
    "run_agent",
    "run_agents_parallel",
]
