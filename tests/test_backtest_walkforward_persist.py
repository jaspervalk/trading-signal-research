"""Persistence test for WalkForwardResult — round-trip + idempotency.

Uses the same synthetic stub strategy as test_backtest_walkforward, but
exercises the persist path against an in-memory SQLite engine so the test
suite doesn't pollute the dev DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.backtest.walkforward import (
    SanityCheck,
    WalkForwardConfig,
    WalkForwardResult,
    persist_result,
)
from app.backtest.metrics import Trade, compute_walkforward_metrics, equity_curve
from app.models import Base, WalkForwardResultRow


@pytest.fixture
def in_memory_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_local()
    try:
        yield session
    finally:
        session.close()


def _result_with_trades(strategy_name: str, version: str, *, n_trades: int = 5) -> WalkForwardResult:
    base = datetime(2025, 6, 1, tzinfo=UTC)
    cfg = WalkForwardConfig(
        start=base,
        end=base + timedelta(days=180),
        rebalance="weekly",
        cost_bps=10,
        min_n_trades=30,
    )
    trades = [
        Trade(
            ticker="AAPL",
            direction="long",
            entry_at=base + timedelta(days=i * 7),
            exit_at=base + timedelta(days=i * 7 + 5),
            entry_price=100.0,
            exit_price=102.0,
            return_pct=0.02,
            weight=0.5,
            regime_at_entry="up",
            holding_days=5,
            strategy_score=1.0,
            benchmark_return_pct=0.005,
        )
        for i in range(n_trades)
    ]
    metrics = compute_walkforward_metrics(
        trades, benchmark_curve=None, test_period_days=180
    )
    return WalkForwardResult(
        strategy_name=strategy_name,
        strategy_version=version,
        config_hash="test_hash_123",
        config=cfg,
        trades=trades,
        equity_curve=equity_curve(trades),
        benchmark_curve=pd.Series(dtype=float),
        metrics=metrics,
        sanity_checks=[
            SanityCheck(name="entry_le_exit_in_window", passed=True, detail="ok"),
            SanityCheck(name="powered_n_trades", passed=False, detail=f"n_trades={n_trades}"),
        ],
        warnings=["test warning"],
        n_rebalances=26,
        underpowered=True,
    )


def test_persist_creates_row_on_first_call(in_memory_session: Session):
    result = _result_with_trades("test_strategy", "v0")
    row_id = persist_result(result, session=in_memory_session)
    in_memory_session.commit()

    row = in_memory_session.scalar(
        select(WalkForwardResultRow).where(WalkForwardResultRow.id == row_id)
    )
    assert row is not None
    assert row.strategy_name == "test_strategy"
    assert row.strategy_version == "v0"
    assert row.config_hash == "test_hash_123"
    assert row.n_trades == 5
    assert row.underpowered is True


def test_persist_is_idempotent_on_natural_key(in_memory_session: Session):
    """Re-persisting the same (strategy_name, version, config_hash) updates,
    not duplicates."""
    first = _result_with_trades("test_strategy", "v0", n_trades=5)
    id_1 = persist_result(first, session=in_memory_session)
    in_memory_session.commit()

    second = _result_with_trades("test_strategy", "v0", n_trades=10)
    id_2 = persist_result(second, session=in_memory_session)
    in_memory_session.commit()

    assert id_1 == id_2
    rows = list(
        in_memory_session.scalars(select(WalkForwardResultRow)).all()
    )
    assert len(rows) == 1
    assert rows[0].n_trades == 10


def test_persist_distinguishes_by_strategy_version(in_memory_session: Session):
    persist_result(_result_with_trades("test_strategy", "v0"), session=in_memory_session)
    persist_result(_result_with_trades("test_strategy", "v1"), session=in_memory_session)
    in_memory_session.commit()
    rows = list(in_memory_session.scalars(select(WalkForwardResultRow)).all())
    assert len(rows) == 2


def test_persist_serializes_trades_and_equity_as_json(in_memory_session: Session):
    import json

    persist_result(_result_with_trades("test_strategy", "v0", n_trades=3), session=in_memory_session)
    in_memory_session.commit()
    row = in_memory_session.scalar(select(WalkForwardResultRow))
    assert row is not None

    trades = json.loads(row.trades_json)
    assert len(trades) == 3
    assert trades[0]["ticker"] == "AAPL"
    assert "entry_at" in trades[0]

    curve = json.loads(row.equity_curve_json)
    assert len(curve) >= 2  # at least the start + one trade
    # Each entry is [iso_date, nav_float]
    assert isinstance(curve[0][0], str)
    assert isinstance(curve[0][1], float)

    sanity = json.loads(row.sanity_checks_json)
    assert any(c["name"] == "entry_le_exit_in_window" for c in sanity)
