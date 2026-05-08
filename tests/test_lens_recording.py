"""Tests for lens-snapshot recording from Deep mode.

Records are idempotent on (ticker, as_of, mode, lens_name) so re-running
the same Deep call (e.g. force=true) does not duplicate snapshots.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, LensSnapshot
from app.research.lens_recording import record_lens_snapshots
from app.research.schema import LensView


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _lens(name: str, direction: str = "bullish", conviction: str = "high") -> LensView:
    return LensView(
        name=name,
        direction=direction,
        conviction=conviction,
        summary=f"stub {name}",
        points=["p1", "p2"],
    )


def test_record_writes_one_row_per_lens(session: Session):
    lenses = [
        _lens("quantitative"),
        _lens("fundamental", direction="bearish"),
        _lens("sentiment_macro", conviction="low"),
        _lens("contrarian_risk", direction="neutral"),
    ]
    ids = record_lens_snapshots(
        session,
        plan_id=None,
        ticker="AAPL",
        as_of=datetime(2026, 5, 8, tzinfo=UTC),
        mode="deep",
        lenses=lenses,
        sources_used=["technicals", "claims"],
        durations_ms=[100, 120, 110, 130],
        costs_usd=[0.001, 0.002, 0.001, 0.0015],
    )
    assert len(ids) == 4
    rows = session.execute(select(LensSnapshot)).scalars().all()
    assert len(rows) == 4
    by_name = {r.lens_name: r for r in rows}
    assert by_name["fundamental"].direction == "bearish"
    assert by_name["sentiment_macro"].conviction == "low"
    assert json.loads(by_name["quantitative"].points_json) == ["p1", "p2"]


def test_record_is_idempotent_on_run_key(session: Session):
    lens = _lens("quantitative")
    args = dict(
        plan_id=None,
        ticker="AAPL",
        as_of=datetime(2026, 5, 8, tzinfo=UTC),
        mode="deep",
        sources_used=[],
        durations_ms=[100],
        costs_usd=[0.001],
    )
    record_lens_snapshots(session, lenses=[lens], **args)
    record_lens_snapshots(session, lenses=[lens], **args)  # second run, same key
    rows = session.execute(select(LensSnapshot)).scalars().all()
    assert len(rows) == 1


def test_record_handles_per_lens_cost_mismatch(session: Session):
    """If costs_usd is shorter than lenses, missing entries default to 0.0."""
    lenses = [_lens("quantitative"), _lens("fundamental")]
    record_lens_snapshots(
        session=session,
        plan_id=None,
        ticker="AAPL",
        as_of=datetime(2026, 5, 8, tzinfo=UTC),
        mode="deep",
        lenses=lenses,
        sources_used=[],
        durations_ms=[100],  # only one — second defaults to 0
        costs_usd=[0.001],
    )
    rows = session.execute(select(LensSnapshot)).scalars().all()
    by_name = {r.lens_name: r for r in rows}
    assert by_name["quantitative"].duration_ms == 100
    assert by_name["fundamental"].duration_ms == 0
