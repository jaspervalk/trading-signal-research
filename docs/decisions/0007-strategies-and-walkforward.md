# ADR 0007 — Strategies and walk-forward backtesting

**Status:** accepted
**Date:** 2026-05-05
**Depends on:** [ADR 0005](0005-product-pivot-decision-support.md), [ADR 0006](0006-claims-and-ticker-signals.md)
**Extends:** [ADR 0003](0003-backtest-assumptions.md)

## Context

ADR 0003 defined the per-call backtest: given an `ExtractedCall`, did the trigger fire, what was the return at horizons {1d, 3d, 5d, 21d}. That's the right primitive but it doesn't compose into a portfolio-level result, which is what "decision-support" needs to defend.

The pivot in ADR 0005 needs to answer: "If I had used this *strategy* over the last N years, what would the portfolio metrics look like?" — with proper walk-forward, calibration, and benchmark comparison. The current code computes none of these.

## Decision

Introduce a `Strategy` abstraction, a walk-forward harness, and portfolio-level metrics. All built on top of the existing `OutcomeWindow` data; **no leakage controls relax.**

### 1. `Strategy` ABC

```python
# src/app/strategies/base.py

class StrategyContext(NamedTuple):
    as_of: datetime               # decision time, UTC
    universe: list[str]           # tickers eligible at as_of
    ticker_signals: dict[str, list[TickerSignal]]   # all signals with window_end <= as_of
    market: MarketDataReader      # bars/snapshots, only data with bar_time <= as_of
    creator_scorecards: dict[int, CreatorScorecard]   # most recent <= as_of

class StrategyDecision(NamedTuple):
    ticker: str
    score: float                  # arbitrary scale; calibrated separately
    direction: str                # 'long' | 'short' | 'flat'
    weight: float                 # portfolio fraction in [0, 1]
    evidence: list[EvidenceRef]   # links back to claims/calls/signals
    reasoning: str                # one-line human-readable explanation

class Strategy(ABC):
    name: str
    version: str

    @abstractmethod
    def evaluate(self, ctx: StrategyContext) -> list[StrategyDecision]: ...
```

A `Strategy` is **stateless and time-aware**: it sees only data with `t <= ctx.as_of`. The harness enforces this by building `MarketDataReader` and `ticker_signals` from filtered queries.

### 2. Initial strategy implementations

Three baselines, each kept simple enough that a reviewer can verify the logic:

- **`MentionMomentum`** — rank tickers by `TickerSignal(claims_all, 7d).net_polarity × n_distinct_creators`, long top decile, equal weights.
- **`BullishCatalystAggregator`** — long tickers with ≥2 distinct-creator `catalyst` claims (claim_class ∈ {factual, opinion}) in the last 14 days, hold 5d.
- **`CreatorConsensus`** — long tickers where ≥2 creators with `hit_rate_lower_ci > 0.55` issued same-direction calls in the last 7 days.

Each ships with a unit test against synthetic data and a backtest run against real data.

### 3. Walk-forward harness

```python
# src/app/backtest/walkforward.py

@dataclass
class WalkForwardConfig:
    start: datetime
    end: datetime
    train_window: timedelta       # for refittable strategies; 0 for static
    test_window: timedelta        # length of each forward step
    step: timedelta               # how far to advance per iteration
    rebalance: str                # 'daily' | 'weekly' | 'on_signal'
    refit: bool                   # whether the strategy refits each step

def run_walkforward(strategy: Strategy, cfg: WalkForwardConfig) -> WalkForwardResult: ...
```

Mechanics:
1. Slice `[cfg.start, cfg.end]` into rolling `(train_window, test_window)` pairs that step by `cfg.step`.
2. For each test slice, run the strategy at each rebalance datetime, get `StrategyDecision`s, simulate execution against the existing `OutcomeWindow` data for activated calls and against bars for non-call decisions.
3. Concatenate test slices into one continuous out-of-sample equity curve.
4. **Hard rules:** the strategy never sees data with `t > test_slice.start`; the harness never tunes hyperparameters on the test concatenation; T2 (final test cutoff) is set in `configs/settings.yaml` and never moved.

### 4. Portfolio-level metrics

Every walk-forward result reports:
- `cagr` — compound annual growth rate of the equity curve.
- `sharpe` — annualized, daily-bar based, rf=0 (V1 simplification).
- `max_drawdown` — peak-to-trough on the equity curve.
- `hit_rate` — fraction of closed positions with positive return, with Wilson 95% CI.
- `turnover` — annualized fraction of portfolio replaced.
- `vol` — annualized stdev of daily returns.
- `benchmark_return` — SPY total return over the same wall-clock window.
- `excess_cagr` — `cagr - benchmark_cagr`.
- `n_trades`, `avg_holding_days`, `win/loss ratio`.
- **Regime breakdown:** all of the above, split by SPY 21d trend at trade-open (up/flat/down per ADR 0003).

### 5. Calibration

For any strategy emitting a `score` interpretable as a probability (or any score with an intended monotonic relationship to outcome):
- Bucket scores into deciles.
- Plot mean predicted score vs realized hit rate per decile.
- Report Brier score and reliability-diagram slope/intercept.
- A strategy whose calibration is off by >2 deciles in any bucket is flagged in the report and surfaced in the dashboard's "Methodology" panel.

### 6. Reporting

A walk-forward run produces:
- A `WalkForwardResult` row in the database (one per (strategy, version, config_hash)).
- A static HTML/notebook report with equity curve, metrics table, regime breakdown, calibration plot.
- A `tsr backtest-strategy <strategy_name>` CLI command to invoke it.

The dashboard's "Backtest explorer" panel (planned in [docs/architecture.md](../architecture.md)) reads from `WalkForwardResult` and renders these reports.

## Bias prevention (carried forward + sharpened)

- **Time discipline (ADR 0003):** unchanged. The harness builds `MarketDataReader` and `ticker_signals` from queries with `t <= ctx.as_of`. Any strategy implementation that bypasses the `ctx` to reach the global DB is a code-review hard-fail.
- **Test-window hygiene:** T2 is fixed once and lives in `configs/settings.yaml`. The harness refuses to evaluate a strategy whose `version` has been changed since T2 was last touched (you must bump the version, which forces a fresh test run).
- **Universe survivorship:** the strategy universe at `as_of` includes all tickers in `configs/universe.csv` that were tradeable at `as_of`. Tickers delisted before `as_of` are excluded; tickers added after `as_of` are excluded. (Survivorship bias is the most common silent failure in backtests of this kind.)
- **Cost model:** the per-call `round_trip_cost_bps=10` from ADR 0003 applies per executed leg. Strategy-level reports also stress-test at 5/10/25/50 bps to expose cost sensitivity.

## Sanity checks (run on every walk-forward result)

- The equity curve never references prices with `bar_time > rebalance_datetime`. Hard fail if so.
- For every closed trade, `entry_at <= exit_at` and both are within the test slice. Hard fail if not.
- Distribution check: trade-level return distribution should overlap meaningfully with the matched SPY-window distribution. A *too-good* result (mean return > benchmark mean by >2σ with N>30) gets flagged for manual leakage investigation before being trusted.
- `n_trades` per strategy is reported; below `min_n` (configurable, default 30) the result is labeled "underpowered" in the report and dashboard.

## Things explicitly out of scope (V1)

- Position sizing beyond equal-weight or single-decision weights. Kelly / vol-targeting comes later.
- Slippage models more complex than constant bps.
- Dividend-adjusted returns beyond yfinance auto-adjusted close.
- Portfolio-level risk constraints (sector caps, beta neutrality).
- Live paper-trading. The harness is historical only.
- Options strategies. Equity-only.

## Consequences

- The Phase 5 "ranking model" reframes as: a `Strategy` whose `evaluate` calls a trained model. Training data and feature pipeline come from the same `TickerSignal` aggregates that power the simpler strategies, which means the model has a working baseline to beat.
- Walk-forward results are persisted; the dashboard can compare strategies over time without re-running.
- A strategy with poor calibration but good return is still allowed to ship — but it ships with a calibration warning, surfaced to the user. Honesty is the V1 substitute for sophistication.
- Migration: existing per-call `OutcomeWindow` data is the truth set for activated trade calls; the harness reads from it directly. No data is recomputed on this ADR.
