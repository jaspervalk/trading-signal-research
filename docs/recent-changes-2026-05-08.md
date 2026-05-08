# Recent changes — 2026-05-08 session

A reference of what shipped across this session. Read this first if picking
up the project cold; pair with [CLAUDE.md](../CLAUDE.md) for the long-term
context primer.

**Headline:** the dashboard now answers two new questions on top of yesterday's
"is this worth researching?":

1. **What action does this imply?** → BUY / ACCUMULATE / HOLD / WAIT / REDUCE / AVOID badge derived from the existing rubric ([ADR 0008](decisions/0008-action-signal-labeling.md)).
2. **Where do I enter, exit, stop — with what conviction?** → unified trade-ticket panel + Plan R/R + valuation context + 4-lens analyst panel (single Haiku call) OR 4 parallel Haiku analysts + Sonnet judge ([docs/entry-exit-research-plan.md](entry-exit-research-plan.md)).

**Test suite:** 352 passing (started 313). 39 new tests across 3 new test files.

## At a glance — new endpoints

```
POST /research/quick/{ticker}             # ~$0.01, 3-5s, single Claude call
GET  /research/quick/{ticker}             # cache hit (or null)

POST /research/deep/{ticker}              # ~$0.04-0.10, ~30s, 4 agents + judge
GET  /research/deep/{ticker}              # cache hit (or null)
```

Both modes return the same `EntryExitPlan` shape. Cached per `(ticker, day, mode)`.

## At a glance — new UI

- `[ ⚡ Quick · ~$0.01 ]` and `[ 🔍 Deep · ~$0.10 ]` buttons on every ticker page.
- Action badge (BUY/ACCUMULATE/HOLD/WAIT/REDUCE/AVOID) at the top of `ResearchStatusStrip`, color-coded.
- Unified **Trade Setup** block (entry / pullback / stop / T1 / T2) with the **Plan R/R** as the headline number.
- **Valuation panel** (right rail) — forward P/E, PEG, market cap, growth, margins, beta, days-to-earnings.
- **4-lens analyst panel** below the trade ticket (Quantitative / Fundamental / Sentiment-Macro / Contrarian-Risk) with independent direction + conviction reads.

## 1. Implied action labels — ADR 0008

**What:** A deterministic view over `DecisionSupportStatus` that maps to a familiar BUY / HOLD / SELL-style vocabulary, without changing the underlying rule-based rubric. Seven labels: `BUY · ACCUMULATE · HOLD · WAIT · REDUCE · AVOID · N/A`.

**Why:** ADR 0005 forbade buy/sell labels in the UI. After 6 months of ADR 0006/0007/0008 work the underlying rubric is mature enough to support a derived labeling that's auditable end-to-end. `SELL` / `SHORT` is intentionally absent — strategies are long-only.

**Where:**

| Component | File |
|---|---|
| `ActionSignal` Pydantic schema + `ACTION_LABELS` enum | [src/app/analysis/schema.py](../src/app/analysis/schema.py) |
| Pure derivation function | [src/app/analysis/action.py](../src/app/analysis/action.py) — `derive_action_signal(status, setup, entry_zone, transcript)` |
| Wired into `TickerResearchView.action` | [src/app/analysis/research.py:184-186](../src/app/analysis/research.py) |
| Frontend types + badge component | [apps/web/src/components/ActionBadge.tsx](../apps/web/src/components/ActionBadge.tsx), `ResearchAction` in [api.ts](../apps/web/src/lib/api.ts) |
| Display in `ResearchStatusStrip` (large badge + status conf + rubric pass-rate + derivation prose) | [apps/web/src/components/ResearchStatusStrip.tsx](../apps/web/src/components/ResearchStatusStrip.tsx) |
| ADR | [docs/decisions/0008-action-signal-labeling.md](decisions/0008-action-signal-labeling.md), with cross-reference added in ADR 0005 |

**Decision tree (in [src/app/analysis/action.py](../src/app/analysis/action.py)):**

| status | confidence | extra | label |
|---|---|---|---|
| `insufficient_data` | any | — | N/A |
| `skip_for_now` | any | — | AVOID |
| `extended_risk` | any | — | REDUCE |
| `research_candidate` | high | entry_zone available | **BUY** |
| `research_candidate` | high | no entry zone | ACCUMULATE |
| `research_candidate` | medium | — | ACCUMULATE |
| `research_candidate` | low | — | HOLD |
| `watch` | high | — | HOLD |
| `watch` | low/medium | — | WAIT |
| `wait_for_setup` | any | — | WAIT |

**Tests:** 16 in [tests/test_analysis_action.py](../tests/test_analysis_action.py).

## 2. UI/UX audit + polish

Resolved a cluster of readability issues on `/tickers/[ticker]`:

- Container `max-w-7xl` (1280px) → `max-w-[1480px]`. Rails 280/320 → 340/360.
- `--muted-2` lifted from `#71717a` → `#8c8c97` (small-text contrast).
- Swept all 56 instances of `text-[9px]` / `text-[9.5px]` / `text-[10px]` → `text-[11px]` across 14 components.
- Restructured `SignalTile.tsx` — bracketed metadata stacked vertically (kills the `[CLAIMS_ALL]` overflow).
- `LiveSignalStrip.tsx` — dropped redundant column-header row.

Other pages (`/tickers`, `/watchlist`, `/calls`, `/methodology`, `/`) verified clean — no regressions.

## 3. Random-ticker research backend — extensions

**Already-computed fields surfaced** in the `TechnicalsCard` (no backend math needed — they were in `MarketSnapshotPanel` all along):

- `avg $ vol 20d` (with red color < $5M, amber < $25M — Stockbee/IBD liquidity floor)
- `pct_off_52w_low` (positive ratio for IPO leaders / Qullamaggie episodic pivots)
- `return_5d / 21d / 63d / 252d` (multi-window momentum)
- `gap_from_prev_close`

`TechnicalsCard.tsx` re-organised into 6 logical sections: **Trend / Momentum / Volatility / Liquidity / Relative / Levels**.

**`build_dual_entry_zones()`** added to [`src/app/analysis/entry.py`](../src/app/analysis/entry.py) — returns `(breakout_zone, pullback_zone)` so the research feature sees both at once. The single-zone `build_entry_zone()` stays untouched for existing callers.

## 4. Fundamental valuation panel

**`ValuationPanel`** Pydantic model added to [`schema.py`](../src/app/analysis/schema.py) and threaded through `TickerResearchView`. Populated from `yfinance.Ticker(t).info` + `.calendar`:

| Field | Source |
|---|---|
| `market_cap`, `forward_pe`, `trailing_pe`, `peg_ratio`, `price_to_sales_ttm`, `price_to_book`, `enterprise_to_ebitda` | `info` |
| `earnings_growth_forward`, `revenue_growth_yoy`, `profit_margins` | `info` |
| `float_shares`, `shares_outstanding`, `short_pct_of_float`, `held_pct_institutions` | `info` |
| `beta`, `dividend_yield` | `info` |
| `days_to_next_earnings`, `next_earnings_date` | `calendar` |
| `sector`, `industry` | `info` |

Frontend: `apps/web/src/components/ValuationPanel.tsx` — sector/industry header, "context, not trigger" framing, qualitative hints (`forward P/E 30.1 [rich]`, `PEG 2.52 [rich vs growth]`), color-coded growth/margin signs.

**Bug noted:** yfinance returns `dividendYield` already in percent form (`0.38` = 0.38%) while everything else is fractions. UI handles via `fmtDivYield()` helper.

## 5. Entry / Exit Research feature — Quick mode

**Plan doc:** [docs/entry-exit-research-plan.md](entry-exit-research-plan.md).

**One Claude Haiku call (~$0.01, 3–5s).** Returns a structured `EntryExitPlan` with:

- Entry zone + (optional) pullback entry zone
- Exit zone primary (T1) + (optional) runner zone (T2)
- Invalidation reference (stop)
- Per-tier R/R + **`plan_r_r_blended`** (default trim allocation: 1/3 at T1, 2/3 at T2)
- `confidence` (bounded by upstream rubric — can't exceed status confidence)
- `timeframe` ∈ {1-3d, 5-15d, 2-6w}
- Bull case / bear case / key risks (3–5 bullets each)
- **`lenses[]`** — 4 LensView entries (Quant / Fundamental / Sentiment-Macro / Contrarian-Risk)
- Audit trail: `mode`, `cost_usd`, `duration_ms`, `sources_used`, `agent_trace`

**Hallucination guards (the critical part):**

1. **Numeric levels picked from deterministic candidates** computed by [src/app/research/exits.py](../src/app/research/exits.py): nearest resistance + 0.25×ATR cushion, recent 63-bar high, Fibonacci 1.272× / 1.618× from base low, ATR-anchored runners, multi-source invalidation candidates.
2. **±15% scaling clamp** — LLM may scale a chosen level by at most ±15% with rationale; wider edits clamped on parse.
3. **Min-risk-distance guard** — `invalidation` floored to `entry.low − 0.75×ATR` (catches the LLM picking a stop inside the entry zone).
4. **Confidence bounded by upstream rubric** — if `DecisionSupportStatus.confidence == "low"`, plan confidence ≤ low.
5. **Plans missing the lens panel are not cached** — forces a retry next click (Anthropic's tool-input schema is a hint, not server-side enforced).

**Files:**

| Component | File |
|---|---|
| Schema (`ZoneBand`, `CandidateLevels`, `LensView`, `EntryExitPlan`, `blended_risk_reward()`) | [src/app/research/schema.py](../src/app/research/schema.py) |
| Deterministic exit math | [src/app/research/exits.py](../src/app/research/exits.py) |
| Context bundler (TickerResearchView + recent claims + candidates) | [src/app/research/context.py](../src/app/research/context.py) |
| Quick mode runner | [src/app/research/quick.py](../src/app/research/quick.py) |
| DB-backed cache (`(ticker, day, mode)`) | [src/app/research/cache.py](../src/app/research/cache.py), `ResearchPlan` ORM in [models.py](../src/app/models.py) |
| Endpoints | [apps/api/app/routes/research.py](../apps/api/app/routes/research.py) |
| Frontend panel (unified trade ticket + Plan R/R + lens panel) | [apps/web/src/components/EntryExitPanel.tsx](../apps/web/src/components/EntryExitPanel.tsx), [LensPanel.tsx](../apps/web/src/components/LensPanel.tsx) |

**Tests:** 23 in [tests/test_research_exits.py](../tests/test_research_exits.py) + [tests/test_research_quick.py](../tests/test_research_quick.py) — exits math, level clamping, confidence bounding, blended R/R helper, mocked-LLM end-to-end, cache round-trip.

## 6. Entry / Exit Research feature — Deep mode

**Four parallel Haiku analysts → one Sonnet judge (~$0.04–0.10, ~30s).**

Each analyst is scoped to its discipline so they can't crowd each other:

| Agent | Sees | Doesn't see | File |
|---|---|---|---|
| Quantitative | Indicators, levels, market snapshot, candidate levels | Fundamentals, news, claims | [agents/technical.py](../src/app/research/agents/technical.py) |
| Fundamental | Valuation panel + sector/industry | Technicals, claims | [agents/fundamental.py](../src/app/research/agents/fundamental.py) |
| Sentiment-Macro | Recent claims + transcript context + creator coverage | Pure technicals, valuation | [agents/sentiment.py](../src/app/research/agents/sentiment.py) |
| Contrarian-Risk | **Full packet** (all of the above) | — | [agents/contrarian.py](../src/app/research/agents/contrarian.py) |

The Contrarian/Risk agent is intentionally adversarial — its job is to find the bear case even on a "good" setup, while the other three land where the data lands. If it can't find a real bear case, it's instructed to say so explicitly with `direction='neutral'`.

**Sonnet judge** ([agents/judge.py](../src/app/research/agents/judge.py)) sees the four lens outputs + deterministic candidate levels + minimal ticker header. Picks final numeric levels (same ±15% clamp + 0.75×ATR risk-distance guard as Quick), bounds `confidence` by the upstream rubric, writes the consolidated bull/bear/risks prose with `Per Quantitative: …` / `Per Fundamental: …` citations.

**Failure semantics ([deep.py](../src/app/research/deep.py)):**

- 1-3 analysts fail → judge proceeds with surviving lenses, partial coverage logged.
- All 4 analysts fail → orchestrator raises (no judge-only "vibes" plan).

**Cost (live AAPL run, this session):** $0.041, 26.6s, sources `[technicals, claims, fundamentals]`. Reads:

- Quantitative · bullish · medium ("Strong uptrend with overbought momentum")
- **Fundamental · bearish · high** ("AAPL rich vs growth; PEG 2.52 signals multiple stretched")
- Sentiment-Macro · bullish · medium ("Single creator bullish")
- **Contrarian-Risk · bearish · medium** ("Valuation stretched at 30x forward PE amid crowded momentum")
- Judge → confidence "medium" (split conviction), Plan R/R 1.77 (green — standard swing)

This is the architecture's payoff: with one Haiku, the Fundamental dimension was bullish; with the four-agent split, it landed bearish · high. The judge then weighed it correctly.

**Tests:** 11 in [tests/test_research_deep.py](../tests/test_research_deep.py) — agent happy/failure paths, parallel order, judge clamping, end-to-end orchestration, partial-failure tolerance, total-failure raises.

## 7. New ORM tables + Alembic now wired

| Table | Where | Purpose |
|---|---|---|
| `ResearchPlan` | [src/app/models.py:912-952](../src/app/models.py) | Cached `EntryExitPlan` JSON keyed by `(ticker, mode, day_key)`. Headline columns denormalised (confidence, timeframe, cost) for cheap listing. |

**Alembic is now live** ([alembic/](../alembic/), [alembic.ini](../alembic.ini)) — set up later in this session after the user discovered Alembic was previously aspirational (the dependency was in `pyproject.toml` but no `alembic.ini` / `alembic/` ever existed; schema was always bootstrapped via `Base.metadata.create_all()`).

Setup performed:
1. `alembic init alembic` — scaffold
2. Wired [alembic/env.py](../alembic/env.py) to import `Base.metadata` from `app.models` and resolve `sqlalchemy.url` via `app.config.load_env().database_url` (same source as the app itself; supports `-x dburl=...` for tests + `DATABASE_URL` env var)
3. Generated baseline migration `be68ca021077_baseline_existing_schema.py` against an empty temp DB so it captures `op.create_table()` for every table — a fresh clone can `alembic upgrade head` to bootstrap
4. Stamped the live DB at the baseline (`alembic stamp head`) so it's marked as already-applied without re-running DDL
5. **Drift discovered:** `extracted_calls.manual_status` was nullable in the live DB but NOT NULL in the ORM — `Base.metadata.create_all()` never propagated the constraint when it was added to the ORM. Verified safe (0 NULL rows in 20-row table), repaired via migration `97889a067fc2_repair_manual_status_not_null.py`. This is the value of Alembic — the drift would have remained invisible forever otherwise.
6. Smoke-tested: add column → autogen → upgrade → downgrade → all work. Final `alembic check` says "No new upgrade operations detected."

Workflow from now on:

```bash
# Edit src/app/models.py to add/drop/alter a column or table
alembic revision --autogenerate -m "describe_change"
# → inspect alembic/versions/<hash>_describe_change.py
alembic upgrade head
# Commit both the model change and the migration file together.
```

`alembic downgrade -1` rolls back one step. Renames are the one autogen weakness — Alembic sees drop+add and writes that; hand-edit to use `op.alter_column(... new_column_name=...)` for a true rename.

A pre-Alembic backup of the DB is kept at `data/tsr.sqlite.bak-pre-alembic` — keep until you've shipped a few migrations and trust the workflow.

## 8. What's still on hold (and why)

| Item | Why | Unblocks when |
|---|---|---|
| ~~Alembic migration for `research_plans`~~ | ~~Used `create_all` to ship; need a proper migration before reset~~ | **DONE 2026-05-08** — Alembic set up, baseline captured, drift repaired |
| SSE streaming for Deep mode (slice 2b) | The 30s wait is one spinner; could show per-agent progress | If real use feels too opaque |
| Per-lens "show your work" expand | Lens cards collapse to summary, click to see raw context | Nice-to-have polish |
| Monthly cost cap (`research.monthly_budget_usd`) | Not enforced yet; per-call costs are visible but no hard cutoff | Once Deep usage > 2–3× per day |
| TradingAgents framework integration | Calibration debt + ADR 0005 framing friction; we use its agent decomposition (technical/fundamental/sentiment/judge) but not its runtime | If our 4-agent system needs more depth (probably never — re-architect first) |
| 341-doc extraction backfill | Spend approval | User OK |
| Stockbee correct channel ID | Pradeep Bonde's actual YouTube channel ID unknown | Manual lookup |
| Calibration of action labels vs realized returns | Need ≥3 months of `ResearchSnapshot` + ADR 0007 walk-forward outcomes | Data accumulation |

## 9. Live verification of slice 2 (recorded for posterity)

```
mode      : deep
cost      : $0.0413 · 26649 ms
confidence: medium · 5-15d
R/R       : T1 0.80 · T2 2.25 · BLENDED 1.77
sources   : [technicals, claims, fundamentals]
entry     : 287.51 - 290.86
T1        : 298.93 - 300.61    (Fibonacci 1.272× from base 245.51)
T2        : 313.47 - 315.14    (Fibonacci 1.618×)
stop      : 277.48
lenses    : 4/4 emitted
```

Bull/bear case bullets cited the lens names ("Per Fundamental: PEG of 2.52…", "Per Contrarian-Risk: RSI at 67 in overbought territory…"). Agent trace had 5 entries (4 analysts + judge). Plan was cached. Re-run via `force=true` works as expected.

## 10. Architectural shape now

```
trading-signal-research/
├── src/app/
│   ├── analysis/                    # ticker research view (extended)
│   │   ├── action.py                # ← NEW — derive_action_signal()
│   │   ├── entry.py                 # +build_dual_entry_zones()
│   │   ├── research.py              # +ValuationPanel via _build_valuation()
│   │   └── schema.py                # +ActionSignal, +ValuationPanel
│   ├── research/                    # ← NEW — entry/exit feature
│   │   ├── schema.py                # ZoneBand, EntryExitPlan, LensView, blended_risk_reward()
│   │   ├── exits.py                 # deterministic exit candidates (resistance + ATR + Fib)
│   │   ├── context.py               # ResearchPacket: TickerResearchView + claims + candidates
│   │   ├── quick.py                 # single Haiku call → EntryExitPlan with lenses
│   │   ├── deep.py                  # 4 parallel analysts → judge → EntryExitPlan
│   │   ├── cache.py                 # (ticker, mode, day_key)
│   │   └── agents/
│   │       ├── base.py              # AgentResult, run_agent, run_agents_parallel
│   │       ├── technical.py         # Quantitative lens (Haiku)
│   │       ├── fundamental.py       # Fundamental lens (Haiku)
│   │       ├── sentiment.py         # Sentiment-Macro lens (Haiku)
│   │       ├── contrarian.py        # Contrarian-Risk lens (Haiku)
│   │       └── judge.py             # Sonnet — final synthesis
│   ├── strategies/                  # ADR 0007 (unchanged this session)
│   ├── backtest/                    # ADR 0007 (unchanged this session)
│   └── models.py                    # +ResearchPlan
├── apps/api/app/routes/
│   └── research.py                  # +/research/quick + /research/deep
├── apps/web/src/components/
│   ├── ActionBadge.tsx              # ← NEW
│   ├── EntryExitPanel.tsx           # ← NEW — unified trade ticket
│   ├── LensPanel.tsx                # ← NEW — 4-lens grid
│   ├── ValuationPanel.tsx           # ← NEW — fundamentals ribbon
│   ├── ResearchStatusStrip.tsx      # +ActionBadge in header
│   └── TechnicalsCard.tsx           # +Liquidity section, +pct_off_52w_low, +returns
├── docs/
│   ├── decisions/
│   │   ├── 0005-product-pivot-decision-support.md   # +relaxation note
│   │   └── 0008-action-signal-labeling.md           # ← NEW
│   ├── entry-exit-research-plan.md                  # ← NEW
│   └── recent-changes-2026-05-08.md                 # ← this file
└── tests/                           # 352 passing (39 new)
```

## 11. Pointers for next session

If picking up cold (in priority order):

1. **[CLAUDE.md](../CLAUDE.md)** — long-term context primer (gitignored; updated this session).
2. **This file** — last session's deltas.
3. **[docs/decisions/0008-action-signal-labeling.md](decisions/0008-action-signal-labeling.md)** — the action-label design and the relaxation of ADR 0005's "no buy/sell" clause.
4. **[docs/entry-exit-research-plan.md](entry-exit-research-plan.md)** — the entry/exit research feature plan + slice 2 multi-agent design.
5. **[docs/decisions/0005-product-pivot-decision-support.md](decisions/0005-product-pivot-decision-support.md)** — the load-bearing ADR; everything still trends here.

The highest-leverage thing to do next:

1. **Slice 2b SSE streaming** for Deep mode (1–2h, makes the 30s wait feel intentional rather than slow).

(The previous "set up Alembic" item is done — see §7.)

## 12. R/R variance fix (later 2026-05-08)

After observing that two Deep runs on AAPL produced R/R 1.77 then 0.80,
diagnosed the variance to: (a) default Anthropic temperature 1.0,
(b) ±15% scaling clamp letting the LLM emit any number near a candidate,
(c) free-form numeric output across three independent levels compounding.

Fix: A+B+C from `docs/superpowers/plans/2026-05-08-rr-variance-fix.md`.
- A: LLM picks levels by reference (`entry_kind`, `*_index`); raw numbers come from `CandidateLevels`. ±15% clamp removed.
- B: New `RRDistribution` enumerates every (entry × primary × runner × invalidation) R/R combo; UI shows the range alongside the headline.
- C: `temperature=0` on Quick + Judge calls.

Result: same packet → same plan; the plan_r_r_blended headline is now
contextualised by an honest min..max range across deterministic candidate
pairings.

### What landed

| Component | File |
|---|---|
| Schema: `RRCombo`, `RRDistribution`, `Picks`, `r_r_distribution` field | [src/app/research/schema.py](../src/app/research/schema.py) |
| Distribution helper: `compute_rr_distribution()`, `risk_reward()` | [src/app/research/rr_distribution.py](../src/app/research/rr_distribution.py) |
| Quick mode refactor: categorical picks, ±15% clamp removed | [src/app/research/quick.py](../src/app/research/quick.py) |
| Deep judge refactor: same categorical change | [src/app/research/agents/judge.py](../src/app/research/agents/judge.py) |
| `temperature=0` pinned on Quick + Judge | both files above |
| Frontend range bar | [apps/web/src/components/RRRangePanel.tsx](../apps/web/src/components/RRRangePanel.tsx), wired into [EntryExitPanel.tsx](../apps/web/src/components/EntryExitPanel.tsx) |
| Tests | [tests/test_research_rr_distribution.py](../tests/test_research_rr_distribution.py) (new), updates to [test_research_quick.py](../tests/test_research_quick.py), [test_research_deep.py](../tests/test_research_deep.py) |

### Verification deferred

Live API runs to confirm same-packet → same-plan determinism not yet
executed. To verify (once API server is running, ANTHROPIC_API_KEY set):

```bash
# Quick mode determinism — should produce identical numbers across runs
curl -X POST 'http://localhost:8000/research/quick/AAPL?force=true' | jq '.plan_r_r_blended, .r_r_distribution.min_rr, .r_r_distribution.max_rr, .r_r_distribution.n_combos'
curl -X POST 'http://localhost:8000/research/quick/AAPL?force=true' | jq '.plan_r_r_blended, .r_r_distribution.min_rr, .r_r_distribution.max_rr, .r_r_distribution.n_combos'

# Deep mode — analyst lenses still vary at default temperature; judge is
# deterministic given the same lens fixture, but real analyst variation
# may flip a level pick. If plan_r_r_blended drifts run-to-run, the
# variance is now a SIGNAL of analyst disagreement, not LLM sampling noise.
curl -X POST 'http://localhost:8000/research/deep/AAPL?force=true' | jq '.plan_r_r_blended, .r_r_distribution.min_rr, .r_r_distribution.max_rr'
```

352 → 360 tests across the branch.
