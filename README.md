# trading-signal-research

A **personal decision-support platform for ticker selection** that combines (1) YouTube transcript analysis as a first-class indicator class, (2) market data + classical technicals + fundamentals (yfinance), (3) walk-forward strategy backtests, and (4) explainable rankings + LLM-driven entry/exit research with linked evidence.

The unit of decision is **the ticker, not the creator**. The product is decision support — the user pulls the trigger; the system ranks, explains, and surfaces an implied action label ([ADR 0008](docs/decisions/0008-action-signal-labeling.md)). It is not a trading bot. Nothing executes orders.

## How it works

```
YouTube ingest ──► transcripts + descriptions ──► call + claim extractor (rules + LLM)
                                                          │
                                                          ▼
                              market data (yfinance) ──► per-call backtest + creator scorecards
                                                          │
                                                          ▼
                                       TickerSignal aggregates (ticker × window × type)
                                                          │
                              ┌───────────────────────────┴────────────────────────┐
                              ▼                                                    ▼
                  Strategy walk-forward backtest                    TickerResearchView (per ticker)
                  (mention_momentum, bullish_catalyst,              ├─ technicals + valuation
                  creator_consensus — ADR 0007)                     ├─ rule-based setup + rubric
                                                                    ├─ ActionSignal (ADR 0008)
                                                                    ├─ Quick research (1 LLM call)
                                                                    └─ Deep research (4 agents + judge)
```

Source-agnostic from day 1: a `SourceAdapter` interface (see [ADR 0001](docs/decisions/0001-source-abstraction.md)) is the only thing that knows about YouTube. Discord plugs in as another adapter without touching downstream stages.

Full design: [docs/architecture.md](docs/architecture.md). Decisions: [docs/decisions/](docs/decisions/). Recent session deltas: [docs/recent-changes-2026-05-08.md](docs/recent-changes-2026-05-08.md), [docs/recent-changes-2026-05-07.md](docs/recent-changes-2026-05-07.md).

## Status

| Phase | What | Status |
|---|---|---|
| 0–4 | Foundations: ingest + normalize + extract + per-call backtest + creator scorecards | done |
| C | Claims schema + extractor v2 (`Claim` ORM, LLM tool-use extension) | done — ADR 0006 |
| D | TickerSignal materialization (4 types × 3 windows) | done — ADR 0006 |
| E | Ticker-first dashboard (ResearchView, scan, watchlist board) | done |
| F | Strategy + walk-forward harness (3 baselines + portfolio metrics + calibration) | done — ADR 0007 |
| H | Implied action labels (BUY/HOLD/SELL-style derived view) | done — [ADR 0008](docs/decisions/0008-action-signal-labeling.md) |
| I | Entry/Exit research — Quick mode (single Haiku call, ~$0.01) | done — [plan](docs/entry-exit-research-plan.md) |
| J | Entry/Exit research — Deep mode (4 parallel analysts + Sonnet judge, ~$0.10) | done |
| B | 341-doc extraction backfill | partial — awaits spend approval |
| K | SSE streaming for Deep mode (per-agent progress) | proposed |
| G | ML ranker | gated on F + ≥6 months claim data |
| 7 | Discord adapter | future |

## Setup

Python 3.11+. I use `uv`, but `pip` works too.

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env
```

Fill in `ANTHROPIC_API_KEY` (for the LLM extractor) and `YOUTUBE_API_KEY` (for the YouTube Data API path) in `.env`.

If you want the Whisper fallback for transcripts (recommended — see below):

```bash
uv pip install -e ".[whisper]"
brew install ffmpeg          # macOS; on linux: apt install ffmpeg
```

## CLI

Everything operator-facing runs through one `tsr` command:

```bash
# Pipeline
tsr initdb               # create the SQLite schema (dev only — prod uses Alembic)
tsr ingest               # pull videos + transcripts for all active creators
tsr extract              # hybrid call+claim extractor over new docs
tsr backtest             # compute per-call outcomes (ADR 0003)
tsr score                # recompute creator scorecards
tsr aggregate-signals    # materialize TickerSignal rows (ADR 0006)

# Research view (per-ticker)
tsr research AAPL                              # full TickerResearchView
tsr scan --watchlist                           # ranked status board for pinned tickers
tsr scan --tickers AAPL,NVDA,TSLA              # ad-hoc N-ticker scan

# Strategy walk-forward (ADR 0007)
tsr backtest-strategy mention_momentum --start 2025-09-01 --end 2025-12-01

# Portfolio (manual trade ledger; quote prices ~15 minutes delayed)
tsr pf add NVDA --side buy --qty 10 --price 145.20 --date 2026-07-15
tsr pf list                  # positions with live P&L
tsr pf trades                # raw ledger
tsr pf rm 3                  # delete a mistaken entry
```

The dashboard (Next.js, see [apps/web/](apps/web/)) is a separate process from `tsr`; it talks to the FastAPI in [apps/api/](apps/api/) which exposes `/tickers/{t}/research`, `/research/scan`, `/research/quick/{t}` (single LLM call), and `/research/deep/{t}` (4-agent multi-lens debate).

## Tracked creators

14 active YouTube channels right now. The list is curated for *falsifiable swing-trade calls* — channels that regularly issue ticker + direction + ideally a price level — rather than popularity. Mix of small (~20k subs) and mid (~500k+) channels.

The current set: Adam Mancini, The Trade Risk, TraderLion, Alphatrends, Bulls on Wall Street, Qullamaggie, Oliver Velez Trading, Stock Market Mentor, Stockbee, Richard Moglen, ShadowTrader, Leavitt Brothers, IBD Videos, T3 Live.

Full config with notes per creator: [configs/creators.yaml](configs/creators.yaml).

## Daily cron

The pipeline is designed to run unattended. There's a wrapper script and a launchd job that runs `ingest → extract → backtest → score` once a day:

```bash
# install the launchd job (macOS)
cp scripts/com.jaspervalk.tsr.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.jaspervalk.tsr.daily.plist

# trigger immediately to verify
launchctl start com.jaspervalk.tsr.daily

# tail the log
tail -f data/logs/cron.log
```

Default schedule is 06:30 local time — three hours before the US market opens, which gives Whisper enough headroom to finish transcribing overnight uploads. `INGEST_LIMIT` and `EXTRACT_LIMIT` in `scripts/daily_run.sh` keep daily runs bounded; pass `--catch-up` for a wider one-off backfill.

On Linux, the same script works with cron:

```cron
30 6 * * * /path/to/trading-signal-research/scripts/daily_run.sh
```

## Ingestion in detail

Two layers, each with a fallback:

**Metadata + listing** (titles, descriptions, posted_at, channel video lists):

1. **YouTube Data API v3** when `YOUTUBE_API_KEY` is set — reliable upload dates, real `since`-date filtering, ~20 quota units per full run.
2. **yt-dlp** scraping as fallback — works without credentials, but upload dates are less reliable.

**Transcripts**:

1. A small per-fetch delay (`transcript.fetch_delay_seconds` in settings, default 2.5s + jitter) to slow down the request rate.
2. `youtube-transcript-api` first — fast and free.
3. **Whisper** kicks in when the native API gets IP-blocked or returns no captions.

Whisper backends (set in `configs/settings.yaml`):

- `faster_whisper` (default): local, free, ~30–90s per 10-min video on CPU using the `base.en` model.
- `openai_api`: cloud, fast, ~$0.006/min, requires `OPENAI_API_KEY`.
- `none`: disable fallback entirely; ingest aborts on the first IP-block.

The full reasoning behind this design is in [docs/decisions/0004-transcript-ingestion.md](docs/decisions/0004-transcript-ingestion.md).

## Repo layout

```
src/app/
  sources/              YouTube adapter + base interface (Discord later)
  ingest/               orchestration + creators.yaml loader
  normalize/            transcript cleaning + ticker recovery
  extract/              prefilter, LLM extractor, validator, gold-set eval
  market/               yfinance client + cache, calendar, snapshots
  backtest/             activation + outcomes + walk-forward harness + metrics + calibration
  scoring/              Wilson CIs, expectancy, creator scorecards
  aggregation/          TickerSignal aggregator (ADR 0006)
  analysis/             random-ticker research view: indicators, levels, setup, status,
                        action labels (ADR 0008), entry zones, valuation, scan
  strategies/           ADR 0007 — Strategy ABC + 3 baselines
  research/             entry/exit research feature: schema, exits, context, quick (1
                        LLM call), deep (4 agents + judge), agents/ subpackage
  portfolio/            manual trade ledger: schema, pure ledger fold, pricing, service
  screener/             broad-universe filter funnel (value/growth/quality/technical)
configs/                creators.yaml, universe.csv, settings.yaml
data/                   ingested data + caches (gitignored except gold/)
notebooks/              eval reports — read-only consumers of src/app
tests/                  unit + integration (352 passing as of 2026-05-08)
docs/                   architecture overview + ADRs (0001–0008) + design memos + disclaimer
apps/api/               FastAPI read-only views + research endpoints (separate process)
apps/web/               Next.js 14 dashboard (App Router, JetBrains Mono terminal aesthetic)
```

## Conventions worth knowing

- All numbers in notebooks come from `src/app` — notebooks never write to the database.
- Every datetime stored is timezone-aware UTC. Market math runs in `America/New_York`.
- Backtests are time-aware: only data with `timestamp ≤ posted_at` can drive any decision about a call. No look-ahead, ever.
- Negative results are reported the same way as positive ones — the case study writes up what the data actually showed.

## Disclaimer

Research and educational software. Not investment advice. See [docs/disclaimer.md](docs/disclaimer.md).
