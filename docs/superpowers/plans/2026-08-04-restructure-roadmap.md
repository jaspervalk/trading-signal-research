# Restructure Roadmap — Analysis + Portfolio Manager

**Date:** 2026-08-04
**Status:** approved direction; Plan 1 written, Plans 2–4 to be written as predecessors complete.

## Goal

Turn `trading-signal-research` into two clean sections:

1. **Analysis** — a quant-first research engine: screener → technicals/fundamentals → entry/exit research (Quick/Deep LLM) → walk-forward backtests. The YouTube-creator algorithm is demoted to a **side feature**: one pluggable signal source among several, able to be switched off without breaking anything else.
2. **Portfolio Manager** — a manual ledger of real positions and trades (Revolut has no API; entries are manual). Becomes the system of record for holdings; the analysis side consumes it ("what do I hold" gets priority research + invalidation alerts).

## Decisions locked (Jasper, 2026-08-04)

- **(a) Watchlist vs Portfolio: keep both, distinct.** Watchlist = "tickers I'm scanning" (Analysis-owned). Portfolio = "things I hold" (new tables). Watching ≠ holding.
- **(b) Creator strategies + per-call backtest: park, don't delete.** With 20 accepted calls against a `min_n_trades: 30` gate they cannot produce significant results. The walk-forward *harness* moves to Analysis with a pluggable signal source; the three creator strategies and per-call outcome path stay in the side feature, dormant until the data backlog (341 unextracted docs, Discord later) gives them a real sample. The gold set and evidence-quote pipeline are preserved.
- **(c) Discord: still on the roadmap** (side-feature scope). `docs/discord-adapter-plan.md` stands; the open question is signal extraction/location in Discord noise, not whether to build the adapter.

## Why (audit summary, 2026-08-04)

Four parallel audits found the "colliding features" are real and countable:

- **Repo state:** HEAD = 2026-05-10; 36 dirty/untracked paths holding four unrelated work threads (Quick-mode bug fixes, fundamentals enrichment, sentiment web-search, entire screener) + ~119 uncommitted tests. Daily pipeline dead since 2026-05-04; `lens_outcomes` empty (0 rows vs 136 snapshots); CI only runs a Playwright scaffold against playwright.dev.
- **Collisions:** 3 ticker-universe concepts (`universe.csv`, `screen_universe.csv`, `Watchlist`) with two same-named `load_universe` functions and a `BRK-B`/`BRK.B` format mismatch; 4–6 independent yfinance access paths with different caches; 3 fundamentals models (`ValuationPanel`, `TickerMetrics`, `FundamentalsExtended`); indicator math triplicated (screener re-implements SMA/returns/RS instead of importing `analysis/indicators`); 3 candidate funnels (`analysis/scan`, `screener/pipeline`, `research/scan_rerank`); duplicated Wilson-CI + regime code; creator leaderboard is the UI front door while all recent work is quant; screener has no API route and no UI.
- **Dead weight:** empty `modeling/`/`reporting/`, dead `market/snapshots.py` + 0-row `market_snapshots` table, `tsr rank` stub, ~8 dead API endpoints/client methods, root-level Playwright scaffold + node_modules, agent scratch dirs.
- **Docs lie:** `architecture.md` describes the pre-pivot product; no ADR since 0008; the `/screen` skill points at a nonexistent `tsr research deep` command; `entry-exit-research-plan.md` declares position sizing out of scope (contradicts Portfolio Manager → ADR 0009 amends it).

## Plan sequence

| Plan | File | Produces | Status |
|---|---|---|---|
| 1 | `2026-08-04-plan1-stabilize-and-clean.md` | Clean tree: 4-thread work committed, junk deleted, dead code removed, real CI, ADR 0009 | **written** |
| 2 | `…-plan2-analysis-consolidation.md` (write after Plan 1) | One market-data client (bars/info/fundamentals/calendar, one cache + rate limiter); one universe module (fixes `BRK-B`/`BRK.B`, kills duplicate `load_universe`); screener imports `analysis/indicators`; screener API route + web page; ticker page + nav re-centered on quant with creator rail collapsible; dead endpoints/client methods removed; `tickers.py` router split quant vs creator | pending |
| 3 | `…-plan3-portfolio-manager.md` (write after Plan 2) | New tables `Position`, `PortfolioTrade` (side/qty/price/date/fees — named to avoid `backtest/metrics.Trade` collision), `CashFlow`; Alembic migration; `tsr pf` CLI namespace (log/edit/close/list, cost basis, realized+unrealized P&L); FastAPI `portfolio` router; web `/portfolio` page; bridge: positions feed scan priority + invalidation-level alerts | pending |
| 4 | `…-plan4-side-feature-and-docs.md` (write after Plan 3) | Creator pipeline packaged as `signals/` side feature behind a source-agnostic seam (`analysis/research.py` transcript/claims panels optional; walk-forward signal loader pluggable); `architecture.md` rewritten from scratch; CLAUDE.md/README refreshed; ops decision on reviving the daily cron | pending |

## Target module map (end state)

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

## Constraints carried forward (do not violate)

- Decision-support framing: rankings + evidence + confidence + N; the user pulls the trigger; no order execution, ever (ADR 0005/0008 survive the restructure).
- No composite scores; always N + Wilson CIs.
- UTC tz-aware datetimes; market math in `America/New_York`; no look-ahead.
- Evidence-quote requirement for extraction (ADR 0002); gold set stays the most important file.
- LLM plans pick from deterministic candidates (±15% clamp, 0.75×ATR guard).

## Open items flagged, not blocking

- Daily cron is dead (launchd job not installed). Reviving it — and adding `score-lens-outcomes` so `lens_outcomes` finally populates — lands in Plan 4 ops.
- 341 ingested docs still unextracted (spend approval) — side-feature backlog, unblocks the parked strategies.
- `screen_universe.csv` is a 132-name seed; `tsr screen-refresh-universe` has never been run.
