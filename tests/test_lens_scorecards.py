"""Tests for LensScorecard aggregations: Wilson CI, regime split, conviction split."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, LensOutcome, LensSnapshot
from app.scoring.lens_scorecards import LensScorecard, compute_lens_scorecards


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _row(
    session: Session,
    lens: str,
    direction: str,
    conviction: str,
    correct: bool,
    regime: str = "up",
    days_ago: int = 30,
):
    snap = LensSnapshot(
        plan_id=None,
        ticker="AAPL",
        as_of=datetime.now(UTC) - timedelta(days=days_ago),
        mode="deep",
        lens_name=lens,
        direction=direction,
        conviction=conviction,
        summary="stub",
        points_json=json.dumps([]),
        sources_used=json.dumps([]),
        cost_usd=0.0,
        duration_ms=0,
    )
    session.add(snap)
    session.flush()
    out = LensOutcome(
        snapshot_id=snap.id,
        horizon="5d",
        return_pct=0.02 if correct else -0.02,
        excess_vs_spy_pct=0.01 if correct else -0.01,
        direction_correct=correct,
        regime=regime,
    )
    session.add(out)


def test_lens_scorecard_aggregates_per_lens(session: Session):
    for _ in range(8):
        _row(session, "quantitative", "bullish", "high", correct=True)
    for _ in range(2):
        _row(session, "quantitative", "bullish", "high", correct=False)
    for _ in range(4):
        _row(session, "fundamental", "bearish", "high", correct=True)
    session.flush()

    cards = compute_lens_scorecards(session, lookback_days=90, horizon="5d")
    by_name = {c.lens_name: c for c in cards}
    assert by_name["quantitative"].n == 10
    assert by_name["quantitative"].hit_rate == pytest.approx(0.8, abs=0.001)
    assert 0.4 < by_name["quantitative"].hit_rate_lo < 0.8
    assert by_name["quantitative"].hit_rate_hi <= 1.0
    assert by_name["fundamental"].n == 4


def test_lens_scorecard_regime_split(session: Session):
    for _ in range(3):
        _row(session, "quantitative", "bullish", "high", correct=True, regime="up")
    for _ in range(3):
        _row(session, "quantitative", "bullish", "high", correct=False, regime="down")
    session.flush()

    cards = compute_lens_scorecards(session, lookback_days=90, horizon="5d")
    quant = next(c for c in cards if c.lens_name == "quantitative")
    assert quant.by_regime["up"]["hit_rate"] == pytest.approx(1.0)
    assert quant.by_regime["down"]["hit_rate"] == pytest.approx(0.0)


def test_lens_scorecard_skips_unscored_snapshots(session: Session):
    """A snapshot without a matching outcome row is excluded from the scorecard."""
    snap = LensSnapshot(
        plan_id=None,
        ticker="AAPL",
        as_of=datetime.now(UTC) - timedelta(days=10),
        mode="deep",
        lens_name="quantitative",
        direction="bullish",
        conviction="high",
        summary="stub",
        points_json=json.dumps([]),
        sources_used=json.dumps([]),
        cost_usd=0.0,
        duration_ms=0,
    )
    session.add(snap)
    session.flush()
    cards = compute_lens_scorecards(session, lookback_days=90, horizon="5d")
    assert cards == []  # no outcomes yet → no scorecards
