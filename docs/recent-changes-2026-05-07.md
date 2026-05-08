# Recent changes — 2026-05-07 session

A reference of what shipped across this session. Organized by theme, with
file paths, how to use, what's tested, and what's deferred.

**Headline numbers:** ~4,500 LoC across 25 new source files + 8 new test files.
**Test suite:** 299 / 299 passing (started at 178 prior to this work — 121 new
tests added). No regressions.

## At a glance — new commands

```bash
# Single-ticker research view (works for any yfinance-resolvable symbol)
tsr research AAPL                    # pretty-printed terminal view
tsr research ETSY --no-metadata      # faster (skips yfinance .info)
tsr research NVDA --json | jq        # structured JSON

# Multi-ticker scan (the daily-research killer feature)
tsr scan --watchlist                 # status board for all pinned tickers
tsr scan --tickers AAPL,NVDA,TSLA    # ad-hoc N-ticker scan
tsr scan --universe                  # full configs/universe.csv sweep (slow)

# Walk-forward backtests (ADR 0007 infrastructure)
tsr backtest-strategy mention_momentum --start 2025-09-01 --end 2025-12-01
tsr backtest-strategy bullish_catalyst_aggregator --start ... --end ... --persist
tsr backtest-strategy creator_consensus --start ... --end ... --json
```

## At a glance — new HTTP endpoints

```
GET  /tickers/{ticker}/research               # full TickerResearchView
GET  /research/scan?source=watchlist          # ranked multi-ticker scan
GET  /research/scan?tickers=AAPL,NVDA,TSLA
GET  /research/snapshots/{ticker}?days=30     # setup-evolution time series
```

---

## 1. Random-ticker research backend

**What:** Analysis layer that turns any valid ticker (in or out of universe)
into a structured `TickerResearchView` — identity, market snapshot, indicators,
swing-based levels, rule-based setup classification, decision-support status,
style fit, and entry-zone candidate. Transparent rubric, no composite scores.

**New module:** `src/app/analysis/`

| File | Role |
|---|---|
| [schema.py](../src/app/analysis/schema.py) | Pydantic `TickerResearchView` + 9 sub-panels, JSON-stable string enums |
| [indicators.py](../src/app/analysis/indicators.py) | SMA / EMA / RSI / ATR / realized vol / volume ratio / RS-vs-SPY / returns / 52w hi-lo / MA slopes / `ma_alignment`, leakage-controlled via `slice_at_or_before` |
| [levels.py](../src/app/analysis/levels.py) | Swing-pivot detection, nearest S/R, pullback depth, consolidation/tightness, signed breakout distance, base low/high |
| [setup.py](../src/app/analysis/setup.py) | Rule-based classifier with `SetupThresholds` dataclass; covers strong_uptrend / uptrend_pullback / breakout_candidate / extended_momentum / range_bound / downtrend / high_volatility_unstable / low_liquidity / insufficient_data / unclear. Includes a "broken trend with counter-trend bounce" branch added after MSFT exposed the gap |
| [style.py](../src/app/analysis/style.py) | Fit scorer for momentum_breakout / trend_pullback / mean_reversion / base_breakout / relative_strength_leader / not_suitable_now |
| [status.py](../src/app/analysis/status.py) | Decision-support status with full `DecisionRubricEntry` rubric (name/value/threshold/passed/weight) — show-your-work surface |
| [entry.py](../src/app/analysis/entry.py) | Entry-zone candidate logic with ATR-anchored invalidation + R/R, distinct method per setup type. Decision-support language only ("research zone" / "invalidation reference" / "risk reference"); never "buy" / "stop loss" / "target" |
| [transcript.py](../src/app/analysis/transcript.py) | Joins `TickerSignal` / `Claim` / `Coverage` and produces a confirms/contradicts read against the technical setup |
| [research.py](../src/app/analysis/research.py) | Orchestrator: `build_ticker_research_view(ticker, session, as_of, ...)` |

**Wired:**
- `GET /tickers/{ticker}/research` in [apps/api/app/routes/tickers.py](../apps/api/app/routes/tickers.py)
- `tsr research <TICKER>` CLI in [src/app/cli.py](../src/app/cli.py)

**Tests:** 56 unit tests across `test_analysis_indicators.py`,
`test_analysis_levels.py`, `test_analysis_setup_status.py`. All passing.

**Locked constraints honored** (per ADR 0005):
- No buy/sell language
- No composite scores — every metric carries N + Wilson 95% CI as a peer
- Evidence quotes are first-class
- Sample-size warnings are visible
- "Historical only" badge slot for ticker-mention freshness
- No predicted return / probability of profit

**One real bug surfaced + fixed during smoke runs:**
- MA alignment was over-strict (5-way descending order). AAPL had `sma_50 < sma_150`
  after a recent dip even though the long trend was up. Relaxed to the
  standard institutional filter: `price > sma_50 > sma_200`.
- Breakout candidate range expanded from `[-3%, 0)` to `[-3%, +3%]` so fresh
  breakouts (price just above the recent high) get caught.

---

## 2. Frontend dashboard — composite design ported

**What:** The locked composite design (Terminal chassis + Brief drawer + Tape
metadata grammar) ported to `apps/web` + research-view panels added.

**Design language locked:**
- Dark Terminal chassis with JetBrains Mono throughout
- Brief's serif-italic pull quotes only inside expanded claim drawers
- Tape's bracketed metadata grammar + LOW-N warning banner system
- Phosphor green / red / amber / cyan accents

**New components** (`apps/web/src/components/`):

| File | Role |
|---|---|
| [ResearchStatusStrip.tsx](../apps/web/src/components/ResearchStatusStrip.tsx) | Headline status strip below the ticker header, color-coded left border |
| [TechnicalsCard.tsx](../apps/web/src/components/TechnicalsCard.tsx) | Trend / momentum / RS / levels in compact mono rows |
| [SetupCard.tsx](../apps/web/src/components/SetupCard.tsx) | Setup type + reasons + counterarguments + decision rubric (✓/✗ rows) |
| [EntryZoneCard.tsx](../apps/web/src/components/EntryZoneCard.tsx) | Trigger / research zone / invalidation / R/R, or "not available" with reason |
| [StyleFitCard.tsx](../apps/web/src/components/StyleFitCard.tsx) | All 6 styles with fit level + reasons |
| [ScanStatusBoard.tsx](../apps/web/src/components/ScanStatusBoard.tsx) | Compact ranked status board, used by /watchlist (and reusable) |

**Updated pages + plumbing:**

| File | Change |
|---|---|
| [apps/web/src/lib/api.ts](../apps/web/src/lib/api.ts) | Full `TickerResearchView` types + `api.tickers.research()` + `api.research.scan()` + `api.research.snapshots()` |
| [apps/web/src/app/tickers/[ticker]/page.tsx](../apps/web/src/app/tickers/[ticker]/page.tsx) | Adds `useQuery` for `/research`, slots 5 new panels into the existing 3-column grid |
| [apps/web/src/app/watchlist/page.tsx](../apps/web/src/app/watchlist/page.tsx) | Rewritten — pinned tickers as a sortable status board with re-scan button + summary counts |
| [apps/web/src/components/Nav.tsx](../apps/web/src/components/Nav.tsx) | Top-right "look up | TICKER" search box; sanitizes input and routes to `/tickers/{TICKER}` |
| [apps/web/src/app/layout.tsx](../apps/web/src/app/layout.tsx) | Newsreader + JetBrains Mono via `next/font/google` |
| [apps/web/src/app/globals.css](../apps/web/src/app/globals.css) | Token system: `--info`, `--panel`, `--panel-2`, `--hairline-2`, `--muted-2`, demo-hatch utility |

**Bugs fixed:**
- `apps/web/src/app/tickers/[ticker]/page.tsx` — switched from
  `use(params)` to sync `params.ticker` (Next.js 14.2.x has sync params; the
  `Promise<{}>` type was forward-compat for 15.x and was breaking the page
  with `An unsupported type was passed to use()`)
- [apps/api/app/main.py](../apps/api/app/main.py) — added :3001 to CORS
  allow-list so apps/web can run alongside the parallel Healthcare-Policy-Copilot
  project on :3000
- [src/app/analysis/transcript.py](../src/app/analysis/transcript.py) — added
  `_ensure_utc()` helper to normalize mixed naive/aware `Document.posted_at`
  rows. Older Qullamaggie ingests stored naive timestamps; this surfaced as
  `can't compare offset-naive and offset-aware datetimes` on NFLX

**Latent bug flagged but not fixed (out of scope):**
- `apps/web/src/app/calls/[id]/page.tsx` and `apps/web/src/app/creators/[id]/page.tsx`
  use the same broken `use(params)` pattern. Same one-line fix; haven't
  touched them since they weren't in scope of any task.

---

## 3. Walk-forward backtesting infrastructure (ADR 0007)

**What:** Strategy ABC + 3 baseline strategies + walk-forward harness +
portfolio metrics + calibration + ORM persistence + CLI command. The full
ADR 0007 §1-6 except the HTML report.

**New strategy module:** `src/app/strategies/`

| File | Role |
|---|---|
| [base.py](../src/app/strategies/base.py) | `Strategy` ABC + `StrategyContext` + `StrategyDecision` + `EvidenceRef`. Stateless, time-aware, never-touches-DB-directly contract |
| [mention_momentum.py](../src/app/strategies/mention_momentum.py) | Rank tickers by `net_polarity × n_distinct_creators` over 7d, top decile long, hold 5d |
| [bullish_catalyst.py](../src/app/strategies/bullish_catalyst.py) | Long when ≥2 distinct creators raise factual/opinion bullish catalyst claims in last 14d |
| [creator_consensus.py](../src/app/strategies/creator_consensus.py) | Long/short when ≥2 calibrated creators (`hit_rate_lower_ci > 0.55`) agree on direction in last 7d. Drops contradicted consensus (both sides crowded) |

**New backtest infrastructure:** `src/app/backtest/`

| File | Role |
|---|---|
| [market_reader.py](../src/app/backtest/market_reader.py) | Leakage-controlled bar reader. `daily_bars()` for strategies (sliced ≤ as_of); `forward_bars()` is harness-internal for the simulator only |
| [metrics.py](../src/app/backtest/metrics.py) | CAGR / Sharpe / max DD / hit rate w/ Wilson 95% CI / turnover / vol / regime breakdown. `WalkForwardMetrics` dataclass |
| [walkforward.py](../src/app/backtest/walkforward.py) | The harness — `WalkForwardConfig`, `run_walkforward()`, V1 simulator (open-next-session → close-after-N-days, net of bps), sanity checks, T2 cutoff enforcement, `persist_result()` |
| [calibration.py](../src/app/backtest/calibration.py) | Decile bucketing + Brier score + reliability slope/intercept + miscalibration flag |

**ORM:** New `WalkForwardResultRow` table in [models.py](../src/app/models.py).
Idempotent on `(strategy_name, strategy_version, config_hash)`. Headline metrics
as columns + JSON blobs for replay (trades / equity_curve / sanity_checks).

**Settings:** New `walkforward:` block in [configs/settings.yaml](../configs/settings.yaml)
+ matching `WalkForwardSettings` in [config.py](../src/app/config.py):
```yaml
walkforward:
  t2_cutoff: "2026-01-01T00:00:00+00:00"
  min_n_trades: 30
  default_rebalance: weekly
  cost_bps_grid: [5, 10, 25, 50]
  benchmark_ticker: SPY
```

**CLI:** `tsr backtest-strategy <name>` with `--start --end --rebalance --cost-bps --min-n-trades --persist --json`.

**Tests:** 57 across `test_backtest_metrics.py`, `test_strategies_mention_momentum.py`,
`test_strategies_bullish_catalyst.py`, `test_strategies_creator_consensus.py`,
`test_backtest_walkforward.py`, `test_backtest_calibration.py`,
`test_backtest_walkforward_persist.py`. All passing.

**End-to-end verified:**
```
tsr backtest-strategy bullish_catalyst_aggregator --start 2025-09-01 --end 2025-12-01 --persist
→ persisted as walkforward_results.id=1
→ n_rebalances=13  n_trades=0  underpowered=True  benchmark_cagr=+29.33%

# Re-run with same config: upserts in place (id=2 reused, total rows still 2)
```

**Real bug surfaced + fixed during integration tests:** the
leakage-bound `MarketDataReader` couldn't produce *forward* bars for the
simulator. Added `forward_bars()` (harness-internal) — strategies use
`daily_bars()` (leakage-bound), the simulator uses `forward_bars()` (outcome
data, by definition post-as_of). The leakage rule still holds because the
strategy never sees `forward_bars()`.

---

## 4. Research instrument — accumulating state across sessions

**What:** Three additions that turn the project from a stateless lookup tool
into a research instrument that compounds value across daily sessions.

### 4a. `ResearchSnapshot` ORM
[src/app/models.py](../src/app/models.py) — new table, ~30 columns, no JSON
blobs. ~1KB per row. Indexed on `(ticker, as_of)` and `status`. Append-only.
Captures the headline of every `TickerResearchView` computation: status,
setup, key levels, transcript signal, key indicators.

[src/app/analysis/snapshot.py](../src/app/analysis/snapshot.py) —
`persist_snapshot(view, session)` writer. Best-effort: fails open so research
view returns even if persist fails.

**Wired** so every `tsr research`, every `/tickers/{t}/research` call, and every
`tsr scan` writes one row per ticker. After ~30 days of daily use you'll have
a meaningful time-series of how each ticker's setup evolved.

### 4b. Multi-ticker scan
[src/app/analysis/scan.py](../src/app/analysis/scan.py) — `scan_tickers([...])`
runs the research orchestrator over a list of tickers and returns a ranked
`ScanResult`. Transparent ranking key (no composite score):
- `research_candidate (high)` → rank 0
- `watch (high/medium)` → rank 1-2
- `extended_risk` → rank 2
- `wait_for_setup` → rank 3
- `skip_for_now` → rank 4
- `insufficient_data` → rank 5
- errors → rank 99

Within a bucket, ties break by setup_confidence then by
`primary_style != not_suitable_now`. Sequential to keep yfinance happy
(no pmap).

[apps/api/app/routes/research.py](../apps/api/app/routes/research.py):
- `GET /research/scan?source=watchlist` — uses pinned watchlist
- `GET /research/scan?tickers=AAPL,NVDA,TSLA` — ad-hoc list
- `GET /research/snapshots/{ticker}?days=30` — setup-evolution timeline

[src/app/cli.py](../src/app/cli.py):
- `tsr scan --watchlist`
- `tsr scan --tickers AAPL,NVDA,TSLA`
- `tsr scan --universe` (slow on cold cache)

### 4c. Watchlist status board
[apps/web/src/app/watchlist/page.tsx](../apps/web/src/app/watchlist/page.tsx)
rewritten — pinned tickers shown as the locked-design status board with
summary counts and a re-scan button. Clicking through goes to the per-ticker
research view. Unpin chips below the table.

**Tests:** 8 in `test_analysis_scan.py` — sort key, dedup, persist on/off,
error rows, summary counts.

---

## 5. Bug fixes / diagnoses

### Stockbee ingest (the explicit path-1 fix)
**Root cause:** config bug, not ingest bug. The channel ID
`UCokSy9UkcPDCYyrU5_6835w` resolves to a Vietnamese stock-market livestream
channel, not Pradeep Bonde's Stockbee. Three Vietnamese-language docs
(`"Livestream 30/6/25: Thúc tiến độ để sớm nâng hạng TTCK Việt Nam"`) had
been ingested; extraction silently produced zero calls because the language
isn't English, so they're inert noise in the corpus.

**Fix:** [configs/creators.yaml](../configs/creators.yaml) — Stockbee config
marked `active: false` with a notes trail. **Action item:** find Pradeep
Bonde's actual channel ID, update `external_id`, set `active: true`.

### Other bugs fixed mid-session
| Bug | Where | Fix |
|---|---|---|
| `An unsupported type was passed to use()` on dynamic ticker pages | apps/web ticker page | Switched from `use(params)` (Next 15 forward-compat) to sync `params.ticker` (Next 14.2 reality) |
| CORS preflight 400 for apps/web running on :3001 | apps/api/app/main.py | Added :3001 to `allow_origins` |
| `can't compare offset-naive and offset-aware datetimes` on NFLX `/research` | analysis/transcript.py | `_ensure_utc()` normalizer for mixed naive/aware `Document.posted_at` rows |
| RSI returned None for monotonic uptrend (divide-by-zero) | analysis/indicators.py | Standard convention: 100 in all-up case, 0 in all-down case, 50 if both flat |
| MA alignment too strict (5-way descending) | analysis/indicators.py | Relaxed to `price > sma_50 > sma_200` (3-way trend filter) |
| Breakout candidate excluded fresh breakouts | analysis/setup.py | Range widened to `[-3%, +3%]` |

---

## What's still on hold (and why)

These are deliberately deferred — call them out in conversation when ready:

| Item | Why deferred | What unblocks it |
|---|---|---|
| **TradingAgents integration** | Calibration debt + cost ($0.30-2/run) + ADR 0005 framing friction. Existing rule-based research view already works. | Run baselines on real data; if they don't beat SPY, multi-agent is a more expensive way to be wrong |
| **341-doc extraction backfill** | Needs your spend approval | Approve ~$1-3 in Anthropic spend; one `tsr extract` run |
| **Stockbee channel correct ID** | I don't know Pradeep Bonde's actual YouTube channel ID | Find it manually, update creators.yaml |
| **ADR 0007 polish** (cost-bps grid, HTML report, calibration wiring into persisted results, `is_tradeable` perf cache) | Value scales with N_trades; currently 0 | Data backlog clears → strategies start firing trades |
| **Setup-evolution chart in dashboard** | Need ~30 days of `ResearchSnapshot` data first | Daily use of `tsr scan --watchlist` and the dashboard `/watchlist` page |
| **Multi-timeframe alignment / earnings calendar / options vol** | New data vendors needed | Vendor selection + integration |
| **ML setup classifier** | Locked rule-based first per ADR 0005 | Strategies + ≥6 months claim data per ADR 0007 §G |
| **Latent `use(params)` bug on calls/[id] + creators/[id] pages** | Out of scope of any task this session | Trivial one-line fix when next touching those pages |

---

## Architectural shape now

```
trading-signal-research/
├── src/app/
│   ├── analysis/                  # ← NEW — random-ticker research backend
│   │   ├── schema.py              # Pydantic TickerResearchView
│   │   ├── indicators.py
│   │   ├── levels.py
│   │   ├── setup.py
│   │   ├── style.py
│   │   ├── status.py
│   │   ├── entry.py
│   │   ├── transcript.py
│   │   ├── research.py            # orchestrator
│   │   ├── snapshot.py            # ← NEW — persistence helper
│   │   └── scan.py                # ← NEW — multi-ticker scan
│   ├── strategies/                # ← NEW — ADR 0007 strategy ABC + baselines
│   │   ├── base.py
│   │   ├── mention_momentum.py
│   │   ├── bullish_catalyst.py
│   │   └── creator_consensus.py
│   ├── backtest/
│   │   ├── activation.py          (existing)
│   │   ├── outcomes.py            (existing)
│   │   ├── run.py                 (existing — per-call backtest)
│   │   ├── market_reader.py       # ← NEW
│   │   ├── metrics.py             # ← NEW
│   │   ├── walkforward.py         # ← NEW — the harness
│   │   └── calibration.py         # ← NEW
│   ├── models.py                  # +WalkForwardResultRow, +ResearchSnapshot
│   ├── cli.py                     # +tsr research, +tsr scan, +tsr backtest-strategy
│   └── config.py                  # +WalkForwardSettings
├── apps/api/app/routes/
│   ├── tickers.py                 # +/tickers/{t}/research
│   └── research.py                # ← NEW — /research/scan + /research/snapshots
├── apps/web/src/
│   ├── components/                # 6 new research-panel components
│   │   ├── ResearchStatusStrip.tsx
│   │   ├── TechnicalsCard.tsx
│   │   ├── SetupCard.tsx
│   │   ├── EntryZoneCard.tsx
│   │   ├── StyleFitCard.tsx
│   │   └── ScanStatusBoard.tsx
│   ├── app/tickers/[ticker]/page.tsx   # research panels wired
│   ├── app/watchlist/page.tsx          # rewritten as status board
│   └── components/Nav.tsx              # +ticker search box
├── configs/
│   ├── settings.yaml              # +walkforward section
│   └── creators.yaml              # Stockbee deactivated
├── docs/
│   └── recent-changes-2026-05-07.md   # this file
└── tests/                         # 121 new tests, 299 total passing
```

## Pointers for next time

If picking this up cold:
1. Open `tsr scan --watchlist` to see the daily research workflow in action
2. `cat docs/recent-changes-2026-05-07.md` (this file)
3. CLAUDE.md is the long-term context primer (gitignored)
4. Walk-forward strategies are ready but signal-starved — fixing the data
   backlog (Stockbee channel ID + 341-doc extraction backfill) is the
   highest-leverage next move
