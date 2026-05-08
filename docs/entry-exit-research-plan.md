# Entry / Exit Research feature — design memo

**Date:** 2026-05-07
**Status:** plan accepted; Phase 1 in progress
**Related ADRs:** [0005 (decision-support framing)](decisions/0005-product-pivot-decision-support.md), [0008 (action labels)](decisions/0008-action-signal-labeling.md)
**Related code:** [`src/app/analysis/entry.py`](../src/app/analysis/entry.py), [`src/app/analysis/levels.py`](../src/app/analysis/levels.py), [`src/app/extract/llm_extractor.py`](../src/app/extract/llm_extractor.py)

A user-driven "Quick read" / "Deep research" feature that produces a structured entry / pullback / exit / stop plan for a ticker, combining deterministic math with LLM synthesis over technicals + creator claims + news.

## Context

The dashboard answers "is this worth researching?" (BUY / HOLD / WAIT / etc.). The user wants the next step: "given that it is, where do I enter, where do I trim, where am I wrong?" — with reasoning, not just numbers.

Most of the numeric work is already deterministic in the codebase ([`entry.py`](../src/app/analysis/entry.py) for entry zones, [`levels.py`](../src/app/analysis/levels.py) for support/resistance, [`indicators.py`](../src/app/analysis/indicators.py) for ATR/RSI). The current panel emits **one** entry zone (breakout *or* pullback) and **no** exit zone. The gap is:

1. Both entry types side-by-side when both apply
2. Structured exit zones (first take-profit, runner)
3. A narrative layer that synthesises technicals + recent creator claims + news context
4. Per-call cost / latency control because LLM calls are not free

## TL;DR

**Two buttons. Both grounded in the existing `TickerResearchView`. Don't run TradingAgents as a runtime — steal its agent decomposition (Technical / News / Sentiment / Judge) but inject our own data and our own vocabulary.** ~70% of what the user wants is already deterministic math; the LLM agents add narrative + qualitative risk + news synthesis on top, not the numbers themselves.

## One button vs two — verdict: two

Cost asymmetry is real (~10×) and use cases differ.

| Mode | Latency | Cost | When you click it |
|---|---|---|---|
| Quick read | 3–5s | ~$0.01–0.02 | Scanning a watchlist, "is this worth thinking about?" |
| Deep research | 30–60s | ~$0.10–0.15 | Sized, serious, "I'm about to enter — give me everything" |

A single button forces always-pay or always-shallow. Two buttons give a dial. Decision-fatigue risk is mitigated by making Quick the primary action and Deep an explicit opt-in with cost shown next to it.

## Module layout

```
src/app/research/                    # new module — sibling of analysis/
  __init__.py
  schema.py        — ZoneBand, ResearchExitZones, EntryExitPlan, AgentNote
  exits.py         — deterministic exit-zone math (resistance + ATR + fib)
  context.py       — ResearchPacket: gather all inputs once
  quick.py         — single Claude call (Haiku) → EntryExitPlan
  deep.py          — Phase 2: agent orchestration → EntryExitPlan
  agents/          — Phase 2
    base.py        — Agent ABC, JSON-schema-constrained tool use
    technical.py   — sees TickerResearchView
    sentiment.py   — sees TickerSignal + recent Claims
    news.py        — sees yfinance.news (last 14d)
    judge.py       — synthesises into final EntryExitPlan
  cache.py         — DB-backed (ResearchPlan table, keyed by ticker+day+mode)
  costs.py         — runtime $ tracking, monthly cap
```

```
apps/api/app/routes/research.py      # extended
  POST /research/quick/{ticker}      → 200 EntryExitPlan
  POST /research/deep/{ticker}       → SSE stream → 200 EntryExitPlan  (Phase 2)
  GET  /research/cached/{ticker}     → most recent cached plan
```

```
apps/web/src/components/
  EntryExitPanel.tsx     — replaces / extends EntryZoneCard
  ResearchActions.tsx    — the two buttons
  ResearchAgentTrace.tsx — collapsible per-agent reasoning  (Phase 2)
```

## Output schema (returned by both Quick and Deep)

```python
class ZoneBand(BaseModel):
    low: float
    high: float
    method: str        # "63-bar high + 0.5×ATR", "resistance + 1×ATR", "Fib 1.272"
    rationale: str

class EntryExitPlan(BaseModel):
    # Numeric levels — deterministic, LLM may edit by ±15% with rationale
    entry_zone: ZoneBand              # primary (breakout or current)
    pullback_entry_zone: ZoneBand | None
    exit_zone_primary: ZoneBand       # first take-profit
    exit_zone_runner: ZoneBand | None # extended target
    invalidation: float               # stop-loss reference
    risk_reward_primary: float
    risk_reward_runner: float | None

    # Qualitative — LLM-driven
    confidence: Literal["low","medium","high"]
    timeframe: Literal["1-3d","5-15d","2-6w"]
    bull_case: list[str]
    bear_case: list[str]
    key_risks: list[str]

    # Audit trail
    mode: Literal["quick","deep"]
    cost_usd: float
    duration_ms: int
    sources_used: list[str]           # ["technicals","claims","news","fundamentals"]
    agent_trace: list[AgentNote] | None  # only populated in deep mode
    disclaimer: str                   # standard ADR 0005 line
```

**Critical design choice — LLM edits, doesn't generate:** numeric fields come from deterministic math; the LLM sees proposed levels and can override **with rationale, by ±15% max**. A wider override is rejected ("level outside reasonable band"). This is the biggest hallucination guard.

## Agent flow — Deep mode (Phase 2)

```
                  ┌─────────────────────────┐
                  │ context.gather(ticker)  │  ← deterministic
                  │ • TickerResearchView    │
                  │ • last 30d Claims       │
                  │ • last 14d yfinance.news│
                  │ • yfinance.info         │
                  │ • candidate_levels()    │  ← from exits.py
                  └────────┬────────────────┘
                           │
            ┌──────────────┼──────────────┐  parallel
            ▼              ▼              ▼
      Technical        Sentiment        News
       (Haiku)         (Haiku)         (Haiku)
            └──────────────┼──────────────┘
                           ▼
                       Judge (Sonnet)         ← single synthesis
                           │
                           ▼
                    EntryExitPlan
```

Each analyst returns a strictly-typed `*View` model with `confidence`, `bull_points`, `bear_points`, `mind_change`. Judge sees all three views + deterministic candidate levels, picks final levels, writes the lists.

The bull-vs-bear debate round from the TradingAgents paper is intentionally dropped: each analyst already emits both sides, and adding a debate round doubles cost. Add it back if calibration testing shows the Judge is biased.

## Token & cost specifics

Anthropic pricing (Claude Haiku 4.5: $1/M in, $5/M out; Sonnet 4.6: $3/M in, $15/M out):

| Component | Tokens (in / out) | Model | Cost |
|---|---|---|---|
| Quick: single call | 4K / 1.5K | Haiku | $0.012 |
| Deep: 3 analysts (parallel) | 5K×3 / 1.5K×3 | Haiku | $0.038 |
| Deep: Judge | 12K / 2K | Sonnet | $0.066 |
| **Deep total** | | | **~$0.10–0.15** |

**Cache aggressively:** same `(ticker, calendar_day, mode)` is free. Same ticker on a new day where no new headlines/claims arrived: only News/Sentiment invalidate, not Technical.

**Runtime cap:** [`configs/settings.yaml`](../configs/settings.yaml) gets `research.monthly_budget_usd: 30`; route returns 429 once exceeded. The current month's spend is shown next to the Deep button.

## Frontend UX

Two buttons next to `WatchlistButton` on the ticker page:

```
[ ⚡ Quick read · ~$0.01 ]      [ 🔍 Deep research · ~$0.10, ~45s ]
```

- Quick: spinner → result inline, replaces / expands the existing `EntryZoneCard`
- Deep: side drawer; streams agent progress; final structured plan with collapsible per-agent reasoning

Both can save to the existing `Annotations` table per-ticker. `mode`, `cost_usd`, `duration_ms` are visible on every result for an honest audit trail.

## Why not run TradingAgents as a runtime

1. **State ownership conflict** — it persists to `~/.tradingagents/memory/`; we already have `Annotations` / `ResearchSnapshot`.
2. **Data injection is awkward** — its analysts pull their own data; forcing them to use our `TickerSignal` + `Claim` means rewriting them anyway.
3. **Vocabulary mismatch** — it emits BUY/SELL with numerical positions; no concept of our `EntryZoneCandidate` / research-zone framing.
4. **Heavy deps** — Python 3.13 + LangGraph + Docker. Project is 3.11, FastAPI, no LangGraph.

What's useful: the [arxiv paper (2412.20138)](https://arxiv.org/abs/2412.20138) has the prompt templates. Read them, adapt the technical / sentiment / news prompts to our schema, throw away the rest.

## Safety / disclaimers

1. Every `EntryExitPlan` carries the ADR 0005 disclaimer string — already on `TickerResearchView`, threaded through.
2. Numeric levels are clamped: LLM may adjust deterministic candidates by ±15% max with rationale. Wider → rejected.
3. Confidence is bounded by the existing rubric: if `DecisionSupportStatus.confidence == "low"`, the plan's confidence is also bounded to ≤ medium. Plan's confidence is `min(LLM_claimed, status_based)`.
4. The action label from ADR 0008 (BUY / HOLD / etc.) stays separate. Entry/Exit panel sits next to it.

## Phased implementation

### Phase 1 — MVP Quick button (~1 day)

1. `src/app/research/schema.py` — `ZoneBand`, `ResearchExitZones`, `EntryExitPlan`, `AgentNote`
2. `src/app/research/exits.py` — deterministic exit math: nearest resistance, +1.5×ATR, +Fib 1.272 / 1.618 from base low → primary + runner zones
3. Extend [`entry.py`](../src/app/analysis/entry.py) to emit *both* breakout and pullback when both apply (currently picks one)
4. `src/app/research/context.py` — `ResearchPacket` gathers `TickerResearchView` + recent `Claim`s + deterministic candidate levels
5. `src/app/research/quick.py` — single Claude (Haiku) call, Anthropic tool-use schema-constrained, returns `EntryExitPlan`
6. `src/app/research/cache.py` + `ResearchPlan` ORM table
7. `POST /research/quick/{ticker}` endpoint, with cache + cost tracking
8. Frontend: `EntryExitPanel` + `ResearchActions` + Quick button on `/tickers/[ticker]`
9. Tests: deterministic math + mocked-LLM end-to-end (no live API in CI)

### Phase 2 — Deep mode (~1.5 days, after Phase 1 reads well)

10. `agents/base.py` — Agent ABC, structured-output helper
11. Three analyst agents (Technical / Sentiment / News) with `*View` outputs
12. `agents/judge.py` — synthesis
13. SSE streaming endpoint, agent-trace UI
14. Monthly cost cap (hard cutoff at threshold)

### Phase 3 — Calibration / polish (later, when there's data)

15. Track entry/exit plans vs actual price action N days later (extension of `ResearchSnapshot` pattern)
16. Compare Quick vs Deep accuracy on a hand-graded set of 20–30 plans
17. If Deep isn't measurably better, drop it. If much better, add bull-vs-bear debate round back.

## Open questions / deferred

- **News source:** Phase 1 uses `yfinance.news` (free, recent ~10 headlines). If quality is poor, evaluate Alpha Vantage / Marketaux / FinnHub in Phase 2.
- **Multi-timeframe:** all current math is daily-bar. 1h-bar entry refinement deferred until daily proves out.
- **Position sizing:** explicitly out of scope. The plan reports R/R; the operator decides size.
- **Auto-execution:** explicitly forbidden. No order routing here, ever.
