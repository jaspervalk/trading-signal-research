"""Portfolio-level metrics for walk-forward results.

Pure functions on equity curves and per-trade dicts. No DB, no IO. Each
metric is named, transparent, and tested against synthetic curves where the
expected value is known.

Conventions:
- All returns are *fractional* (0.05 = +5%), not percent.
- Equity curves are pandas Series indexed by tz-aware UTC timestamps with a
  starting value of 1.0.
- "trading_days_per_year" is the standard 252.
- Risk-free rate is 0 in V1 (per ADR 0007 §4 simplification).
- Wilson 95% CI is the same formula used elsewhere in the project (see
  `app.scoring.metrics`); reused here for hit-rate confidence bounds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Trade record


@dataclass(frozen=True)
class Trade:
    """One closed simulated position.

    `entry_at` / `exit_at` are tz-aware UTC.
    `return_pct` is signed in the trade's direction and net of round-trip cost.
    `regime_at_entry` is one of `up` | `flat` | `down` per ADR 0003 — derived
    from SPY's 21d trend at entry.
    """

    ticker: str
    direction: str  # 'long' | 'short'
    entry_at: datetime
    exit_at: datetime
    entry_price: float
    exit_price: float
    return_pct: float
    weight: float
    regime_at_entry: str = "unknown"
    holding_days: int = 0
    strategy_score: float | None = None
    benchmark_return_pct: float | None = None


# ---------------------------------------------------------------------------
# Equity-curve metrics


def equity_curve(trades: list[Trade]) -> pd.Series:
    """Build a per-trade equity curve from a list of (chronologically
    ordered) closed trades. Starting NAV = 1.0; each trade scales NAV by
    `1 + weight × return_pct`.

    Returns an empty Series for an empty trade list.
    """
    if not trades:
        return pd.Series(dtype=float)
    sorted_trades = sorted(trades, key=lambda t: t.exit_at)
    nav = 1.0
    points = [(sorted_trades[0].entry_at, 1.0)]
    for t in sorted_trades:
        nav *= 1.0 + t.weight * t.return_pct
        points.append((t.exit_at, nav))
    idx = pd.DatetimeIndex([p[0] for p in points], tz="UTC")
    return pd.Series([p[1] for p in points], index=idx)


def cagr(curve: pd.Series) -> float | None:
    """Compound annual growth rate of the equity curve.

    `((end_nav / start_nav) ** (1 / years)) - 1`. Returns None if the curve
    spans less than ~30 days (CAGR is meaningless on tiny windows).
    """
    if len(curve) < 2:
        return None
    days = (curve.index[-1] - curve.index[0]).total_seconds() / 86400
    if days < 30:
        return None
    years = days / 365.25
    end = float(curve.iloc[-1])
    start = float(curve.iloc[0])
    if start <= 0 or end <= 0:
        return None
    return (end / start) ** (1.0 / years) - 1.0


def max_drawdown(curve: pd.Series) -> float | None:
    """Peak-to-trough drawdown on the equity curve, returned as a *negative*
    fraction. -0.20 = 20% drawdown. Returns None on an empty curve.
    """
    if curve.empty:
        return None
    running_max = curve.cummax()
    drawdowns = curve / running_max - 1.0
    return float(drawdowns.min())


def sharpe(curve: pd.Series, *, rf: float = 0.0) -> float | None:
    """Annualized Sharpe ratio computed on per-trade returns derived from
    the equity curve. With rf=0, this is `mean(ret)/std(ret) × √252`.

    Returns None when fewer than 5 trades or when std is exactly 0.
    """
    if len(curve) < 6:
        return None
    rets = curve.pct_change().dropna()
    if rets.empty:
        return None
    mu = float(rets.mean()) - rf / TRADING_DAYS_PER_YEAR
    sigma = float(rets.std(ddof=1))
    if sigma == 0 or math.isnan(sigma):
        return None
    return (mu / sigma) * math.sqrt(TRADING_DAYS_PER_YEAR)


def annualized_vol(curve: pd.Series) -> float | None:
    if len(curve) < 6:
        return None
    rets = curve.pct_change().dropna()
    if rets.empty:
        return None
    sigma = float(rets.std(ddof=1))
    if math.isnan(sigma):
        return None
    return sigma * math.sqrt(TRADING_DAYS_PER_YEAR)


# ---------------------------------------------------------------------------
# Trade-level metrics


def hit_rate(trades: list[Trade]) -> float | None:
    if not trades:
        return None
    wins = sum(1 for t in trades if t.return_pct > 0)
    return wins / len(trades)


def wilson_ci(k: int, n: int, *, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson 95% CI for a binomial proportion. Returns (lower, upper)."""
    if n <= 0:
        return None
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def hit_rate_with_ci(trades: list[Trade]) -> tuple[float | None, tuple[float, float] | None]:
    if not trades:
        return None, None
    n = len(trades)
    k = sum(1 for t in trades if t.return_pct > 0)
    return k / n, wilson_ci(k, n)


def avg_holding_days(trades: list[Trade]) -> float | None:
    if not trades:
        return None
    return sum(t.holding_days for t in trades) / len(trades)


def win_loss_ratio(trades: list[Trade]) -> float | None:
    """Average win / |average loss|. None if there are no losses."""
    wins = [t.return_pct for t in trades if t.return_pct > 0]
    losses = [t.return_pct for t in trades if t.return_pct < 0]
    if not wins or not losses:
        return None
    avg_win = sum(wins) / len(wins)
    avg_loss = abs(sum(losses) / len(losses))
    if avg_loss == 0:
        return None
    return avg_win / avg_loss


def turnover(trades: list[Trade], *, period_days: float) -> float | None:
    """Annualized fraction of portfolio replaced.

    Each trade represents `weight` of the portfolio being recycled. Sum the
    weights and annualize over the test window.
    """
    if not trades or period_days <= 0:
        return None
    total_weight_traded = sum(t.weight for t in trades)
    return total_weight_traded * (365.25 / period_days)


# ---------------------------------------------------------------------------
# Regime breakdown


def regime_breakdown(trades: list[Trade]) -> dict[str, dict[str, Any]]:
    """Split the trade-set by regime and report N + hit rate + mean return."""
    out: dict[str, dict[str, Any]] = {}
    by_regime: dict[str, list[Trade]] = {}
    for t in trades:
        by_regime.setdefault(t.regime_at_entry, []).append(t)
    for regime, group in by_regime.items():
        n = len(group)
        wins = sum(1 for t in group if t.return_pct > 0)
        hr, ci = hit_rate_with_ci(group)
        out[regime] = {
            "n": n,
            "hit_rate": hr,
            "hit_rate_ci": ci,
            "mean_return_pct": sum(t.return_pct for t in group) / n if n else None,
        }
    return out


# ---------------------------------------------------------------------------
# Composite report


@dataclass
class WalkForwardMetrics:
    """The full metrics block per ADR 0007 §4."""

    n_trades: int
    cagr: float | None
    sharpe: float | None
    max_drawdown: float | None
    annualized_vol: float | None
    hit_rate: float | None
    hit_rate_ci: tuple[float, float] | None
    benchmark_cagr: float | None
    excess_cagr: float | None
    benchmark_return_total: float | None
    avg_holding_days: float | None
    win_loss_ratio: float | None
    turnover: float | None
    underpowered: bool
    regime_breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)


def compute_walkforward_metrics(
    trades: list[Trade],
    *,
    benchmark_curve: pd.Series | None,
    test_period_days: float,
    min_n_trades: int = 30,
) -> WalkForwardMetrics:
    """Compose all metrics into one report."""
    curve = equity_curve(trades)
    hr, ci = hit_rate_with_ci(trades)
    bench_cagr = cagr(benchmark_curve) if benchmark_curve is not None and not benchmark_curve.empty else None
    bench_total = (
        float(benchmark_curve.iloc[-1] / benchmark_curve.iloc[0] - 1)
        if benchmark_curve is not None and len(benchmark_curve) >= 2
        else None
    )
    strat_cagr = cagr(curve)
    excess = (
        strat_cagr - bench_cagr
        if strat_cagr is not None and bench_cagr is not None
        else None
    )
    return WalkForwardMetrics(
        n_trades=len(trades),
        cagr=strat_cagr,
        sharpe=sharpe(curve),
        max_drawdown=max_drawdown(curve),
        annualized_vol=annualized_vol(curve),
        hit_rate=hr,
        hit_rate_ci=ci,
        benchmark_cagr=bench_cagr,
        excess_cagr=excess,
        benchmark_return_total=bench_total,
        avg_holding_days=avg_holding_days(trades),
        win_loss_ratio=win_loss_ratio(trades),
        turnover=turnover(trades, period_days=test_period_days),
        underpowered=len(trades) < min_n_trades,
        regime_breakdown=regime_breakdown(trades),
    )


__all__ = [
    "TRADING_DAYS_PER_YEAR",
    "Trade",
    "WalkForwardMetrics",
    "annualized_vol",
    "avg_holding_days",
    "cagr",
    "compute_walkforward_metrics",
    "equity_curve",
    "hit_rate",
    "hit_rate_with_ci",
    "max_drawdown",
    "regime_breakdown",
    "sharpe",
    "turnover",
    "wilson_ci",
    "win_loss_ratio",
]
