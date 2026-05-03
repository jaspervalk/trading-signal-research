"""End-to-end API tests against a fresh in-memory database.

Builds a small fixture graph (creator + doc + segment + call + scorecard)
then exercises the read + write endpoints via FastAPI's TestClient.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.models import (
    CALL_STATUS_ACCEPTED,
    Creator,
    CreatorScorecard,
    DIRECTION_LONG,
    Document,
    ENTRY_MARKET,
    ExtractedCall,
    OUTCOME_STATUS_EVALUATED,
    OutcomeWindow,
    SourceChannel,
    TranscriptSegment,
)


@pytest.fixture
def client(engine, session):
    """Use FastAPI's dependency_overrides to inject the test session."""
    from apps.api.app.deps import db_session
    from apps.api.app.main import app

    def _override():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[db_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def seeded(client, session):
    creator = Creator(display_name="Test Creator")
    session.add(creator)
    session.flush()
    ch = SourceChannel(
        creator_id=creator.id, source_type="youtube", external_id="UCtest"
    )
    session.add(ch)
    session.flush()
    doc = Document(
        source_channel_id=ch.id, source_type="youtube", external_id="vtest",
        title="A test video", posted_at=datetime(2026, 5, 1, tzinfo=UTC),
        url="https://x.test/v", duration_seconds=600,
    )
    session.add(doc)
    session.flush()
    seg = TranscriptSegment(
        document_id=doc.id, start_seconds=0, end_seconds=5, text="NVDA above 920",
    )
    session.add(seg)
    session.flush()
    call = ExtractedCall(
        document_id=doc.id, primary_segment_id=seg.id,
        ticker="NVDA", direction=DIRECTION_LONG, entry_type=ENTRY_MARKET,
        timeframe="swing", evidence_quote="NVDA above 920",
        extractor_version="v0.1", llm_confidence=0.9, rule_confidence=1.0,
        final_confidence=0.95, status=CALL_STATUS_ACCEPTED,
    )
    session.add(call)
    session.flush()
    ow = OutcomeWindow(
        call_id=call.id, horizon="5d",
        window_start_at=doc.posted_at, window_end_at=doc.posted_at + timedelta(days=7),
        activated=True, activation_at=doc.posted_at, entry_fill_price=920,
        return_pct=0.05, excess_return_pct=0.04,
        status=OUTCOME_STATUS_EVALUATED, evaluator_version="v0.1",
    )
    session.add(ow)
    sc = CreatorScorecard(
        creator_id=creator.id, window_label="all", horizon="5d",
        n_calls=1, n_activated=1, n_unique_tickers=1, activation_rate=1.0,
        hit_rate=1.0, hit_rate_lower_ci=0.2, hit_rate_upper_ci=1.0,
        mean_return=0.05, mean_excess_return=0.04,
        evaluator_version="v0.1",
    )
    session.add(sc)
    session.commit()
    return {"creator": creator, "call": call, "doc": doc}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_creators_list(client, seeded):
    r = client.get("/creators")
    assert r.status_code == 200
    names = [c["display_name"] for c in r.json()]
    assert "Test Creator" in names


def test_leaderboard(client, seeded):
    r = client.get("/leaderboard?horizon=5d&window_label=all")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["creator_name"] == "Test Creator"
    assert rows[0]["n_activated"] == 1
    assert rows[0]["mean_excess_return"] == pytest.approx(0.04)


def test_calls_list_with_filters(client, seeded):
    r = client.get("/calls?ticker=NVDA")
    assert r.status_code == 200
    payload = r.json()
    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["call"]["ticker"] == "NVDA"
    assert item["creator_name"] == "Test Creator"
    assert len(item["outcomes"]) == 1


def test_get_call_detail(client, seeded):
    cid = seeded["call"].id
    r = client.get(f"/calls/{cid}")
    assert r.status_code == 200
    body = r.json()
    assert body["call"]["id"] == cid
    assert body["outcomes"][0]["return_pct"] == pytest.approx(0.05)


def test_annotation_crud(client, seeded):
    cid = seeded["call"].id
    # Create
    r = client.post(
        "/annotations",
        json={"entity_type": "call", "entity_id": str(cid), "body": "interesting setup"},
    )
    assert r.status_code == 201
    aid = r.json()["id"]

    # List
    r = client.get(f"/annotations?entity_type=call&entity_id={cid}")
    assert r.status_code == 200
    assert any(a["id"] == aid for a in r.json())

    # Update
    r = client.patch(f"/annotations/{aid}", json={"body": "updated"})
    assert r.status_code == 200
    assert r.json()["body"] == "updated"

    # Delete
    r = client.delete(f"/annotations/{aid}")
    assert r.status_code == 204
    assert client.get(f"/annotations?entity_type=call&entity_id={cid}").json() == []


def test_tag_lifecycle(client, seeded):
    cid = seeded["call"].id
    r = client.post(
        "/tags", json={"entity_type": "call", "entity_id": str(cid), "label": "watch"}
    )
    assert r.status_code == 201

    # Duplicate fails
    r = client.post(
        "/tags", json={"entity_type": "call", "entity_id": str(cid), "label": "watch"}
    )
    assert r.status_code == 409

    r = client.get("/tags/labels")
    assert r.status_code == 200
    assert "watch" in r.json()


def test_watchlist_pin_unpin(client, seeded):
    r = client.post(
        "/watchlist", json={"entity_type": "ticker", "entity_id": "NVDA", "note": "earnings"}
    )
    assert r.status_code == 201
    wid = r.json()["id"]

    r = client.get("/watchlist?entity_type=ticker")
    assert any(w["id"] == wid for w in r.json())

    r = client.delete(f"/watchlist/{wid}")
    assert r.status_code == 204


def test_review_call_status_transitions(client, seeded):
    cid = seeded["call"].id
    r = client.post(
        f"/review/calls/{cid}",
        json={"manual_status": "confirmed", "manual_notes": "looks legit"},
    )
    assert r.status_code == 200
    assert r.json()["manual_status"] == "confirmed"

    r = client.post(f"/review/calls/{cid}", json={"manual_status": "garbage"})
    assert r.status_code == 400


def test_gold_label_create_and_list(client, seeded):
    r = client.post(
        "/gold",
        json={
            "source_key": "test_001",
            "source_text": "NVDA above 920",
            "expected_is_call": True,
            "expected_ticker": "NVDA",
            "expected_direction": "long",
            "expected_entry_type": "trigger_above",
            "expected_entry_price": 920.0,
            "notes": "smoke test",
        },
    )
    assert r.status_code == 201
    gid = r.json()["id"]

    r = client.get("/gold")
    assert any(g["id"] == gid for g in r.json())

    r = client.get(f"/gold/{gid}")
    assert r.status_code == 200
    assert r.json()["expected_ticker"] == "NVDA"
