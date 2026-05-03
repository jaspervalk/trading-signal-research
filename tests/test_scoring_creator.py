"""Integration test for creator scoring against an in-memory DB.

Builds a tiny universe (1 creator, a few calls + outcomes), runs scoring,
and verifies the persisted CreatorScorecard fields.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    CALL_STATUS_ACCEPTED,
    Creator,
    CreatorScorecard,
    DIRECTION_LONG,
    Document,
    ENTRY_MARKET,
    ExtractedCall,
    OUTCOME_STATUS_EVALUATED,
    OUTCOME_STATUS_NOT_TRIGGERED,
    OutcomeWindow,
    SourceChannel,
)


@pytest.fixture
def populated_db(monkeypatch, engine, session):
    """Wire app.scoring.creator's session_scope to use the test session."""
    # Build creator + channel + 6 documents/calls/outcomes (5d horizon).
    creator = Creator(display_name="Test Creator")
    session.add(creator)
    session.flush()
    ch = SourceChannel(
        creator_id=creator.id, source_type="youtube", external_id="UCtest"
    )
    session.add(ch)
    session.flush()

    base = datetime(2026, 4, 1, tzinfo=UTC)
    for i, (ret, excess, activated) in enumerate(
        [
            (0.05, 0.04, True),
            (0.02, 0.01, True),
            (-0.01, -0.02, True),
            (0.08, 0.06, True),
            (None, None, False),  # not activated
            (-0.03, -0.04, True),
        ]
    ):
        doc = Document(
            source_channel_id=ch.id, source_type="youtube",
            external_id=f"v{i}", title=f"vid{i}",
            posted_at=base - timedelta(days=i * 3),
        )
        session.add(doc)
        session.flush()
        call = ExtractedCall(
            document_id=doc.id,
            ticker=f"TKR{i}",
            direction=DIRECTION_LONG,
            entry_type=ENTRY_MARKET,
            extractor_version="v0.1",
            final_confidence=0.9,
            status=CALL_STATUS_ACCEPTED,
        )
        session.add(call)
        session.flush()
        ow = OutcomeWindow(
            call_id=call.id,
            horizon="5d",
            window_start_at=doc.posted_at,
            window_end_at=doc.posted_at + timedelta(days=7),
            activated=activated,
            activation_at=doc.posted_at if activated else None,
            entry_fill_price=100.0 if activated else None,
            return_pct=ret,
            excess_return_pct=excess,
            mae=-0.02 if activated else None,
            evaluator_version="v0.1",
            status=OUTCOME_STATUS_EVALUATED if activated else OUTCOME_STATUS_NOT_TRIGGERED,
        )
        session.add(ow)
    session.commit()

    # Patch session_scope used by app.scoring.creator to yield our test session.
    from contextlib import contextmanager

    @contextmanager
    def _scope():
        try:
            yield session
        finally:
            pass  # don't close — fixture owns it

    monkeypatch.setattr("app.scoring.creator.session_scope", _scope)
    return creator


def test_compute_scorecard_basic(populated_db):
    from app.scoring.creator import compute_scorecards

    cards = compute_scorecards(
        creator_ids=[populated_db.id], horizons=["5d"], windows=["all"],
        now=datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert len(cards) == 1
    c = cards[0]
    assert c.n_calls == 6
    assert c.n_activated == 5
    assert c.activation_rate == pytest.approx(5 / 6)
    # hit_rate: 4 of 5 activated returns are positive (0.05, 0.02, 0.08, then -0.01 and -0.03 negatives → 3 positive)
    assert c.hit_rate == pytest.approx(3 / 5)
    assert c.hit_rate_lower_ci is not None and c.hit_rate_lower_ci < c.hit_rate
    assert c.hit_rate_upper_ci is not None and c.hit_rate_upper_ci > c.hit_rate
    assert c.mean_return == pytest.approx((0.05 + 0.02 - 0.01 + 0.08 - 0.03) / 5)
    # excess hit rate: 3 of 5 are > 0
    assert c.excess_hit_rate == pytest.approx(3 / 5)


def test_below_min_n_returns_none(populated_db):
    from app.scoring.creator import compute_scorecards
    from app.config import load_project_settings

    # Ask for a window with no data
    cards = compute_scorecards(
        creator_ids=[populated_db.id],
        horizons=["5d"],
        windows=["90d"],
        now=datetime(2030, 1, 1, tzinfo=UTC),  # far future, all data falls outside
    )
    assert cards == []
