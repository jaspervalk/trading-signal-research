"""Unit tests for portfolio metrics (ADR 0007 §4)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.backtest.metrics import (
    Trade,
    cagr,
    compute_walkforward_metrics,
    equity_curve,
    hit_rate,
    hit_rate_with_ci,
    max_drawdown,
    regime_breakdown,
    sharpe,
    turnover,
    wilson_ci,
    win_loss_ratio,
)


def _trade(
    *,
    ticker: str = "AAPL",
    entry_offset_days: int = 0,
    holding_days: int = 5,
    return_pct: float = 0.02,
    weight: float = 0.1,
    regime: str = "up",
    benchmark_return: float | None = 0.005,
) -> Trade:
    base = datetime(2025, 1, 1, tzinfo=UTC)
    return Trade(
        ticker=ticker,
        direction="long",
        entry_at=base + timedelta(days=entry_offset_days),
        exit_at=base + timedelta(days=entry_offset_days + holding_days),
        entry_price=100.0,
        exit_price=100.0 * (1 + return_pct),
        return_pct=return_pct,
        weight=weight,
        regime_at_entry=regime,
        holding_days=holding_days,
        benchmark_return_pct=benchmark_return,
    )


# ---------------------------------------------------------------------------
# Equity curve


def test_equity_curve_empty_trades_returns_empty_series():
    out = equity_curve([])
    assert out.empty


def test_equity_curve_compounds_returns_in_order():
    trades = [
        _trade(entry_offset_days=0, holding_days=5, return_pct=0.10, weight=1.0),
        _trade(entry_offset_days=5, holding_days=5, return_pct=0.10, weight=1.0),
    ]
    curve = equity_curve(trades)
    # Two compounded 10% wins from NAV=1.0 → final = 1.0 * 1.1 * 1.1 = 1.21
    assert curve.iloc[-1] == pytest.approx(1.21, rel=1e-6)


def test_equity_curve_applies_weight():
    trade = _trade(return_pct=0.10, weight=0.5)
    curve = equity_curve([trade])
    # 50% weight × 10% return → 5% portfolio impact
    assert curve.iloc[-1] == pytest.approx(1.05, rel=1e-6)


# ---------------------------------------------------------------------------
# CAGR / max DD / Sharpe


def test_cagr_returns_none_for_short_window():
    base = datetime(2025, 1, 1, tzinfo=UTC)
    curve = pd.Series([1.0, 1.05], index=pd.DatetimeIndex(
        [base, base + timedelta(days=10)], tz="UTC"
    ))
    assert cagr(curve) is None


def test_cagr_computes_for_one_year_doubling():
    base = datetime(2025, 1, 1, tzinfo=UTC)
    curve = pd.Series([1.0, 2.0], index=pd.DatetimeIndex(
        [base, base + timedelta(days=365)], tz="UTC"
    ))
    out = cagr(curve)
    assert out == pytest.approx(1.0, rel=0.01)  # 100% CAGR


def test_max_drawdown_returns_negative_for_drawdown():
    base = datetime(2025, 1, 1, tzinfo=UTC)
    idx = pd.DatetimeIndex(
        [base + timedelta(days=i) for i in range(4)], tz="UTC"
    )
    curve = pd.Series([1.0, 1.5, 0.9, 1.1], index=idx)
    out = max_drawdown(curve)
    assert out == pytest.approx((0.9 - 1.5) / 1.5, rel=1e-6)


def test_max_drawdown_zero_for_monotonic_uptrend():
    base = datetime(2025, 1, 1, tzinfo=UTC)
    idx = pd.DatetimeIndex(
        [base + timedelta(days=i) for i in range(5)], tz="UTC"
    )
    curve = pd.Series([1.0, 1.1, 1.2, 1.3, 1.4], index=idx)
    assert max_drawdown(curve) == pytest.approx(0.0)


def test_sharpe_returns_none_for_too_few_trades():
    trades = [_trade(entry_offset_days=i * 5) for i in range(3)]
    assert sharpe(equity_curve(trades)) is None


def test_sharpe_positive_for_mostly_winning_curve():
    # Mix of mostly-positive returns with some variance — Sharpe is undefined
    # when std = 0, so we need realistic dispersion.
    returns = [0.02, 0.01, 0.03, 0.005, 0.025, -0.005, 0.015, 0.02, 0.03, 0.01,
               0.02, 0.005, 0.025, 0.015, -0.01, 0.02, 0.025, 0.01, 0.015, 0.02]
    trades = [_trade(entry_offset_days=i * 5, return_pct=r) for i, r in enumerate(returns)]
    out = sharpe(equity_curve(trades))
    assert out is not None and out > 0


# ---------------------------------------------------------------------------
# Hit rate + Wilson CI


def test_hit_rate_simple():
    trades = [
        _trade(return_pct=0.10),
        _trade(return_pct=-0.05),
        _trade(return_pct=0.02),
    ]
    assert hit_rate(trades) == pytest.approx(2 / 3)


def test_hit_rate_with_ci_returns_bounds():
    trades = [_trade(return_pct=0.01) for _ in range(8)] + [_trade(return_pct=-0.01) for _ in range(2)]
    hr, ci = hit_rate_with_ci(trades)
    assert hr == pytest.approx(0.8)
    assert ci is not None
    lo, hi = ci
    assert lo < 0.8 < hi


def test_wilson_ci_returns_none_for_n_zero():
    assert wilson_ci(0, 0) is None


# ---------------------------------------------------------------------------
# Turnover + win/loss + regime


def test_turnover_annualizes_correctly():
    trades = [_trade(weight=0.5) for _ in range(4)]
    # 4 × 0.5 = 2.0 weight traded over 365 days → 2.0 annualized
    out = turnover(trades, period_days=365.25)
    assert out == pytest.approx(2.0, rel=1e-3)


def test_win_loss_ratio_returns_none_when_no_losses():
    trades = [_trade(return_pct=0.05) for _ in range(3)]
    assert win_loss_ratio(trades) is None


def test_win_loss_ratio_computes_basic_case():
    trades = [
        _trade(return_pct=0.10),
        _trade(return_pct=0.20),
        _trade(return_pct=-0.05),
        _trade(return_pct=-0.05),
    ]
    # avg_win = 0.15, avg_loss = 0.05 → ratio = 3.0
    assert win_loss_ratio(trades) == pytest.approx(3.0, rel=1e-6)


def test_regime_breakdown_groups_correctly():
    trades = [
        _trade(regime="up", return_pct=0.05),
        _trade(regime="up", return_pct=-0.02),
        _trade(regime="down", return_pct=-0.10),
    ]
    out = regime_breakdown(trades)
    assert out["up"]["n"] == 2
    assert out["down"]["n"] == 1
    assert out["up"]["hit_rate"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Composite metrics


def test_compute_walkforward_metrics_marks_underpowered():
    trades = [_trade(entry_offset_days=i * 5) for i in range(5)]
    metrics = compute_walkforward_metrics(
        trades, benchmark_curve=None, test_period_days=30, min_n_trades=30
    )
    assert metrics.underpowered is True
    assert metrics.n_trades == 5


def test_compute_walkforward_metrics_excess_cagr_uses_benchmark():
    base = datetime(2025, 1, 1, tzinfo=UTC)
    bench_idx = pd.DatetimeIndex(
        [base + timedelta(days=i * 30) for i in range(13)], tz="UTC"
    )
    # Benchmark up 50% over a year.
    bench = pd.Series([1.0 + 0.5 * (i / 12) for i in range(13)], index=bench_idx)
    trades = [
        _trade(entry_offset_days=i * 25, holding_days=5, return_pct=0.05)
        for i in range(13)
    ]
    metrics = compute_walkforward_metrics(
        trades, benchmark_curve=bench, test_period_days=365, min_n_trades=10
    )
    assert metrics.benchmark_cagr is not None
    assert metrics.cagr is not None
    assert metrics.excess_cagr == pytest.approx(metrics.cagr - metrics.benchmark_cagr)
