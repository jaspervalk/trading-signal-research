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


def run_agent_with_web_search(
    *,
    agent_name: str,
    system_prompt: str,
    user_message: str,
    web_search_max_uses: int = 1,
    client: Anthropic | None = None,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 2500,
) -> AgentResult:
    """Same contract as `run_agent`, but adds the Anthropic `web_search`
    server-tool to the tools list.

    Anthropic's web_search is a server-tool: the model calls it, Anthropic
    executes it, and the model sees the results in the same turn. We do NOT
    force submit_lens via tool_choice — instead we set `tool_choice="auto"`
    and instruct in the system prompt that the model must call web_search
    once (max_uses=1) then submit_lens. Web search currently costs $10 per
    1000 searches (~$0.01 per search).

    Returns the same `AgentResult` shape. If the model never emits a
    submit_lens tool_use, returns an error result (lens=None) and the
    orchestrator carries on with the remaining lenses.
    """
    if agent_name not in LENS_NAMES:
        raise ValueError(f"Unknown agent_name {agent_name}; expected one of {LENS_NAMES}")

    client = _client_or_default(client)
    tool_name = "submit_lens"
    tools: list[dict[str, Any]] = [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": int(web_search_max_uses),
        },
        {
            "name": tool_name,
            "description": (
                f"Submit your {agent_name} lens read on the trade setup. "
                "Call this AFTER any web_search call so you can cite findings."
            ),
            "input_schema": _lens_tool_input_schema(agent_name),
        },
    ]

    started = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            tools=tools,
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as e:
        log.warning(
            "research.deep.agent_call_failed",
            agent=agent_name,
            error=str(e),
            web_search=True,
        )
        return AgentResult(
            agent_name=agent_name,
            lens=None,
            cost_usd=0.0,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=str(e),
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    token_cost = _compute_cost(
        response, price_in=HAIKU_PRICE_IN, price_out=HAIKU_PRICE_OUT
    )
    n_searches, search_cost = _extract_web_search_usage(response)
    cost = token_cost + search_cost

    raw = _extract_tool_input(response, tool_name=tool_name)
    search_citations = _extract_search_citations(response)

    if raw is None:
        return AgentResult(
            agent_name=agent_name,
            lens=None,
            cost_usd=cost,
            duration_ms=duration_ms,
            raw={"n_searches": n_searches, "citations": search_citations},
            error="no submit_lens tool_use block in response",
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
        log.warning(
            "research.deep.lens_parse_failed", agent=agent_name, error=str(e)
        )
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
        raw={
            "lens_input": raw,
            "n_searches": n_searches,
            "citations": search_citations,
        },
    )


_WEB_SEARCH_PRICE_PER_SEARCH = 0.01  # $10 / 1000


def _extract_web_search_usage(response: Any) -> tuple[int, float]:
    """Count web_search invocations and price them.

    Anthropic exposes `usage.server_tool_use.web_search_requests` on newer
    SDK versions. Older SDKs may not — fall back to counting `server_tool_use`
    blocks in the content list.
    """
    usage = getattr(response, "usage", None)
    requests = 0
    if usage is not None:
        stu = getattr(usage, "server_tool_use", None)
        if stu is not None:
            requests = int(getattr(stu, "web_search_requests", 0) or 0)
    if requests == 0:
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "server_tool_use":
                if getattr(block, "name", "") == "web_search":
                    requests += 1
    return requests, requests * _WEB_SEARCH_PRICE_PER_SEARCH


def _extract_search_citations(response: Any) -> list[dict[str, str]]:
    """Pull cited URLs/titles from the response for the audit trail."""
    out: list[dict[str, str]] = []
    for block in getattr(response, "content", []) or []:
        # web_search_tool_result blocks carry the actual search payload.
        if getattr(block, "type", None) == "web_search_tool_result":
            content = getattr(block, "content", None) or []
            for item in content:
                url = getattr(item, "url", None) or ""
                title = getattr(item, "title", None) or ""
                if url:
                    out.append({"title": title, "url": url})
        # Citations may also be attached to text blocks as `block.citations`.
        if getattr(block, "type", None) == "text":
            cites = getattr(block, "citations", None) or []
            for c in cites:
                url = getattr(c, "url", None) or ""
                title = getattr(c, "title", None) or ""
                if url:
                    out.append({"title": title, "url": url})
    return out


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


def _format_other_lenses(others: list[LensView]) -> str:
    """Format the other lenses' round-1 reads for inclusion in a revision prompt.

    Used by every analyst's revision call. Excludes the receiving analyst's
    own round-1 read (caller filters before passing in).
    """
    if not others:
        return "(no other lens reads available)"
    parts: list[str] = []
    for lv in others:
        parts.append(
            f"- [{lv.name}] direction={lv.direction} · conviction={lv.conviction}"
        )
        parts.append(f"    summary: {lv.summary}")
        for p in lv.points:
            parts.append(f"    · {p}")
    return "\n".join(parts)


REVISION_TOOL_NAME = "submit_revised_lens"


def _revision_tool_input_schema() -> dict[str, Any]:
    """JSON schema for a revision call's `submit_revised_lens` tool.

    Mirrors `_lens_tool_input_schema` plus a `revised_summary` separate
    from the round-1 summary and a required `responded_to` list.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "direction",
            "conviction",
            "revised_summary",
            "revised_points",
            "responded_to",
        ],
        "properties": {
            "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
            "conviction": {"type": "string", "enum": list(CONFIDENCE)},
            "revised_summary": {"type": "string"},
            "revised_points": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 4,
            },
            "responded_to": {
                "type": "array",
                "items": {"type": "string", "enum": list(LENS_NAMES)},
                "minItems": 0,
                "maxItems": 3,
            },
        },
    }


def run_revision(
    *,
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    round_one_lens: LensView,
    client: Anthropic | None = None,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 1500,
) -> AgentResult:
    """Run one analyst's revision call.

    On success, returns an `AgentResult` whose `lens` is the round-1 lens
    enriched with `revised_summary`, `revised_points`, `responded_to` (and
    potentially updated `direction` / `conviction` if the analyst changed
    their mind). On failure, returns the round-1 lens verbatim — orchestrator
    fallback is a no-op.
    """
    if client is None:
        env = load_env()
        if not env.anthropic_api_key:
            return AgentResult(
                agent_name=agent_name,
                lens=round_one_lens,
                cost_usd=0.0,
                duration_ms=0,
                error="ANTHROPIC_API_KEY not set",
            )
        client = Anthropic(api_key=env.anthropic_api_key)

    started = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,
            system=system_prompt,
            tools=[
                {
                    "name": REVISION_TOOL_NAME,
                    "description": (
                        "Submit a revised lens read after seeing the other "
                        "lenses' round-1 reads."
                    ),
                    "input_schema": _revision_tool_input_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": REVISION_TOOL_NAME},
            messages=[{"role": "user", "content": user_prompt}],
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        cost = _compute_cost(response, price_in=HAIKU_PRICE_IN, price_out=HAIKU_PRICE_OUT)
        raw = _extract_tool_input(response, tool_name=REVISION_TOOL_NAME)
        if raw is None:
            return AgentResult(
                agent_name=agent_name,
                lens=round_one_lens,
                cost_usd=cost,
                duration_ms=duration_ms,
                error=f"no {REVISION_TOOL_NAME} block in response",
            )

        revised_lens = round_one_lens.model_copy(
            update={
                "direction": raw.get("direction", round_one_lens.direction),
                "conviction": raw.get("conviction", round_one_lens.conviction),
                "revised_summary": raw.get("revised_summary"),
                "revised_points": list(raw.get("revised_points", [])),
                "responded_to": list(raw.get("responded_to", [])),
            }
        )
        return AgentResult(
            agent_name=agent_name,
            lens=revised_lens,
            cost_usd=cost,
            duration_ms=duration_ms,
            raw=raw,
        )
    except Exception as e:
        log.warning("research.deep.revision_failed", agent=agent_name, error=str(e))
        return AgentResult(
            agent_name=agent_name,
            lens=round_one_lens,
            cost_usd=0.0,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=str(e),
        )


__all__ = [
    "AgentResult",
    "HAIKU_PRICE_IN",
    "HAIKU_PRICE_OUT",
    "REVISION_TOOL_NAME",
    "SONNET_PRICE_IN",
    "SONNET_PRICE_OUT",
    "_format_other_lenses",
    "_revision_tool_input_schema",
    "run_agent",
    "run_agent_with_web_search",
    "run_agents_parallel",
    "run_revision",
]
