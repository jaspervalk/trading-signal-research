# ADR 0009 — Two-section restructure: Analysis and Portfolio Manager

**Status:** accepted
**Date:** 2026-08-04
**Supersedes:** the creator-first emphasis of [ADR 0005](0005-product-pivot-decision-support.md) — its decision-support core (rankings + evidence + confidence + N; no composite scores; user pulls the trigger; no auto-execution) is preserved, not revisited.
**Depends on:** [ADR 0007](0007-strategies-and-walkforward.md) (walk-forward harness), [ADR 0008](0008-action-signal-labeling.md) (action labels)
**Amends:** [docs/entry-exit-research-plan.md](../entry-exit-research-plan.md) — narrows its "position sizing out of scope" exclusion.

## Context

HEAD sat at 2026-05-10 with 36 dirty/untracked paths across four unrelated work threads — Quick-mode bug fixes, fundamentals enrichment, sentiment web-search, and an entire screener subsystem — plus roughly 119 uncommitted tests. The daily pipeline (`scripts/daily_run.sh`) has been dead since 2026-05-04; `lens_outcomes` is empty (0 rows against 136 snapshots); CI runs only a Playwright scaffold against playwright.dev, not this repo. The four-part audit behind this ADR (`docs/superpowers/plans/2026-08-04-restructure-roadmap.md`) is the source of record for these findings.

The audit found real, countable collisions, not just untidiness: three ticker-universe concepts (`universe.csv`, `screen_universe.csv`, `Watchlist`) with two same-named `load_universe` functions and a `BRK-B`/`BRK.B` format mismatch; four to six independent `yfinance` access paths with different caches; three fundamentals models (`ValuationPanel`, `TickerMetrics`, `FundamentalsExtended`); indicator math triplicated because the screener reimplements SMA/returns/RS instead of importing `analysis/indicators`; three candidate funnels (`analysis/scan`, `screener/pipeline`, `research/scan_rerank`); duplicated Wilson-CI and regime code. Dead weight accumulated alongside it: empty `modeling/`/`reporting/`, a dead `market/snapshots.py` against a 0-row `market_snapshots` table, a `tsr rank` stub, roughly eight dead API endpoints/client methods.

Underneath the mess, the product had already drifted from what ADR 0005 described. All recent work — screener, fundamentals enrichment, sentiment web-search, entry/exit research — is quant-first, not creator-first, yet the creator leaderboard remains the UI front door and `architecture.md` still describes the pre-pivot product. No ADR has been written since 0008 (2026-05-07). The creator-call backtest sample is 20 accepted calls against ADR 0007's `min_n_trades: 30` gate, meaning creator-strategy results are underpowered by construction and cannot currently support a decision. Separately, the operator's real broker (Revolut) has no API, so any notion of "what do I actually hold" has to be a manually maintained ledger — a concept the codebase has no home for today. `entry-exit-research-plan.md` currently declares position sizing out of scope, which conflicts with wanting the system to know what's already held.

## Decision

The project restructures into two sections.

### 1. Analysis — quant-first research engine

Screener → technicals/fundamentals → entry/exit research (Quick/Deep LLM, per ADR 0008's derivation discipline) → walk-forward backtests (ADR 0007). The YouTube-creator pipeline demotes to a **pluggable, off-switchable side feature** — one signal source among several, not the product's spine. Watchlist stays Analysis-owned: "tickers I'm scanning." It is a distinct concept from Portfolio holdings; watching a ticker does not imply holding it, and the two lists are never merged.

### 2. Portfolio Manager — manual position/trade ledger

The system of record for actual holdings. **One** stored table: `portfolio_trades`
(side/qty/price/currency/fees/traded_at/optional `eur_amount` — named to avoid
collision with `backtest/metrics.Trade`). **Positions are derived, not stored**:
folding the trade ledger chronologically with weighted-average cost is a pure
function, so holdings can never drift from the history that produced them. This
amends the original sketch of `Position` / `PortfolioTrade` / `CashFlow` tables —
`Position` is computed, and `CashFlow` is deferred with dividends and cash
balance (see the v1 scope in
[the design spec](../superpowers/specs/2026-08-04-portfolio-manager-design.md)).
Portfolio state feeds back into Analysis as scan priority (held tickers get research attention by default) and invalidation alerts (an entry/exit plan moving against a held position surfaces a flag) — a one-way bridge from Portfolio into Analysis's scan/alerting, not a merge of the two data models.

### 3. Creator strategies + per-call backtest: parked, not deleted

With 20 accepted calls against ADR 0007's `min_n_trades: 30` gate, the three creator strategies and the per-call outcome path cannot produce a statistically defensible result. They move into the side feature (`signals/creator/`) and stay dormant until the 341-document backlog (or a future Discord adapter) gives them a real sample. The walk-forward *harness* itself — metrics, calibration, persistence — moves to Analysis and takes a pluggable signal source, so quant strategies use the same proven machinery. The gold set (`data/gold/extraction_gold.jsonl`) and the evidence-quote extraction pipeline (ADR 0002) are preserved unchanged; nothing about extraction correctness is in question, only sample size.

### 4. Discord adapter stays on the side-feature roadmap

`docs/discord-adapter-plan.md` stands. The open question remains signal extraction in Discord noise, not whether to build the adapter — it plugs into `signals/` alongside the YouTube adapter via the existing `SourceAdapter` ABC (ADR 0001).

### Target module map

```
src/app/
  marketdata/        ← merged: yfinance client (bars+info+fundamentals+peers+calendar),
                       one cache policy, one rate limiter          [Analysis]
  analysis/          ← unchanged core: indicators, levels, setup, status, action, entry [Analysis]
  screener/          ← imports analysis.indicators + marketdata; config in settings.yaml [Analysis]
  research/          ← Quick/Deep LLM plans, lenses, rerank (as-is, well built) [Analysis]
  backtest/          ← walk-forward harness + metrics + calibration, pluggable signal source [Analysis]
  portfolio/         ← NEW: positions, trades, P&L, alerts          [Portfolio Manager]
  signals/creator/   ← sources, ingest, normalize, extract, aggregation, creator scoring,
                       per-call backtest, 3 parked strategies       [side feature, off-switchable]
```

### Amendment to entry-exit-research-plan.md

"Position sizing out of scope" is narrowed: **manual position tracking is now in scope** (Portfolio Manager records what is actually held, at what cost basis). **Automated position-sizing advice and auto-execution remain out of scope and forbidden**, unchanged from ADR 0005 §4 and ADR 0008 §4. The system records and surfaces holdings; it does not tell the operator how many shares to buy, and it never places an order.

## Consequences

**Staged execution (not part of this ADR's commitment, tracked in the roadmap):**
1. Plan 1 — stabilize and clean: commit the four dirty work threads, delete dead code/junk, real CI, this ADR.
2. Plan 2 — Analysis consolidation: one `marketdata` client, one universe module (fixes `BRK-B`/`BRK.B`, kills duplicate `load_universe`), screener imports `analysis/indicators`, screener gets an API route + web page, ticker page/nav re-centered on quant with the creator rail collapsible.
3. Plan 3 — Portfolio Manager: greenfield tables + migration, `tsr pf` CLI namespace, FastAPI `portfolio` router, `/` web page. (**Shipped ahead of Plan 2 at the owner's request**, 2026-08-04.) The scan-priority/invalidation-alert bridge into Analysis was scoped for this plan but was **not** implemented — still pending.
4. Plan 4 — side-feature packaging + docs: creator pipeline behind a source-agnostic seam, `architecture.md` rewritten from scratch, CLAUDE.md/README refreshed, ops decision on reviving the daily cron.

**Architectural:**
- `architecture.md` is now stale by this ADR's own admission and will be rewritten in Plan 4 — until then, ADRs (this one included) are the authoritative description of the target state, same precedence rule CLAUDE.md already applies.
- The restructure formalizes a direction the code had already begun: the sentiment lens already demotes creator claims to one input among several, and the screener's creator-coverage integration was already additive-only, not load-bearing. This ADR names the direction; it does not invent it.
- The walk-forward harness gains a second consumer (quant strategies) beyond the parked creator strategies it was built for — no interface change is anticipated, only a different signal-source implementation plugged into the existing pluggable seam from ADR 0007.

**Operational:**
- Portfolio Manager introduces manual data-entry as a workflow the operator has to actually do (no broker API to lean on). This is a real ongoing cost, accepted because Revolut has no API and the alternative — no ledger at all — means "what do I hold" stays untracked indefinitely.
- Reviving the daily cron and adding `score-lens-outcomes` (so `lens_outcomes` finally populates) is deferred to Plan 4 ops, not this ADR.

**Hard rules carried forward, not relaxed:**
- Decision-support framing (ADR 0005/0008): rankings + evidence + confidence + N, always; no composite scores; the user pulls the trigger; no order execution, ever — full stop, including from Portfolio Manager.
- UTC tz-aware datetimes; market math in `America/New_York`; no look-ahead (ADR 0003).
- Evidence-quote requirement for extraction (ADR 0002); the gold set remains the most important file in the repo.
- LLM entry/exit plans pick from deterministic candidates only, with the ±15% scaling clamp and 0.75×ATR minimum risk-distance guard (`docs/entry-exit-research-plan.md`).

## What this ADR is *not* doing

- Not deleting the creator pipeline, its ORM tables, or its tests. Parked means dormant and off-switchable, not removed.
- Not building any broker API integration. Revolut has none; Portfolio Manager entries are manual by design, not as a stopgap.
- Not committing to the specific file/module layout of Plans 2–4 beyond the target module map above — those are implementation plans, written and reviewed separately as each predecessor completes.
- Not reopening the "no naked buy/sell labels" question — ADR 0008's action-label mechanism is unaffected by this restructure.
- Not training the ML ranker. Still gated on strategy backtests + claim volume per ADR 0005, and now additionally on the quant walk-forward harness actually running against a pluggable strategy for a meaningful period.

## Things explicitly out of scope (V1)

- **Broker API integrations.** Revalidate only if the operator changes brokers to one with an API worth the integration cost.
- **Automated trading / auto-execution.** Forbidden outright, not deferred — see ADR 0005 §4, ADR 0008 §4, and the amendment above.
- **Automated position-sizing advice.** Portfolio Manager tracks what is held; it does not recommend how much to buy or sell.
- **ML ranker.** Gated per ADR 0005; this restructure does not change or accelerate that gate.
- **Reviving the daily cron / `score-lens-outcomes`.** Flagged as an open item, executed (if at all) in Plan 4 ops, not this ADR.
