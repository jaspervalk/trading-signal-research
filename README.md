# trading-signal-research

A pipeline that takes trader content from YouTube (Discord later), pulls structured trade calls out of the messy text, backtests them against market data, and scores creators on how reliable their calls actually are.

The point is to evaluate stock-trading content creators with real data — not to run a trading bot.

## How it works

```
YouTube ingest ──► transcripts + descriptions ──► call extractor (rules + LLM)
                                                          │
                                                          ▼
                              market data (yfinance) ──► backtest engine
                                                          │
                                                          ▼
                                            creator scorecards
                                                          │
                                                          ▼
                                          ranking model ──► alerts + dashboard
```

The whole thing is source-agnostic — there's a `SourceAdapter` interface so Discord (and anything else later) can plug in without touching the downstream stages.

Full design: [docs/architecture.md](docs/architecture.md). Decisions: [docs/decisions/](docs/decisions/).

## Status

| Phase | What | Status |
|---|---|---|
| 0 | Repo + foundations | done |
| 1a | YouTube ingestion + data model | done |
| 1b | Normalization, ticker recovery, Whisper fallback, Data API | done |
| 2 | Hybrid call extraction (rules + LLM) | done |
| 3 | Market data + backtest engine | done |
| 4 | Creator scorecards | done |
| 5 | Ranking model | next |
| 6 | Alerts + dashboard ("mini Bloomberg terminal") | |
| 7 | Discord source | |

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

Everything runs through one `tsr` command:

```bash
tsr initdb               # create the SQLite schema
tsr ingest               # pull videos + transcripts for all active creators
tsr extract              # run the call extractor over new documents
tsr backtest             # compute outcomes for accepted calls
tsr score                # recompute creator scorecards
tsr rank                 # rank new calls (Phase 5, not yet implemented)
```

A typical end-to-end run is `ingest → extract → backtest → score`.

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
  backtest/             activation rules + outcome computation
  scoring/              Wilson CIs, expectancy, creator scorecards
  modeling/             ranker (Phase 5 — empty for now)
  reporting/            leaderboard rendering
configs/                creators.yaml, universe.csv, settings.yaml
data/                   ingested data + caches (gitignored except gold/)
notebooks/              eval reports — read-only consumers of src/app
tests/                  unit + integration tests (113 currently)
docs/                   architecture overview + ADRs + disclaimer
apps/web/               dashboard (Phase 6, placeholder for now)
```

## Conventions worth knowing

- All numbers in notebooks come from `src/app` — notebooks never write to the database.
- Every datetime stored is timezone-aware UTC. Market math runs in `America/New_York`.
- Backtests are time-aware: only data with `timestamp ≤ posted_at` can drive any decision about a call. No look-ahead, ever.
- Negative results are reported the same way as positive ones — the case study writes up what the data actually showed.

## Disclaimer

Research and educational software. Not investment advice. See [docs/disclaimer.md](docs/disclaimer.md).
