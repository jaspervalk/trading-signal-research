"""Smoke tests for the Claim ORM model (per ADR 0006)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models import (
    CLAIM_CLASS_OPINION,
    CLAIM_POLARITY_BULLISH,
    CLAIM_STATUS_ACCEPTED,
    CLAIM_TYPE_CATALYST,
    Claim,
    Creator,
    Document,
    SourceChannel,
)


def _seed_doc(session) -> Document:
    creator = Creator(display_name="Test Creator")
    session.add(creator)
    session.flush()
    channel = SourceChannel(
        creator_id=creator.id,
        source_type="youtube",
        external_id="UC_TEST",
    )
    session.add(channel)
    session.flush()
    doc = Document(
        source_channel_id=channel.id,
        source_type="youtube",
        external_id="vid_abc",
        title="Test video",
        posted_at=datetime(2026, 5, 1, 14, 0, tzinfo=UTC),
    )
    session.add(doc)
    session.flush()
    return doc


def test_claim_persists_with_required_fields(session):
    doc = _seed_doc(session)
    claim = Claim(
        document_id=doc.id,
        claim_type=CLAIM_TYPE_CATALYST,
        ticker="NVDA",
        polarity=CLAIM_POLARITY_BULLISH,
        claim_class=CLAIM_CLASS_OPINION,
        summary="AI demand drives Q3 growth.",
        evidence_quote="AI demand drives Q3 growth",
        extractor_version="v0.1",
        final_confidence=0.75,
        status=CLAIM_STATUS_ACCEPTED,
    )
    session.add(claim)
    session.commit()

    fetched = session.query(Claim).first()
    assert fetched is not None
    assert fetched.ticker == "NVDA"
    assert fetched.claim_type == "catalyst"
    assert fetched.polarity == "bullish"
    assert fetched.claim_class == "opinion"
    assert fetched.status == "accepted"
    assert "Claim" in repr(fetched)


def test_claim_allows_null_ticker_for_macro(session):
    doc = _seed_doc(session)
    claim = Claim(
        document_id=doc.id,
        claim_type="macro_theme",
        ticker=None,
        sector=None,
        polarity="bearish",
        claim_class="opinion",
        summary="Rates topping pressures growth.",
        evidence_quote="rates topping pressures growth",
        extractor_version="v0.1",
        final_confidence=0.6,
        status="accepted",
    )
    session.add(claim)
    session.commit()
    fetched = session.query(Claim).first()
    assert fetched.ticker is None
    assert fetched.claim_type == "macro_theme"


def test_claim_indexes_present(engine):
    """Sanity-check the documented indexes exist on the claims table."""
    from sqlalchemy import inspect

    insp = inspect(engine)
    idx_names = {ix["name"] for ix in insp.get_indexes("claims")}
    # We declared three named indexes in the model.
    assert "ix_claims_doc_ticker" in idx_names
    assert "ix_claims_status_conf" in idx_names
    assert "ix_claims_type_polarity" in idx_names
