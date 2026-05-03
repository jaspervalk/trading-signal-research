# Architecture

This is the canonical design doc for `trading-signal-research`. It supersedes any inline notes elsewhere. Decision records that change architecture-relevant choices live in `docs/decisions/`.

## What this system is

A research pipeline that:

1. **Ingests** trader content from YouTube (Discord later).
2. **Extracts** structured trade calls — ticker, direction, entry/trigger, target, stop, timeframe — from messy text via a hybrid rules + LLM pipeline.
3. **Backtests** each call deterministically against market data, separating *activation* (did the trigger fire?) from *outcome* (what happened after entry?).
4. **Scores** each creator on a multi-metric scorecard with confidence intervals, not a single composite number.
5. **Ranks** new calls with a calibrated predictive model that combines creator history + price-action context + call features.
6. **Surfaces** results via a static report (Phase 1) and a live dashboard with Discord alerts (Phase 6).

This is not a trading bot. Nothing executes orders. The product is *evaluated information*.

## Locked decisions (V1)

- **Phase 1 source:** YouTube only (transcripts + descriptions). Discord deferred to Phase 7.
- **Phase 1 market data:** `yfinance` with a caching layer; daily + 1h bars where available.
- **Primary success metric:** **return at 5 trading days, conditional on activation, net of 10 bps round-trip cost.**
- **Secondary metrics:** hit rate (with Wilson 95% CI), MFE / MAE, hit-target-before-stop (when both defined), 1d/3d/21d returns, excess vs SPY.
- **Trigger semantics:** market / limit / trigger_above / trigger_below / unspecified. See ADR 0003.
- **Calendar:** XNYS via `pandas-market-calendars`. All datetimes stored UTC, market math done in `America/New_York`.
- **Storage:** SQLite for V1 (`data/tsr.sqlite`). Schema migrations via Alembic (added in Phase 1b).
- **Notebooks:** consumers only. They never write to the database.
- **CLI-first:** operator interface is `tsr <command>`; no HTTP API in V1.

## Layers

```
┌──────────────────────────────────────────────────────────────────┐
│  Sources (YouTube; Discord later)                                │
│   └─► SourceAdapter ── RawDocument / RawSegment                  │
└──────────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────────────────────────┐
│  Ingest (idempotent persist of Document + TranscriptSegment)     │
└──────────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────────────────────────┐
│  Normalize (clean text, recover ASR-mangled tickers)             │
└──────────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────────────────────────┐
│  Extract                                                         │
│   ├─ prefilter (rules)                                           │
│   ├─ llm_extractor (Claude tool-use, evidence_quote required)    │
│   ├─ validator (rules; reject hallucinated fields)               │
│   └─ confidence (final_confidence ∈ [0,1])                       │
│   → ExtractedCall                                                │
└──────────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────────────────────────┐
│  Market (yfinance + cache; calendar-aware)                       │
│   → MarketSnapshot, OutcomeWindow                                │
└──────────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────────────────────────┐
│  Backtest (activation + outcome; leakage-free)                   │
│   → BacktestResult                                               │
└──────────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────────────────────────┐
│  Scoring (creator scorecards w/ Wilson CIs; recency variants)    │
└──────────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────────────────────────┐
│  Modeling (logistic → XGBoost; calibrated; SHAP)                 │
└──────────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────────────────────────┐
│  Reporting + Dashboard ("mini-Bloomberg") + Discord alerts (out) │
└──────────────────────────────────────────────────────────────────┘
```

## Data model (current + planned)

Phase 1a (in code):
- `Creator` — source-agnostic identity.
- `SourceChannel` — `(source_type, external_id)` natural key. **The abstraction lives here.**
- `Document` — one piece of content (a video for YouTube).
- `TranscriptSegment` — timestamped chunk of `Document`.

Phase 2+ (planned, not yet in `models.py`):
- `ExtractedCall` — structured call w/ `evidence_quote`, `final_confidence`, status.
- `MarketSnapshot` — price/volume context at `posted_at` for a call's ticker.
- `OutcomeWindow` — per (call, horizon): activated, fill, return, MFE, MAE.
- `CreatorScorecard` — per (creator, window): N, hit_rate + CI, expectancy, excess-vs-SPY, etc.
- `ModelPrediction`, `Alert` — Phase 5+.

Each entity ships with the phase that uses it. Don't pre-add unused tables.

## Source abstraction (Discord-readiness)

The contract that makes Discord a one-adapter add later:

```python
class SourceAdapter(ABC):
    source_type: str

    def list_recent_documents(self, channel_external_id, *, since=None, limit=None) -> Iterable[str]: ...
    def fetch_document(self, external_id) -> RawDocument: ...
```

`RawDocument` and `RawSegment` are the only types downstream stages see. A `DiscordAdapter` will produce `RawDocument`s where each "video" is a message-thread or daily message-batch, with `RawSegment`s being individual messages. No downstream code changes.

See [decisions/0001-source-abstraction.md](decisions/0001-source-abstraction.md).

## Extraction strategy summary

Hybrid pipeline; LLM is one stage of four. The LLM is **never trusted alone** to decide a field — every non-null field must carry an `evidence_quote` that the validator checks against the source segment.

See [decisions/0002-extraction-hybrid.md](decisions/0002-extraction-hybrid.md).

## Backtest assumptions summary

Activation logic, fill assumptions, costs, calendar handling, and leakage controls are spelled out separately. **Read this before trusting any number out of the backtest.**

See [decisions/0003-backtest-assumptions.md](decisions/0003-backtest-assumptions.md).

## Dashboard ("mini Bloomberg terminal") — Phase 6

Built after the eval harness produces honest results. Stack proposal:

- **Frontend:** Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui.
- **Charts:** TradingView's open-source `lightweight-charts` for candles; `recharts` for everything else.
- **Backend:** FastAPI exposing read-only views over the same SQLAlchemy models. No HTTP API exists in V1; this is when it appears.
- **Real-time:** server-sent events for the alerts feed; polling for everything else. No full WebSocket layer in V1.

Tabs/panels (target):
- **Watchlist + chart panel** — tracked tickers, chart, intraday context.
- **Live alerts feed** — newly-ranked calls; click-through to the source video timestamp.
- **Creator leaderboard** — sortable by metric, with N + CI badges; greys-out low-N rows.
- **Per-creator drill-down** — scorecard, equity-curve-of-calls, recent calls table.
- **Backtest explorer** — filter calls by ticker / direction / horizon / regime.
- **Methodology panel** — links to ADRs and the case-study writeup. Front-and-centre.

Discord alerts go out via a webhook from the same alert-generation pipeline. The dashboard and Discord both subscribe to the same `Alert` rows; neither is the source of truth.

## Build order (canonical)

| Phase | Goal | Status |
|---|---|---|
| 0 | Skeleton, env, db, base entities, source adapter ABC + YouTube adapter | **in progress** |
| 1a | YouTube ingestion CLI persists Documents + Segments idempotently | next |
| 1b | Normalization + ticker recovery + Alembic migrations | |
| 2 | Hybrid call extractor + 200-segment gold set + extractor eval notebook | |
| 3 | Market data + backtest engine + leakage-controlled outcomes | |
| 4 | Creator scorecards + leaderboard | |
| 5 | First ranking model (logistic → XGBoost) + calibration + SHAP | |
| 6 | Dashboard (mini Bloomberg) + Discord alerts (out) | |
| 7 | Discord adapter (in) + multi-source normalization | |

The ranking model deliberately follows data accumulation. Don't train it in Phase 2.

## Principles

- **Honesty over polish.** Negative results are first-class outputs.
- **Leakage discipline.** Time-aware everything. No tuning on the test window.
- **Composite scores hide everything.** Show metrics with N and CIs.
- **Notebooks are reports, not pipelines.** All logic lives in `src/app/`.
- **Don't preemptively build.** Each entity / module ships with the phase that uses it.
