# Watchlist Scan Reranking Implementation Plan (Phase 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute task-by-task.

**Goal:** Add a reduced 2-lens panel (Quantitative + Contrarian-Risk) per watchlist ticker plus a small Sonnet rerank judge that emits a `research-priority` rank in `{high, medium, low, skip}` with a 1-line rationale. Composes with existing `tsr scan` to surface "what's worth deeper research today" instead of pure rule-based ranking.

**Architecture:**
- **Reduced panel.** Only 2 of the 4 Deep-mode analysts run: Quantitative (statistical edge / momentum) and Contrarian-Risk (adversarial filter). Skip Fundamental + Sentiment-Macro for batch speed/cost.
- **Rerank judge.** A small Sonnet call that takes the 2 lens reads + minimal ticker header → `ScanRerankResult` with `rank` (priority), `rationale` (≤25 words), `lenses` (the 2 reads passed through).
- **Composes with `tsr scan`.** New flag `--rerank` runs the existing rule-based scan, then for each ticker dispatches the panel in parallel, attaches the rank to the existing scan output.
- **No cache (this phase).** Recordings still flow through Phase 1's `LensSnapshot` infra. Add caching when daily usage justifies it.
- **No frontend (this phase).** CLI-only delivery; frontend can mount on watchlist board in a follow-up.

**Tech Stack:** Python 3.11, Pydantic v2, Anthropic SDK Haiku 4.5 (analysts) + Sonnet 4.6 (rerank judge), `concurrent.futures.ThreadPoolExecutor` for batch parallelism, pytest, Typer.

**Cost (target):** ~$0.01-0.015 per ticker × 20 watchlist tickers = ~$0.20-0.30 per `tsr scan --rerank` invocation. User-driven, not cron-driven. Bounded.

**Out of scope:**
- Caching at `(ticker, day_key)` — add when daily usage exceeds 1-2 invocations.
- Frontend mounting on `ScanStatusBoard.tsx` — deferred until rerank quality is validated.
- 4-lens (full) batch panel — too expensive for batch use.
- Cron integration — user-driven for now.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `src/app/research/schema.py` | modify | Add `ScanRerankResult` Pydantic model. |
| `src/app/research/rerank.py` | **new** | `run_rerank(packet) -> ScanRerankResult` orchestrating 2 analysts + Sonnet rerank judge. |
| `src/app/research/agents/rerank_judge.py` | **new** | Sonnet rerank judge — small prompt, picks rank from {high, medium, low, skip} + ≤25-word rationale. |
| `src/app/research/scan_rerank.py` | **new** | `rerank_scan_results(scan_output) -> list[ScanRerankResult]` — fans out parallel panels for each scanned ticker. |
| `src/app/cli.py` | modify | Extend existing `scan` command with `--rerank` flag. |
| `apps/api/app/routes/research.py` | modify | Add `POST /research/scan-rerank` endpoint. |
| `tests/test_research_rerank.py` | **new** | Unit tests for `run_rerank` (mocked clients) + happy path + partial-lens-failure. |

---

## Task 1 — `ScanRerankResult` schema

**Files:**
- Modify: `src/app/research/schema.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_research_rerank.py` (file may not exist yet — create it):

```python
"""Tests for the watchlist scan rerank feature (Phase 3)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.research.schema import LensView, ScanRerankResult


def test_scan_rerank_result_schema():
    r = ScanRerankResult(
        ticker="AAPL",
        as_of=datetime(2026, 5, 8, tzinfo=UTC),
        rank="high",
        rationale="Strong breakout with no broad-market warning signs.",
        lenses=[
            LensView(name="quantitative", direction="bullish", conviction="high",
                     summary="63d high cleared on volume", points=["p1"]),
            LensView(name="contrarian_risk", direction="neutral", conviction="medium",
                     summary="No crowded-trade signals", points=["p1"]),
        ],
        cost_usd=0.013,
        duration_ms=2400,
    )
    assert r.rank == "high"
    assert len(r.lenses) == 2
    assert r.cost_usd == 0.013
```

Run:
```bash
cd /Users/jaspervalk/Documents/projects/trading-signal-research && pytest tests/test_research_rerank.py -v
```

Expected: FAIL with `ImportError: cannot import name 'ScanRerankResult'`.

- [ ] **Step 2: Add the schema in `src/app/research/schema.py`**

Insert after `EntryExitPlan` (or wherever the entry/exit research schemas cluster):

```python
RANKS = ("high", "medium", "low", "skip")


class ScanRerankResult(BaseModel):
    """Per-ticker output of the watchlist rerank panel.

    Reduced 2-lens panel (Quantitative + Contrarian-Risk) → small Sonnet
    rerank judge → research-priority rank. Used by `tsr scan --rerank` to
    surface "what's worth deeper research today" beyond rule-based scan.
    """

    ticker: str
    as_of: datetime
    rank: str  # one of RANKS
    rationale: str  # ≤25 words; the judge's 1-line reason for this rank
    lenses: list[LensView] = Field(default_factory=list)
    cost_usd: float = 0.0
    duration_ms: int = 0
    sources_used: list[str] = Field(default_factory=list)
    error: str | None = None  # set if the panel failed; rank='skip' in that case
```

Add `ScanRerankResult` and `RANKS` to `__all__`.

- [ ] **Step 3: Verify test passes**

```bash
pytest tests/test_research_rerank.py::test_scan_rerank_result_schema -v
```

Expected: PASS.

- [ ] **Step 4: Run full suite**

```bash
.venv/bin/pytest -q 2>&1 | tail -3
```

Expected: 380+ passed (379 existing + 1 new).

- [ ] **Step 5: Commit**

```bash
git add src/app/research/schema.py tests/test_research_rerank.py
git commit -m "feat(schema): add ScanRerankResult for watchlist rerank panel

Phase 3 schema: per-ticker reduced-panel output (2 lenses + rank +
rationale). RANKS = (high, medium, low, skip). 'skip' is the
graceful-failure rank — set when the panel errors or returns no usable
lens reads."
```

---

## Task 2 — Rerank judge (Sonnet)

**Files:**
- Create: `src/app/research/agents/rerank_judge.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_research_rerank.py`:

```python
import pytest
from types import SimpleNamespace

from app.research.agents.rerank_judge import (
    RERANK_TOOL_NAME,
    rerank_judge_tool_input_schema,
    run_rerank_judge,
)


def _fake_judge_response(raw_input: dict, input_tokens=600, output_tokens=80):
    block = SimpleNamespace(type="tool_use", name=RERANK_TOOL_NAME, input=raw_input)
    return SimpleNamespace(
        content=[block],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        model_dump=lambda: {"content": [{"type": "tool_use", "input": raw_input}]},
    )


class _FakeJudgeClient:
    def __init__(self, raw_input: dict):
        self._resp = _fake_judge_response(raw_input)
        class _Messages:
            def __init__(inner, parent): inner.parent = parent
            def create(inner, **kwargs):
                inner.parent.last_kwargs = kwargs
                return inner.parent._resp
        self.messages = _Messages(self)
        self.last_kwargs = {}


def test_rerank_judge_schema_required_fields():
    schema = rerank_judge_tool_input_schema()
    assert "rank" in schema["required"]
    assert "rationale" in schema["required"]
    assert schema["properties"]["rank"]["enum"] == ["high", "medium", "low", "skip"]


def test_rerank_judge_returns_structured_output():
    quant = LensView(name="quantitative", direction="bullish", conviction="high",
                     summary="strong breakout", points=["p1"])
    contra = LensView(name="contrarian_risk", direction="bearish", conviction="medium",
                      summary="crowded long", points=["p1"])
    raw = {
        "rank": "medium",
        "rationale": "Strong tech but Contrarian flags positioning risk; size accordingly.",
    }
    client = _FakeJudgeClient(raw)
    rank, rationale, cost, duration_ms = run_rerank_judge(
        ticker="AAPL",
        as_of=datetime(2026, 5, 8, tzinfo=UTC),
        lenses=[quant, contra],
        last_close=200.0,
        atr_14=4.0,
        client=client,
    )
    assert rank == "medium"
    assert "Contrarian" in rationale or "positioning" in rationale
    assert cost > 0
    assert duration_ms >= 0
    # Verify temperature=0
    assert client.last_kwargs.get("temperature") == 0
```

Run:
```bash
pytest tests/test_research_rerank.py -v
```

Expected: FAIL — `rerank_judge` module doesn't exist.

- [ ] **Step 2: Create `src/app/research/agents/rerank_judge.py`**

```python
"""Sonnet rerank judge for watchlist scan reranking (Phase 3).

Takes 2 lens reads (Quant + Contrarian) + minimal ticker context, picks
a research-priority rank in {high, medium, low, skip} with a ≤25-word
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
≤25-word rationale.

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
- Rationale ≤25 words. Reference WHICH lens drove the rank.
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
```

- [ ] **Step 3: Verify tests pass**

```bash
pytest tests/test_research_rerank.py -v
```

Expected: 3/3 PASS.

- [ ] **Step 4: Commit**

```bash
git add src/app/research/agents/rerank_judge.py tests/test_research_rerank.py
git commit -m "feat(research): Sonnet rerank judge — picks rank from 2 lens reads

Small Sonnet call (~\$0.005/ticker) that takes Quant + Contrarian-Risk
lens reads + minimal price header → rank in {high, medium, low, skip}
+ ≤25-word rationale. temperature=0 for determinism. Failures return
('skip', error). Distinct from entry/exit judge.py — different output
shape, smaller prompt."
```

---

## Task 3 — Rerank orchestrator (`run_rerank`)

**Files:**
- Create: `src/app/research/rerank.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_research_rerank.py`:

```python
from datetime import UTC, datetime
from app.research.rerank import run_rerank


def test_run_rerank_happy_path(monkeypatch):
    """Quant + Contrarian both succeed → judge picks 'high'."""
    from app.research import rerank as rerank_mod

    quant_lens = LensView(name="quantitative", direction="bullish", conviction="high",
                          summary="strong setup", points=["p1"])
    contra_lens = LensView(name="contrarian_risk", direction="neutral", conviction="medium",
                           summary="no concerns", points=["p1"])

    def fake_quant(packet, client=None):
        from app.research.agents.base import AgentResult
        return AgentResult(agent_name="quantitative", lens=quant_lens, cost_usd=0.005, duration_ms=1200)

    def fake_contrarian(packet, client=None):
        from app.research.agents.base import AgentResult
        return AgentResult(agent_name="contrarian_risk", lens=contra_lens, cost_usd=0.005, duration_ms=1100)

    def fake_judge(*, ticker, as_of, lenses, last_close, atr_14, client=None):
        return ("high", "Quant: clean setup; Contrarian: no risk flags.", 0.008, 1500)

    monkeypatch.setattr(rerank_mod, "run_technical", fake_quant)
    monkeypatch.setattr(rerank_mod, "run_contrarian", fake_contrarian)
    monkeypatch.setattr(rerank_mod, "run_rerank_judge", fake_judge)

    packet = _stub_packet()  # minimal helper — see _stub_packet() in test_research_deep.py for the pattern

    result = run_rerank(packet)
    assert result.ticker == packet.ticker
    assert result.rank == "high"
    assert "Quant" in result.rationale
    assert len(result.lenses) == 2
    assert result.cost_usd > 0


def test_run_rerank_one_analyst_fails(monkeypatch):
    """If 1 of 2 analysts fails, judge runs with 1 lens. Result still emitted."""
    from app.research import rerank as rerank_mod
    from app.research.agents.base import AgentResult

    contra_lens = LensView(name="contrarian_risk", direction="bearish", conviction="high",
                           summary="overbought + thin float", points=["p1"])

    def fake_quant_fail(packet, client=None):
        return AgentResult(agent_name="quantitative", lens=None, cost_usd=0.0,
                           duration_ms=500, error="Haiku timeout")

    def fake_contrarian(packet, client=None):
        return AgentResult(agent_name="contrarian_risk", lens=contra_lens, cost_usd=0.005, duration_ms=1100)

    def fake_judge(*, ticker, as_of, lenses, last_close, atr_14, client=None):
        # Judge sees only contrarian; picks 'low' or 'skip'
        return ("skip", "Quant failed; Contrarian-only insufficient.", 0.005, 1000)

    monkeypatch.setattr(rerank_mod, "run_technical", fake_quant_fail)
    monkeypatch.setattr(rerank_mod, "run_contrarian", fake_contrarian)
    monkeypatch.setattr(rerank_mod, "run_rerank_judge", fake_judge)

    packet = _stub_packet()
    result = run_rerank(packet)
    assert result.rank == "skip"
    assert len(result.lenses) == 1  # only contrarian survived


def test_run_rerank_both_analysts_fail(monkeypatch):
    """If both analysts fail, return rank='skip' WITHOUT calling the judge."""
    from app.research import rerank as rerank_mod
    from app.research.agents.base import AgentResult

    def fake_fail(packet, client=None):
        return AgentResult(agent_name="x", lens=None, cost_usd=0.0, duration_ms=500, error="fail")

    judge_called = []
    def fake_judge(**kwargs):
        judge_called.append(kwargs)
        return ("high", "shouldn't happen", 0.0, 0)

    monkeypatch.setattr(rerank_mod, "run_technical", fake_fail)
    monkeypatch.setattr(rerank_mod, "run_contrarian", fake_fail)
    monkeypatch.setattr(rerank_mod, "run_rerank_judge", fake_judge)

    packet = _stub_packet()
    result = run_rerank(packet)
    assert result.rank == "skip"
    assert result.error is not None
    assert "both analysts failed" in result.error.lower()
    assert judge_called == []  # judge NOT invoked when both fail
```

The `_stub_packet()` helper here should produce a minimal `ResearchPacket` — copy the pattern from existing `tests/test_research_deep.py` (it has `_stub_packet()` already). Add the helper at the top of `tests/test_research_rerank.py` if it's not importable.

- [ ] **Step 2: Verify tests fail**

```bash
pytest tests/test_research_rerank.py -v
```

Expected: FAIL — `rerank` module doesn't exist.

- [ ] **Step 3: Create `src/app/research/rerank.py`**

```python
"""Watchlist rerank orchestrator (Phase 3).

Reduced 2-lens panel (Quantitative + Contrarian-Risk) → small Sonnet
rerank judge → ScanRerankResult. Used by `tsr scan --rerank` to surface
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
```

- [ ] **Step 4: Verify tests pass**

```bash
pytest tests/test_research_rerank.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/research/rerank.py tests/test_research_rerank.py
git commit -m "feat(research): run_rerank() orchestrator

Reduced 2-lens panel (Quant + Contrarian) + Sonnet rerank judge →
ScanRerankResult. Total analyst failure short-circuits to rank='skip'
without calling the judge. Cost ~\$0.01-0.015 per ticker."
```

---

## Task 4 — Batch fan-out (`rerank_scan_results`)

**Files:**
- Create: `src/app/research/scan_rerank.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_research_rerank.py`:

```python
from app.research.scan_rerank import rerank_tickers


def test_rerank_tickers_parallel(monkeypatch):
    """Fan out N tickers across a small ThreadPoolExecutor; collect results in
    submission order (not arrival order) for deterministic display."""
    from app.research import scan_rerank as scan_rerank_mod

    def fake_build_packet(ticker, **kwargs):
        return _stub_packet(ticker=ticker)

    def fake_run_rerank(packet, client=None):
        return ScanRerankResult(
            ticker=packet.ticker,
            as_of=packet.as_of,
            rank="medium",
            rationale=f"stub for {packet.ticker}",
            lenses=[],
            cost_usd=0.01,
            duration_ms=100,
        )

    monkeypatch.setattr(scan_rerank_mod, "build_research_packet", fake_build_packet)
    monkeypatch.setattr(scan_rerank_mod, "run_rerank", fake_run_rerank)

    results = rerank_tickers(["AAPL", "NVDA", "TSLA"])
    tickers = [r.ticker for r in results]
    assert tickers == ["AAPL", "NVDA", "TSLA"]  # preserves submission order
    assert all(r.rank == "medium" for r in results)
```

- [ ] **Step 2: Create `src/app/research/scan_rerank.py`**

```python
"""Batch wrapper for `tsr scan --rerank`.

Fans out one rerank panel per ticker via ThreadPoolExecutor. Collects
results in submission order (so the user sees the same row order as the
input). Per-ticker failures degrade gracefully: a ticker whose packet
build fails returns a `ScanRerankResult` with rank='skip' and an error.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from anthropic import Anthropic

from app.logging import get_logger
from app.research.context import build_research_packet
from app.research.rerank import run_rerank
from app.research.schema import ScanRerankResult

log = get_logger(__name__)


def rerank_tickers(
    tickers: list[str],
    *,
    client: Anthropic | None = None,
    max_workers: int = 4,
) -> list[ScanRerankResult]:
    """Fan out reranks across `tickers`, return in submission order.

    Per-ticker errors are caught and surface as rank='skip' results — one
    bad packet doesn't kill the batch.
    """
    results: dict[str, ScanRerankResult] = {}

    def work(ticker: str) -> ScanRerankResult:
        try:
            packet = build_research_packet(ticker)
        except Exception as e:
            log.warning("research.rerank.packet_build_failed", ticker=ticker, error=str(e))
            return ScanRerankResult(
                ticker=ticker,
                as_of=datetime.now(timezone.utc),
                rank="skip",
                rationale=f"packet build failed: {e}",
                lenses=[],
                cost_usd=0.0,
                duration_ms=0,
                error=str(e),
            )
        return run_rerank(packet, client=client)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(work, t): t for t in tickers}
        for fut in as_completed(futures):
            ticker = futures[fut]
            results[ticker] = fut.result()

    return [results[t] for t in tickers]


__all__ = ["rerank_tickers"]
```

If `build_research_packet` doesn't exist with that exact name, find it in `src/app/research/context.py` and adapt the import / call signature. Common alternatives: `build_packet`, `gather_packet`.

- [ ] **Step 3: Verify tests pass**

```bash
pytest tests/test_research_rerank.py -v
```

Expected: 4/4 PASS.

- [ ] **Step 4: Commit**

```bash
git add src/app/research/scan_rerank.py tests/test_research_rerank.py
git commit -m "feat(research): batch rerank fan-out for scan --rerank

ThreadPoolExecutor with max_workers=4. Per-ticker packet-build failures
surface as rank='skip' with error; one bad ticker doesn't kill the batch.
Results returned in submission order for stable display."
```

---

## Task 5 — CLI integration (`tsr scan --rerank`)

**Files:**
- Modify: `src/app/cli.py`

- [ ] **Step 1: Read existing scan command**

```bash
sed -n '400,500p' src/app/cli.py
```

Note how the existing `scan` command parses tickers from `--watchlist | --tickers | --universe` and renders output.

- [ ] **Step 2: Add `--rerank` flag**

In the existing `scan` command (around line 404), add a `rerank: bool = typer.Option(False, "--rerank", help="...")` parameter. After the existing scan output is computed, if `rerank` is True, call `rerank_tickers(ticker_list)` and append the rank to each row of the output.

The exact insertion depends on how the scan output is currently rendered. Two patterns are likely:

**Pattern A (table-row-by-row):** the existing scan iterates and prints. Insert the rerank call before the print loop and look up the rank per-ticker as you print.

**Pattern B (scan returns a dataclass list, prints at end):** call `rerank_tickers` after the scan returns, zip ranks onto rows.

Either way:

```python
if rerank:
    from app.research.scan_rerank import rerank_tickers
    typer.echo(f"reranking {len(ticker_list)} ticker(s)…")
    rerank_results = rerank_tickers(ticker_list)
    rerank_by_ticker = {r.ticker: r for r in rerank_results}
    # Now thread `rerank_by_ticker[ticker].rank` and `.rationale` into
    # the existing scan output. Adapt to whatever the existing render is.
```

If the scan output is a typer.echo'd table, add 2 columns: `rank` and `rationale (truncated to 60 chars)`. If JSON output is supported (via `--json` flag), include the rerank fields in each row.

Read the existing render carefully and adapt. Keep changes minimal.

- [ ] **Step 3: Smoke the CLI (no real LLM calls; rely on existing universe/watchlist)**

```bash
.venv/bin/tsr scan --watchlist --rerank --json 2>&1 | head -30
```

If this hits real Anthropic, the test costs ~$0.20-0.30 — confirm the user wants to proceed. If you're an autonomous agent, prefer:

```bash
.venv/bin/tsr scan --tickers AAPL --rerank --json 2>&1 | head -30
```

Single ticker = ~$0.01. Acceptable smoke cost.

If you cannot run a smoke without spending money, just verify the CLI accepts the new flag without error:

```bash
.venv/bin/tsr scan --help | grep rerank
```

Should show the new option.

- [ ] **Step 4: Commit**

```bash
git add src/app/cli.py
git commit -m "feat(cli): tsr scan --rerank composes reduced lens panel onto scan output

Per-ticker rank ('high'/'medium'/'low'/'skip') + ≤25-word rationale.
Cost ~\$0.01-0.015 per ticker. Use --tickers AAPL,NVDA for ad-hoc; use
--watchlist for the daily-driver."
```

---

## Task 6 — API endpoint

**Files:**
- Modify: `apps/api/app/routes/research.py`

- [ ] **Step 1: Append the endpoint**

```python
from app.research.scan_rerank import rerank_tickers


@router.post("/scan-rerank", response_model=list[dict])
def post_scan_rerank(
    tickers: list[str] | None = None,
    source: str = "tickers",  # "watchlist" | "tickers"
    db: Session = Depends(db_session),
):
    """Rerank a list of tickers via the reduced 2-lens panel + Sonnet judge.

    Body: {"tickers": ["AAPL", "NVDA"]} or query ?source=watchlist to pull
    from the user's pinned watchlist.

    Cost: ~\$0.01-0.015 per ticker. No caching this phase — every call
    spends.
    """
    if source == "watchlist":
        from app.models import Watchlist
        ticker_list = [
            row.ticker for row in db.query(Watchlist).all()
        ]
    else:
        ticker_list = list(tickers or [])

    if not ticker_list:
        return []

    results = rerank_tickers(ticker_list)
    return [
        {
            "ticker": r.ticker,
            "as_of": r.as_of.isoformat(),
            "rank": r.rank,
            "rationale": r.rationale,
            "lenses": [
                {"name": l.name, "direction": l.direction, "conviction": l.conviction, "summary": l.summary}
                for l in r.lenses
            ],
            "cost_usd": r.cost_usd,
            "duration_ms": r.duration_ms,
            "sources_used": r.sources_used,
            "error": r.error,
        }
        for r in results
    ]
```

If the watchlist row schema differs (e.g. `WatchlistRow.symbol` rather than `.ticker`), adapt. Read the existing `Watchlist` ORM in `src/app/models.py`.

If the FastAPI dependency for the DB session is named differently (`get_db` vs `db_session` etc.), match what the rest of the file uses.

- [ ] **Step 2: Smoke the endpoint (single ticker, ~$0.01)**

```bash
# Start the API in another terminal:
# .venv/bin/uvicorn apps.api.app.main:app --port 8001 --reload
curl -s -X POST 'http://localhost:8001/research/scan-rerank' \
  -H 'content-type: application/json' \
  -d '{"tickers": ["AAPL"]}' | jq '.'
```

Expected: 1-element list with rank, rationale, lenses, cost.

If you can't run the API for budget reasons, at minimum verify the route is registered:

```bash
.venv/bin/python -c "
from apps.api.app.routes.research import router
paths = [r.path for r in router.routes if 'rerank' in r.path]
print(paths)
"
```

Expected: includes `/research/scan-rerank`.

- [ ] **Step 3: Commit**

```bash
git add apps/api/app/routes/research.py
git commit -m "feat(api): POST /research/scan-rerank endpoint

Body: {tickers: [...]} or ?source=watchlist. Returns rank + rationale +
lenses + cost per ticker. No caching this phase — every call hits the
Anthropic API. Total cost ~\$0.01-0.015 per ticker."
```

---

## Task 7 — Recent-changes documentation + final verification

**Files:**
- Modify: `docs/recent-changes-2026-05-08.md`

- [ ] **Step 1: Append §15**

```markdown
## 15. Watchlist scan reranking (Phase 3 of agent roadmap)

Phase 3 of [docs/superpowers/plans/2026-05-08-lens-agents-roadmap.md](superpowers/plans/2026-05-08-lens-agents-roadmap.md).
Reduced 2-lens panel + small Sonnet judge per ticker; produces a research-priority
rank in {high, medium, low, skip} with a ≤25-word rationale. Composes
with the existing rule-based `tsr scan` to surface "what's worth deeper
research today" beyond pure-mechanical ranking.

**What landed:**

| Component | File |
|---|---|
| `ScanRerankResult` schema + `RANKS` enum | [src/app/research/schema.py](../src/app/research/schema.py) |
| Sonnet rerank judge | [src/app/research/agents/rerank_judge.py](../src/app/research/agents/rerank_judge.py) |
| `run_rerank()` orchestrator (Quant + Contrarian → judge) | [src/app/research/rerank.py](../src/app/research/rerank.py) |
| Batch fan-out (`rerank_tickers()`) | [src/app/research/scan_rerank.py](../src/app/research/scan_rerank.py) |
| CLI: `tsr scan --rerank` | [src/app/cli.py](../src/app/cli.py) |
| API: `POST /research/scan-rerank` | [apps/api/app/routes/research.py](../apps/api/app/routes/research.py) |

**Cost:** ~\$0.01-0.015 per ticker (2 Haiku + 1 Sonnet, all temperature=0).
A typical 20-ticker watchlist run is ~\$0.20-0.30.

**No caching this phase.** Every invocation hits the Anthropic API.
Caching at `(ticker, day_key)` is a deliberate Phase-3.5 follow-up if
daily usage justifies the migration cost.

**No frontend yet.** CLI + JSON API delivery only. Mounting on
`ScanStatusBoard.tsx` is a follow-up.
```

- [ ] **Step 2: Final test sweep**

```bash
.venv/bin/pytest -q 2>&1 | tail -3
```

Expected: 384+ passed (379 + 5 new rerank tests).

- [ ] **Step 3: Commit**

```bash
git add docs/recent-changes-2026-05-08.md
git commit -m "docs: §15 — watchlist scan reranking (Phase 3)

CLI tsr scan --rerank, API POST /research/scan-rerank, ~\$0.01-0.015 per
ticker. No caching this phase; flag for follow-up if daily usage warrants."
```

---

## Self-Review Notes

**Spec coverage:**
- Schema: Task 1.
- Rerank judge: Task 2.
- Orchestrator: Task 3.
- Batch fan-out: Task 4.
- CLI: Task 5.
- API: Task 6.
- Doc: Task 7.

**Type consistency:** `ScanRerankResult`, `RANKS`, `run_rerank`, `rerank_tickers`, `run_rerank_judge` — all consistent. `rank` is always one of the 4 string values; `lenses` is `list[LensView]`.

**Risk:** Task 5 (CLI) requires reading the existing scan command to integrate cleanly. The render adaptation depends on whether the scan command currently prints incrementally or at the end. Plan tells implementer to read first; small risk of needing additional plan adjustment.

**Risk #2:** Task 4 references `build_research_packet` from `app.research.context`. If the actual function name is different, the import + call need adjusting. Plan tells implementer to find and adapt.

**Placeholder check:** Step 5 of Task 5 references "Pattern A" / "Pattern B" — those are decision points based on what the implementer sees, not placeholders for code-to-fill-in. Acceptable per the skill since the right pattern is determined by reading the existing code.
