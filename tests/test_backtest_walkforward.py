"""Integration tests for the walk-forward harness.

These tests use a synthetic Strategy that emits deterministic decisions and
a synthetic in-memory MarketDataReader so we don't hit yfinance. This proves
the *harness mechanics* are correct: rebalance enumeration, simulation,
sanity checks, leakage controls.

A separate smoke test (test_backtest_walkforward_smoke.py, opt-in) runs
the real harness against the live DB + yfinance for end-to-end verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from unittest.mock import patch

from app.backtest.walkforward import WalkForwardConfig, run_walkforward
from app.strategies.base import (
    EvidenceRef,
    Strategy,
    StrategyContext,
    StrategyDecision,
)


# ---------------------------------------------------------------------------
# Synthetic strategy + market reader


class _AlwaysAAPLLong(Strategy):
    """Emits one long decision on AAPL at every rebalance."""

    name = "test_always_aapl"
    version = "v0"

    def evaluate(self, ctx: StrategyContext) -> list[StrategyDecision]:
        if "AAPL" not in ctx.universe:
            return []
        return [
            StrategyDecision(
                ticker="AAPL",
                score=1.0,
                direction="long",
                weight=1.0,
                holding_period_days=5,
                evidence=[EvidenceRef(kind="note", note="synthetic")],
                reasoning="test",
            )
        ]


@dataclass
class _StubReader:
    """In-memory reader that fakes yfinance with a synthetic price series."""

    as_of: datetime

    def __post_init__(self) -> None:
        # 365 days of daily bars rising 0.05%/day for AAPL and 0.02%/day for SPY.
        idx = pd.date_range(
            start=self.as_of - timedelta(days=400),
            end=self.as_of + timedelta(days=20),
            freq="D",
            tz="UTC",
        )
        self._aapl = self._make_frame(idx, daily_pct=0.0005)
        self._spy = self._make_frame(idx, daily_pct=0.0002)

    @staticmethod
    def _make_frame(idx: pd.DatetimeIndex, *, daily_pct: float) -> pd.DataFrame:
        n = len(idx)
        prices = [100.0 * (1 + daily_pct) ** i for i in range(n)]
        return pd.DataFrame(
            {
                "Open": prices,
                "High": [p * 1.005 for p in prices],
                "Low": [p * 0.995 for p in prices],
                "Close": prices,
                "Volume": [1_000_000] * n,
            },
            index=idx,
        )

    def daily_bars(self, ticker: str, *, lookback_days: int = 30) -> pd.DataFrame:
        df = self._aapl if ticker == "AAPL" else self._spy if ticker == "SPY" else pd.DataFrame()
        if df.empty:
            return df
        cutoff = pd.Timestamp(self.as_of).tz_convert("UTC") if self.as_of.tzinfo else pd.Timestamp(self.as_of).tz_localize("UTC")
        # Slice the FULL future-extending frame to as_of FIRST — this is what
        # the real reader does.
        return df[df.index <= cutoff].copy()

    def benchmark_bars(self, *, lookback_days: int = 30) -> pd.DataFrame:
        return self.daily_bars("SPY", lookback_days=lookback_days)

    def forward_bars(self, ticker: str, *, forward_days: int = 60) -> pd.DataFrame:
        df = self._aapl if ticker == "AAPL" else self._spy if ticker == "SPY" else pd.DataFrame()
        if df.empty:
            return df
        cutoff = pd.Timestamp(self.as_of).tz_convert("UTC") if self.as_of.tzinfo else pd.Timestamp(self.as_of).tz_localize("UTC")
        return df[df.index > cutoff].copy()

    def forward_benchmark_bars(self, *, forward_days: int = 60) -> pd.DataFrame:
        return self.forward_bars("SPY", forward_days=forward_days)

    def is_tradeable(self, ticker: str, *, min_volume: int = 1_000) -> bool:
        return ticker in ("AAPL", "SPY")


# ---------------------------------------------------------------------------
# Helpers to bypass DB during these unit tests


@pytest.fixture
def _no_db_loads(monkeypatch):
    """Stub out the DB-touching loaders so the harness runs without a session."""
    from app.backtest import walkforward as wf

    monkeypatch.setattr(wf, "_load_recent_calls", lambda *a, **k: [])
    monkeypatch.setattr(wf, "_load_recent_claims", lambda *a, **k: [])
    monkeypatch.setattr(wf, "_load_creator_scorecards", lambda *a, **k: {})
    monkeypatch.setattr(wf, "_load_universe", lambda *a, **k: ["AAPL"])
    # Replace MarketDataReader.at with our stub.
    monkeypatch.setattr(wf, "MarketDataReader", type("R", (), {"at": staticmethod(lambda as_of, **k: _StubReader(as_of=as_of))}))


def _post_t2_window():
    # Use a window that's well after t2_cutoff so we exercise the cutoff-clamp.
    return datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC)


def _pre_t2_window():
    return datetime(2025, 6, 1, tzinfo=UTC), datetime(2025, 9, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Tests


def test_harness_runs_against_synthetic_strategy(_no_db_loads):
    start, end = _pre_t2_window()
    cfg = WalkForwardConfig(
        start=start,
        end=end,
        rebalance="weekly",
        cost_bps=10,
        min_n_trades=5,
    )
    # Pass session=None — _no_db_loads has stubbed out everything that needs it.
    result = run_walkforward(_AlwaysAAPLLong(), cfg, session=object())

    # ~13 weekly rebalances inside a 90-day window.
    assert result.n_rebalances >= 8
    # Most rebalances should produce a fillable trade.
    assert len(result.trades) >= 5
    # All trades inside the test window with entry <= exit.
    for t in result.trades:
        assert start <= t.entry_at <= end
        assert t.entry_at <= t.exit_at


def test_harness_clamps_to_t2_cutoff(_no_db_loads):
    start, end = _post_t2_window()
    cfg = WalkForwardConfig(
        start=start,
        end=end,
        rebalance="weekly",
        cost_bps=10,
        min_n_trades=5,
    )
    result = run_walkforward(_AlwaysAAPLLong(), cfg, session=object())
    # The harness should clamp `end` to t2_cutoff (2026-01-01). Since t2 is
    # before our requested start, we expect zero rebalances.
    assert result.n_rebalances == 0
    assert result.trades == []


def test_harness_records_sanity_checks(_no_db_loads):
    start, end = _pre_t2_window()
    cfg = WalkForwardConfig(
        start=start,
        end=end,
        rebalance="weekly",
        min_n_trades=5,
    )
    result = run_walkforward(_AlwaysAAPLLong(), cfg, session=object())
    names = [c.name for c in result.sanity_checks]
    assert "entry_le_exit_in_window" in names
    assert "powered_n_trades" in names
    in_window_check = next(c for c in result.sanity_checks if c.name == "entry_le_exit_in_window")
    assert in_window_check.passed is True


def test_harness_marks_underpowered_when_n_below_threshold(_no_db_loads):
    start, end = _pre_t2_window()
    cfg = WalkForwardConfig(
        start=start,
        end=end,
        rebalance="weekly",
        min_n_trades=1000,  # impossibly high
    )
    result = run_walkforward(_AlwaysAAPLLong(), cfg, session=object())
    assert result.underpowered is True


def test_config_hash_is_stable():
    cfg1 = WalkForwardConfig(
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2025, 6, 1, tzinfo=UTC),
    )
    cfg2 = WalkForwardConfig(
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2025, 6, 1, tzinfo=UTC),
    )
    from app.backtest.walkforward import _hash_config

    s = _AlwaysAAPLLong()
    assert _hash_config(s, cfg1) == _hash_config(s, cfg2)
