"""Tests for lens outcome computation: realised returns + direction-correct
classification at 1d/3d/5d/21d horizons.

Uses an in-memory SQLite + a stub market reader that returns deterministic
returns. Real market data is exercised only by integration tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, LensOutcome, LensSnapshot
from app.scoring.lens_outcomes import (
    HORIZONS,
    compute_lens_outcomes,
    direction_correct,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _snapshot(session: Session, **overrides) -> LensSnapshot:
    defaults = dict(
        plan_id=None,
        ticker="AAPL",
        # as_of must satisfy TWO constraints from compute_lens_outcomes:
        # (1) be within max_age_days=60 to avoid freshness filter at line 57-64
        # (2) be old enough that 21d horizon has elapsed (line 70-71 check)
        # This offset (45d) meets both: in-window AND far enough for all horizons.
        as_of=datetime.now(UTC) - timedelta(days=45),
        mode="deep",
        lens_name="quantitative",
        direction="bullish",
        conviction="high",
        summary="stub",
        points_json=json.dumps(["p1"]),
        sources_used=json.dumps([]),
        cost_usd=0.001,
        duration_ms=100,
    )
    defaults.update(overrides)
    row = LensSnapshot(**defaults)
    session.add(row)
    session.flush()
    return row


class _StubMarket:
    """Returns deterministic returns by horizon + regime."""

    def __init__(
        self,
        returns_by_horizon: dict[str, float],
        spy_returns: dict[str, float],
        regime: str = "up",
    ):
        self.returns_by_horizon = returns_by_horizon
        self.spy_returns = spy_returns
        self.regime = regime

    def ticker_return(self, ticker: str, as_of: datetime, horizon: str) -> float | None:
        return self.returns_by_horizon.get(horizon)

    def spy_return(self, as_of: datetime, horizon: str) -> float | None:
        return self.spy_returns.get(horizon)

    def regime_for(self, as_of: datetime) -> str:
        return self.regime


def test_direction_correct_bullish_up_move():
    assert direction_correct("bullish", 0.025) is True
    assert direction_correct("bullish", -0.005) is False


def test_direction_correct_bearish_down_move():
    assert direction_correct("bearish", -0.030) is True
    assert direction_correct("bearish", 0.010) is False


def test_direction_correct_neutral_within_threshold():
    """Neutral is correct when |return| <= 0.5% (small move)."""
    assert direction_correct("neutral", 0.003) is True
    assert direction_correct("neutral", -0.004) is True
    assert direction_correct("neutral", 0.020) is False  # too big to be neutral


def test_compute_lens_outcomes_writes_one_row_per_horizon(session: Session):
    _snapshot(session)
    market = _StubMarket(
        returns_by_horizon={"1d": 0.005, "3d": 0.012, "5d": 0.025, "21d": 0.060},
        spy_returns={"1d": 0.001, "3d": 0.005, "5d": 0.010, "21d": 0.020},
        regime="up",
    )
    n = compute_lens_outcomes(session, market=market, max_age_days=60)
    assert n == 4  # 4 horizons
    rows = session.execute(select(LensOutcome).order_by(LensOutcome.horizon)).scalars().all()
    horizons = sorted(r.horizon for r in rows)
    assert horizons == sorted(HORIZONS)
    by_h = {r.horizon: r for r in rows}
    assert by_h["5d"].return_pct == pytest.approx(0.025)
    assert by_h["5d"].excess_vs_spy_pct == pytest.approx(0.025 - 0.010)
    assert by_h["5d"].direction_correct is True
    assert by_h["5d"].regime == "up"


def test_compute_lens_outcomes_is_idempotent(session: Session):
    _snapshot(session)
    market = _StubMarket(
        returns_by_horizon={"1d": 0.005, "3d": 0.012, "5d": 0.025, "21d": 0.060},
        spy_returns={"1d": 0.001, "3d": 0.005, "5d": 0.010, "21d": 0.020},
    )
    compute_lens_outcomes(session, market=market, max_age_days=60)
    n2 = compute_lens_outcomes(session, market=market, max_age_days=60)
    assert n2 == 0  # already computed
    rows = session.execute(select(LensOutcome)).scalars().all()
    assert len(rows) == 4


def test_compute_lens_outcomes_skips_too_recent(session: Session):
    """Snapshots whose as_of + horizon hasn't elapsed yet are skipped."""
    _snapshot(
        session, as_of=datetime.now(UTC) - timedelta(hours=12)  # < 1d
    )
    market = _StubMarket(returns_by_horizon={"1d": 0.005}, spy_returns={"1d": 0.001})
    n = compute_lens_outcomes(session, market=market, max_age_days=60)
    assert n == 0
