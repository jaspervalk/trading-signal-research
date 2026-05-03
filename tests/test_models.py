"""Smoke tests for the Phase 1a entity chain."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Creator, Document, SourceChannel, TranscriptSegment


def test_creator_unique_display_name(session):  # type: ignore[no-untyped-def]
    session.add(Creator(display_name="Foo"))
    session.commit()

    session.add(Creator(display_name="Foo"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_full_entity_chain(session):  # type: ignore[no-untyped-def]
    creator = Creator(display_name="Test Creator")
    session.add(creator)
    session.flush()

    channel = SourceChannel(
        creator_id=creator.id,
        source_type="youtube",
        external_id="UCabc",
        handle="@test",
        url="https://example.test",
    )
    session.add(channel)
    session.flush()

    doc = Document(
        source_channel_id=channel.id,
        source_type="youtube",
        external_id="vid123",
        title="A video",
        description="...",
        posted_at=datetime(2026, 5, 1, 14, 30, tzinfo=UTC),
        url="https://example.test/v/vid123",
        duration_seconds=600,
    )
    session.add(doc)
    session.flush()

    seg = TranscriptSegment(
        document_id=doc.id,
        start_seconds=12.0,
        end_seconds=18.5,
        text="NVDA looks strong above 920",
    )
    session.add(seg)
    session.commit()

    fetched = session.get(Document, doc.id)
    assert fetched is not None
    assert fetched.source_channel.creator.display_name == "Test Creator"
    assert len(fetched.segments) == 1
    assert fetched.segments[0].text.startswith("NVDA")


def test_source_channel_uniqueness(session):  # type: ignore[no-untyped-def]
    c = Creator(display_name="X")
    session.add(c)
    session.flush()

    session.add(SourceChannel(creator_id=c.id, source_type="youtube", external_id="UCdup"))
    session.commit()

    session.add(SourceChannel(creator_id=c.id, source_type="youtube", external_id="UCdup"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_document_uniqueness_across_source_pair(session):  # type: ignore[no-untyped-def]
    c = Creator(display_name="Y")
    session.add(c)
    session.flush()
    ch = SourceChannel(creator_id=c.id, source_type="youtube", external_id="UCy")
    session.add(ch)
    session.flush()

    posted = datetime(2026, 5, 1, tzinfo=UTC)
    session.add(
        Document(
            source_channel_id=ch.id,
            source_type="youtube",
            external_id="dupvid",
            posted_at=posted,
        )
    )
    session.commit()

    session.add(
        Document(
            source_channel_id=ch.id,
            source_type="youtube",
            external_id="dupvid",
            posted_at=posted,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
