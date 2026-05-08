"""Smoke run for the walk-forward harness against the real DB + yfinance.

Run with:  .venv/bin/python _design/_smoke_walkforward.py

Not in tests/ because it makes real network calls. Used to verify the
harness produces a sensible-shaped result on actual data.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.backtest.walkforward import WalkForwardConfig, run_walkforward
from app.strategies.mention_momentum import MentionMomentum


def main() -> None:
    # Use a 6-month window ending well before t2_cutoff (2026-01-01).
    end = datetime(2025, 12, 1, tzinfo=UTC)
    start = end - timedelta(days=180)

    cfg = WalkForwardConfig(
        start=start,
        end=end,
        rebalance="weekly",
        cost_bps=10,
        min_n_trades=30,
    )
    strategy = MentionMomentum()

    result = run_walkforward(strategy, cfg)

    print()
    print(f"  STRATEGY  {result.strategy_name}@{result.strategy_version}  config={result.config_hash}")
    print(f"  WINDOW    {start.date()} → {end.date()}  rebalance={cfg.rebalance}  cost_bps={cfg.cost_bps}")
    print(f"  RESULT    n_rebalances={result.n_rebalances}  n_trades={len(result.trades)}  "
          f"underpowered={result.underpowered}")
    print()

    m = result.metrics
    print("  ── Metrics ──")
    print(f"    cagr             {_fmt_pct(m.cagr)}")
    print(f"    benchmark_cagr   {_fmt_pct(m.benchmark_cagr)}")
    print(f"    excess_cagr      {_fmt_pct(m.excess_cagr)}")
    print(f"    sharpe           {_fmt_num(m.sharpe)}")
    print(f"    max_drawdown     {_fmt_pct(m.max_drawdown)}")
    print(f"    annualized_vol   {_fmt_pct(m.annualized_vol)}")
    print(f"    hit_rate         {_fmt_pct(m.hit_rate, 0)}  CI={_fmt_ci(m.hit_rate_ci)}")
    print(f"    avg_holding_days {_fmt_num(m.avg_holding_days, 1)}")
    print(f"    win_loss_ratio   {_fmt_num(m.win_loss_ratio)}")
    print(f"    turnover         {_fmt_num(m.turnover, 2)}")
    print()

    if m.regime_breakdown:
        print("  ── Regime breakdown ──")
        for regime, stats in m.regime_breakdown.items():
            print(f"    {regime:8s}  n={stats['n']}  hr={_fmt_pct(stats['hit_rate'], 0)}  "
                  f"mean_ret={_fmt_pct(stats['mean_return_pct'])}")
        print()

    print("  ── Sanity checks ──")
    for c in result.sanity_checks:
        marker = "✓" if c.passed else "✗"
        print(f"    {marker} {c.name:30s}  {c.detail}")
    print()

    if result.warnings:
        print("  ── Warnings ──")
        for w in result.warnings[:5]:
            print(f"    · {w}")
        if len(result.warnings) > 5:
            print(f"    ({len(result.warnings) - 5} more)")
        print()

    print(f"  ── First 5 trades ──")
    for t in result.trades[:5]:
        print(f"    {t.entry_at.date()} → {t.exit_at.date()}  "
              f"{t.direction} {t.ticker:5s}  ret={_fmt_pct(t.return_pct, 2)}  "
              f"regime={t.regime_at_entry}  weight={t.weight:.2f}")
    print()


def _fmt_pct(v, digits: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.{digits}f}%"


def _fmt_num(v, digits: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}"


def _fmt_ci(ci) -> str:
    if ci is None:
        return "—"
    return f"[{ci[0] * 100:.0f}-{ci[1] * 100:.0f}%]"


if __name__ == "__main__":
    main()
