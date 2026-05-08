"""Tests for the ticker-first dashboard endpoints (per docs/dashboard-ticker-page-ia.md)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.models import (
    CALL_STATUS_ACCEPTED,
    CLAIM_CLASS_FACTUAL,
    CLAIM_CLASS_OPINION,
    CLAIM_POLARITY_BEARISH,
    CLAIM_POLARITY_BULLISH,
    CLAIM_STATUS_ACCEPTED,
    CLAIM_TYPE_CATALYST,
    CLAIM_TYPE_RISK,
    Claim,
    Creator,
    CreatorScorecard,
    DIRECTION_LONG,
    Document,
    ENTRY_MARKET,
    ExtractedCall,
    SIGNAL_TYPE_CLAIMS_ALL,
    SIGNAL_TYPE_CLAIMS_FACTUAL,
    SIGNAL_TYPE_TRADE_CALLS,
    SIGNAL_WINDOW_7D,
    SIGNAL_WINDOW_30D,
    SourceChannel,
    TickerSignal,
)


@pytest.fixture
def client(engine, session):
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
def seeded_for_signals(client, session):
    """Two creators, one calibrated and one not, with calls + claims on NVDA + AAPL."""
    high = Creator(display_name="HighCred")
    low = Creator(display_name="LowCred")
    session.add_all([high, low])
    session.flush()

    # high creator has a scorecard; low creator does not (uncalibrated).
    sc = CreatorScorecard(
        creator_id=high.id, window_label="all", horizon="5d",
        n_calls=20, n_activated=18, n_unique_tickers=5,
        hit_rate=0.7, hit_rate_lower_ci=0.65, hit_rate_upper_ci=0.85,
        evaluator_version="v0.1",
    )
    session.add(sc)

    h_chan = SourceChannel(creator_id=high.id, source_type="youtube", external_id="UCh")
    l_chan = SourceChannel(creator_id=low.id, source_type="youtube", external_id="UCl")
    session.add_all([h_chan, l_chan])
    session.flush()

    base = datetime(2026, 5, 1, tzinfo=UTC)
    h_doc = Document(
        source_channel_id=h_chan.id, source_type="youtube", external_id="vh",
        title="High vid", posted_at=base, url="https://x.test/h", duration_seconds=300,
    )
    l_doc = Document(
        source_channel_id=l_chan.id, source_type="youtube", external_id="vl",
        title="Low vid", posted_at=base - timedelta(days=2), url="https://x.test/l",
        duration_seconds=300,
    )
    session.add_all([h_doc, l_doc])
    session.flush()

    # NVDA call from HighCred (long), accepted.
    call = ExtractedCall(
        document_id=h_doc.id, ticker="NVDA", direction=DIRECTION_LONG,
        entry_type=ENTRY_MARKET, timeframe="swing",
        evidence_quote="NVDA looks strong",
        extractor_version="v0.1", final_confidence=0.9,
        status=CALL_STATUS_ACCEPTED,
    )
    session.add(call)

    # NVDA bullish opinion claim from HighCred.
    c_high = Claim(
        document_id=h_doc.id, claim_type=CLAIM_TYPE_CATALYST, ticker="NVDA",
        polarity=CLAIM_POLARITY_BULLISH, claim_class=CLAIM_CLASS_OPINION,
        summary="AI demand strong", evidence_quote="AI demand is strong",
        extractor_version="v0.1", final_confidence=0.8,
        status=CLAIM_STATUS_ACCEPTED,
    )
    # NVDA bearish factual claim from LowCred.
    c_low = Claim(
        document_id=l_doc.id, claim_type=CLAIM_TYPE_RISK, ticker="NVDA",
        polarity=CLAIM_POLARITY_BEARISH, claim_class=CLAIM_CLASS_FACTUAL,
        summary="Margin compression", evidence_quote="margin compression",
        extractor_version="v0.1", final_confidence=0.7,
        status=CLAIM_STATUS_ACCEPTED,
    )
    # AAPL claim — separate ticker, should NOT appear in NVDA queries.
    c_aapl = Claim(
        document_id=h_doc.id, claim_type=CLAIM_TYPE_CATALYST, ticker="AAPL",
        polarity=CLAIM_POLARITY_BULLISH, claim_class=CLAIM_CLASS_OPINION,
        summary="AAPL services growth", evidence_quote="AAPL services growing",
        extractor_version="v0.1", final_confidence=0.8,
        status=CLAIM_STATUS_ACCEPTED,
    )
    # Pending-review claim — should NOT appear by default (status filter).
    c_pending = Claim(
        document_id=h_doc.id, claim_type=CLAIM_TYPE_CATALYST, ticker="NVDA",
        polarity=CLAIM_POLARITY_BULLISH, claim_class=CLAIM_CLASS_OPINION,
        summary="Pending claim", evidence_quote="pending",
        extractor_version="v0.1", final_confidence=0.4,
        status="pending_review",
    )
    session.add_all([c_high, c_low, c_aapl, c_pending])

    # TickerSignal aggregates — what aggregate-signals would have produced.
    sig_7d = TickerSignal(
        ticker="NVDA",
        window_end=base,
        window_size=SIGNAL_WINDOW_7D,
        signal_type=SIGNAL_TYPE_CLAIMS_ALL,
        n_mentions=2, n_distinct_creators=2, n_documents=2,
        net_polarity=0.0,            # +1 high, -1 low → 0
        credibility_weighted_polarity=0.2,
        n_factual=1, n_opinion=1, n_speculation=0, n_hype=0,
        aggregator_version="v0.1",
    )
    sig_30d = TickerSignal(
        ticker="NVDA",
        window_end=base,
        window_size=SIGNAL_WINDOW_30D,
        signal_type=SIGNAL_TYPE_TRADE_CALLS,
        n_mentions=1, n_distinct_creators=1, n_documents=1,
        net_polarity=1.0, credibility_weighted_polarity=1.0,
        aggregator_version="v0.1",
    )
    sig_factual = TickerSignal(
        ticker="NVDA",
        window_end=base,
        window_size=SIGNAL_WINDOW_30D,
        signal_type=SIGNAL_TYPE_CLAIMS_FACTUAL,
        n_mentions=1, n_distinct_creators=1, n_documents=1,
        net_polarity=-1.0, credibility_weighted_polarity=-1.0,
        n_factual=1,
        aggregator_version="v0.1",
    )
    session.add_all([sig_7d, sig_30d, sig_factual])
    session.commit()


# ---------------------------------------------------------------------------
# /tickers/{ticker}/signals


def test_signals_returns_all_rows_for_ticker(client, seeded_for_signals):
    r = client.get("/tickers/NVDA/signals")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 3
    types = {(row["window_size"], row["signal_type"]) for row in rows}
    assert (SIGNAL_WINDOW_7D, SIGNAL_TYPE_CLAIMS_ALL) in types
    assert (SIGNAL_WINDOW_30D, SIGNAL_TYPE_TRADE_CALLS) in types
    assert (SIGNAL_WINDOW_30D, SIGNAL_TYPE_CLAIMS_FACTUAL) in types


def test_signals_filters_by_signal_type(client, seeded_for_signals):
    r = client.get(f"/tickers/NVDA/signals?signal_type={SIGNAL_TYPE_TRADE_CALLS}")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["signal_type"] == SIGNAL_TYPE_TRADE_CALLS


def test_signals_filters_by_window_size(client, seeded_for_signals):
    r = client.get(f"/tickers/NVDA/signals?window_size={SIGNAL_WINDOW_7D}")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["window_size"] == SIGNAL_WINDOW_7D


def test_signals_unknown_ticker_returns_empty_list(client, seeded_for_signals):
    r = client.get("/tickers/ZZZZ/signals")
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# /tickers/{ticker}/claims


def test_claims_returns_only_this_ticker(client, seeded_for_signals):
    r = client.get("/tickers/NVDA/claims")
    assert r.status_code == 200
    rows = r.json()
    # NVDA has 3 claims total: 2 accepted (high + low) + 1 pending.
    # Default status='accepted' → should return 2.
    assert len(rows) == 2
    for row in rows:
        assert row["ticker"] == "NVDA"
        assert row["status"] == "accepted"


def test_claims_excludes_pending_review_by_default(client, seeded_for_signals):
    r = client.get("/tickers/NVDA/claims")
    rows = r.json()
    summaries = [row["summary"] for row in rows]
    assert "Pending claim" not in summaries


def test_claims_filter_by_polarity(client, seeded_for_signals):
    r = client.get("/tickers/NVDA/claims?polarity=bearish")
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["polarity"] == "bearish"
    assert rows[0]["claim_class"] == "factual"


def test_claims_filter_by_claim_class(client, seeded_for_signals):
    r = client.get("/tickers/NVDA/claims?claim_class=factual")
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["claim_class"] == "factual"


def test_claims_includes_creator_and_document_context(client, seeded_for_signals):
    r = client.get("/tickers/NVDA/claims")
    rows = r.json()
    row = next(r for r in rows if r["polarity"] == "bullish")
    assert row["creator_name"] == "HighCred"
    assert row["document_title"] == "High vid"
    assert row["document_url"] == "https://x.test/h"


def test_claims_aapl_query_does_not_leak_nvda(client, seeded_for_signals):
    r = client.get("/tickers/AAPL/claims")
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"


# ---------------------------------------------------------------------------
# /tickers/{ticker}/coverage


def test_coverage_returns_per_creator_rollup(client, seeded_for_signals):
    r = client.get("/tickers/NVDA/coverage")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2

    high = next(r for r in rows if r["creator_name"] == "HighCred")
    low = next(r for r in rows if r["creator_name"] == "LowCred")

    assert high["n_calls"] == 1
    assert high["n_claims"] == 1
    assert high["is_calibrated"] is True
    assert high["hit_rate_lower_ci"] == pytest.approx(0.65)
    # HighCred polarity: long call (+1) + bullish claim (+1) → mean = 1.0
    assert high["net_polarity_on_this_ticker"] == pytest.approx(1.0)

    assert low["n_calls"] == 0
    assert low["n_claims"] == 1
    assert low["is_calibrated"] is False
    assert low["hit_rate_lower_ci"] is None
    # LowCred polarity: bearish claim (-1) → mean = -1.0
    assert low["net_polarity_on_this_ticker"] == pytest.approx(-1.0)


def test_coverage_calibrated_creators_sort_first(client, seeded_for_signals):
    r = client.get("/tickers/NVDA/coverage")
    rows = r.json()
    # HighCred is calibrated, LowCred is not — HighCred must come first.
    assert rows[0]["creator_name"] == "HighCred"
    assert rows[1]["creator_name"] == "LowCred"


def test_coverage_excludes_pending_review_claims(client, seeded_for_signals):
    """The pending-review NVDA claim from HighCred must not bump n_claims."""
    r = client.get("/tickers/NVDA/coverage")
    rows = r.json()
    high = next(r for r in rows if r["creator_name"] == "HighCred")
    # Only 1 accepted claim (the bullish catalyst), not 2 (pending excluded).
    assert high["n_claims"] == 1


def test_coverage_unknown_ticker_returns_empty(client, seeded_for_signals):
    r = client.get("/tickers/ZZZZ/coverage")
    assert r.status_code == 200
    assert r.json() == []
