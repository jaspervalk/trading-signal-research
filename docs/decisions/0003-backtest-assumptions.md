# ADR 0003 — Backtest assumptions

**Status:** accepted (V1 baseline; will evolve)
**Date:** 2026-05-03

This is the document a reviewer should read before trusting any number from the backtest. If we change anything here, the change is itself a separate ADR / a versioned notes section here, not a silent edit.

## Time discipline

- All datetimes stored UTC, tz-aware, always.
- Market math done in `America/New_York`, using `pandas_market_calendars` calendar `XNYS`.
- "Trading day" everywhere — never calendar day. 1d / 3d / 5d / 21d horizons all step in trading days.
- The **only** clock the simulator may use is `posted_at`. `discovered_at`, `ingested_at`, etc. are forbidden inputs to any backtest function.

## Activation

A call is *activated* iff it becomes a real position within `trigger_window_days` (V1: 10 trading days from `posted_at`).

| `entry_type`     | Activation rule                                                                 | Fill price                                |
|------------------|---------------------------------------------------------------------------------|-------------------------------------------|
| `market`         | Activated at next regular-session open after `posted_at`.                       | That session's open.                      |
| `limit`          | Activated when intraday price touches `entry_price` (long: low ≤ price; short: high ≥ price). | `entry_price`.                  |
| `trigger_above`  | Activated when intraday `high ≥ entry_price` after `posted_at`.                 | max(`entry_price`, that bar's open).      |
| `trigger_below`  | Activated when intraday `low ≤ entry_price` after `posted_at`.                 | min(`entry_price`, that bar's open).      |
| `unspecified`    | Treated as `market`. Outcome lives in a separate evaluation bucket so we can compare specific vs. unspecified calls. | next session's open. |

If never activated within the window, the call is `not_triggered` — its own outcome class. Not a loss, not a win.

## Outcomes

For each (call, horizon) where the call activated:

- `return_pct` = `(exit_price - fill_price) / fill_price` for long, sign-flipped for short.
- `mfe` (max favourable excursion) over the window in the call's direction.
- `mae` (max adverse excursion) over the window in the call's direction.
- `hit_target` = whether `target_price` was touched intraday before `stop_price` (only when both set).
- `hit_stop` = whether `stop_price` was touched intraday before `target_price` (only when both set).
- For activated calls without an explicit exit, `exit_price` = close of the last bar inside the horizon window.

## Costs

- `round_trip_cost_bps` = 10 bps applied as `return_pct -= 0.001` on every activated call (entry+exit combined).
- This is the V1 simplification. Slippage is implicitly bundled into this number.

## Reporting modes

Every aggregate metric must be reported in **both**:

1. **Conditional** — only activated calls. Tells you "given the call became real, did it work?"
2. **Unconditional** — non-activated calls counted as 0% return. Tells you "what does it cost to act on every call from this creator?"

These tell different stories. Picking one and hiding the other is a tell of bad analysis.

## Benchmark

For each (call, horizon):
- `benchmark_return` = SPY return over the same wall-clock window.
- `excess_vs_spy` = `return_pct - benchmark_return`.

Report creator scorecards in both raw and excess-vs-SPY terms. Excess is the more honest one.

## Regime split

Bucket each call by SPY 21d trend at `posted_at`:
- `up`: SPY 21d return > +2%
- `flat`: between -2% and +2%
- `down`: < -2%

Per-bucket aggregates per creator. Sample sizes will often be too small — that's the point of showing them.

## Splits (training the ranker)

- Time-based splits only. No random shuffling.
- Train ≤ T1, val ∈ (T1, T2], test > T2. T2 is set once and never moved.
- No hyperparameter tuning on the test window.
- Creator features for any call must be computed using **only** evaluated calls strictly before that call's `posted_at`. The feature builder enforces this; any leak is a build error.

## Sanity checks (run on every backtest result set)

- For every call, `activation_at >= posted_at`. Hard fail if not.
- For every activated call, `entry_fill_price` is within the day's range at `activation_at`. Hard fail if not.
- For every call, the price series used to evaluate it does not include any bar with `bar_time < posted_at`. Hard fail if not.
- Distribution check: median `return_pct` for activated calls should be roughly comparable to the matched SPY window. Wildly higher → suspect leakage.

## Things we are deliberately NOT doing in V1

- Intraday simulation finer than 1 hour.
- Modeling slippage as a function of liquidity.
- Borrow costs / hard-to-borrow penalties for shorts.
- Dividend-adjusted returns (we use yfinance auto-adjusted close as the V1 compromise).
- Multi-leg options simulation. Out of scope.
