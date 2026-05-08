"""Tests for the TickerSignal aggregator (per ADR 0006).

These tests use synthetic ExtractedCall + Claim rows in an in-memory DB.
No LLM or network calls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.aggregation.ticker_signals import (
    AGGREGATOR_VERSION,
    CONSENSUS_HIT_RATE_LOWER_CI_THRESHOLD,
    most_recent_close_at_or_before,
    run_aggregation,
)
from app.models import (
    CLAIM_CLASS_FACTUAL,
    CLAIM_CLASS_OPINION,
    CLAIM_CLASS_SPECULATION,
    CLAIM_POLARITY_BEARISH,
    CLAIM_POLARITY_BULLISH,
    CLAIM_STATUS_ACCEPTED,
    CLAIM_STATUS_PENDING_REVIEW,
    SIGNAL_TYPE_CLAIMS_ALL,
    SIGNAL_TYPE_CLAIMS_FACTUAL,
    SIGNAL_TYPE_CREATOR_CONSENSUS,
    SIGNAL_TYPE_TRADE_CALLS,
    SIGNAL_WINDOW_7D,
    Claim,
    Creator,
    CreatorScorecard,
    Document,
    ExtractedCall,
    SourceChannel,
    TickerSignal,
)


# A Tuesday at NYSE close (UTC = 20:00 in standard time, 20:00 in DST too
# because NYSE close 16:00 ET = 20:00 UTC during DST). Pick a date guaranteed
# to be a trading day so window_end alignment is deterministic in tests.
_NOW = datetime(2026, 5, 5, 21, 0, tzinfo=UTC)   # Tue 17:00 ET = post-close


# ---------------------------------------------------------------------------
# Test helpers


def _seed_creator(session, name: str, *, hit_rate_lower_ci: float | None = None) -> Creator:
    creator = Creator(display_name=name)
    session.add(creator)
    session.flush()
    channel = SourceChannel(
        creator_id=creator.id,
        source_type="youtube",
        external_id=f"UC_{name.replace(' ', '_')}",
    )
    session.add(channel)
    session.flush()
    if hit_rate_lower_ci is not None:
        sc = CreatorScorecard(
            creator_id=creator.id,
            window_label="all",
            horizon="5d",
            n_calls=20,
            n_activated=18,
            hit_rate=0.7,
            hit_rate_lower_ci=hit_rate_lower_ci,
            hit_rate_upper_ci=0.85,
            evaluator_version="v0.1",
        )
        session.add(sc)
        session.flush()
    return creator


def _seed_doc(session, creator: Creator, *, posted_at: datetime, ext: str = "vid") -> Document:
    channel = session.query(SourceChannel).filter_by(creator_id=creator.id).first()
    doc = Document(
        source_channel_id=channel.id,
        source_type="youtube",
        external_id=f"{ext}_{creator.id}_{posted_at.isoformat()}",
        title="t",
        posted_at=posted_at,
    )
    session.add(doc)
    session.flush()
    return doc


def _seed_call(
    session,
    doc: Document,
    *,
    ticker: str,
    direction: str = "long",
    status: str = "accepted",
) -> ExtractedCall:
    call = ExtractedCall(
        document_id=doc.id,
        ticker=ticker,
        direction=direction,
        entry_type="market",
        timeframe="swing",
        extractor_version="v0.1",
        final_confidence=0.9,
        status=status,
    )
    session.add(call)
    session.flush()
    return call


def _seed_claim(
    session,
    doc: Document,
    *,
    ticker: str | None,
    claim_type: str = "catalyst",
    polarity: str = CLAIM_POLARITY_BULLISH,
    claim_class: str = CLAIM_CLASS_OPINION,
    status: str = CLAIM_STATUS_ACCEPTED,
    sector: str | None = None,
) -> Claim:
    claim = Claim(
        document_id=doc.id,
        claim_type=claim_type,
        ticker=ticker,
        sector=sector,
        polarity=polarity,
        claim_class=claim_class,
        summary="test claim",
        evidence_quote="test evidence",
        extractor_version="v0.1",
        final_confidence=0.8,
        status=status,
    )
    session.add(claim)
    session.flush()
    return claim


# ---------------------------------------------------------------------------
# Window alignment


def test_most_recent_close_returns_a_past_close():
    out = most_recent_close_at_or_before(_NOW)
    assert out <= _NOW
    # The close must be a regular session close (16:00 ET = 20:00 UTC normal,
    # 20:00 UTC during DST). Don't assert exact tz; just sanity.
    assert out.tzinfo is not None


def test_most_recent_close_is_strict_for_intra_session():
    """During trading hours (e.g., 18:00 UTC), the most-recent close should
    be the PREVIOUS session's close, not today's (which hasn't happened)."""
    intra = datetime(2026, 5, 5, 18, 0, tzinfo=UTC)   # ~14:00 ET, mid-session
    out = most_recent_close_at_or_before(intra)
    # Must be strictly before the intra-session timestamp.
    assert out < intra


# ---------------------------------------------------------------------------
# Aggregation: empty DB


def test_aggregation_with_empty_db_writes_no_rows(monkeypatch, engine, session):
    monkeypatch.setattr("app.aggregation.ticker_signals.session_scope", _scope(session))
    summary = run_aggregation(now=_NOW, window_sizes=[SIGNAL_WINDOW_7D])
    assert summary["total"] == 0
    assert session.query(TickerSignal).count() == 0


# ---------------------------------------------------------------------------
# Aggregation: trade calls only


def test_trade_calls_aggregate_by_ticker(monkeypatch, engine, session):
    creator = _seed_creator(session, "Adam")
    doc1 = _seed_doc(session, creator, posted_at=_NOW - timedelta(days=2), ext="d1")
    doc2 = _seed_doc(session, creator, posted_at=_NOW - timedelta(days=4), ext="d2")
    _seed_call(session, doc1, ticker="NVDA", direction="long")
    _seed_call(session, doc2, ticker="NVDA", direction="long")
    _seed_call(session, doc2, ticker="AAPL", direction="short")
    session.commit()

    monkeypatch.setattr("app.aggregation.ticker_signals.session_scope", _scope(session))
    summary = run_aggregation(now=_NOW, window_sizes=[SIGNAL_WINDOW_7D])

    nvda = (
        session.query(TickerSignal)
        .filter_by(ticker="NVDA", signal_type=SIGNAL_TYPE_TRADE_CALLS)
        .one()
    )
    aapl = (
        session.query(TickerSignal)
        .filter_by(ticker="AAPL", signal_type=SIGNAL_TYPE_TRADE_CALLS)
        .one()
    )
    assert nvda.n_mentions == 2
    assert nvda.n_distinct_creators == 1
    assert nvda.n_documents == 2
    assert nvda.net_polarity == 1.0   # both long → +1
    assert aapl.net_polarity == -1.0  # short → -1
    assert summary["total"] >= 2


def test_pending_review_calls_excluded(monkeypatch, engine, session):
    creator = _seed_creator(session, "Adam")
    doc = _seed_doc(session, creator, posted_at=_NOW - timedelta(days=2))
    _seed_call(session, doc, ticker="NVDA", direction="long", status="accepted")
    _seed_call(session, doc, ticker="NVDA", direction="long", status="pending_review")
    session.commit()

    monkeypatch.setattr("app.aggregation.ticker_signals.session_scope", _scope(session))
    run_aggregation(now=_NOW, window_sizes=[SIGNAL_WINDOW_7D])

    nvda = (
        session.query(TickerSignal)
        .filter_by(ticker="NVDA", signal_type=SIGNAL_TYPE_TRADE_CALLS)
        .one()
    )
    assert nvda.n_mentions == 1   # only the accepted row counted


# ---------------------------------------------------------------------------
# Aggregation: claims


def test_claims_all_aggregate_with_polarity(monkeypatch, engine, session):
    creator = _seed_creator(session, "Adam")
    doc = _seed_doc(session, creator, posted_at=_NOW - timedelta(days=1))
    _seed_claim(session, doc, ticker="NVDA", polarity=CLAIM_POLARITY_BULLISH,
                claim_class=CLAIM_CLASS_OPINION)
    _seed_claim(session, doc, ticker="NVDA", polarity=CLAIM_POLARITY_BEARISH,
                claim_class=CLAIM_CLASS_OPINION)
    _seed_claim(session, doc, ticker="NVDA", polarity=CLAIM_POLARITY_BULLISH,
                claim_class=CLAIM_CLASS_FACTUAL)
    session.commit()

    monkeypatch.setattr("app.aggregation.ticker_signals.session_scope", _scope(session))
    run_aggregation(now=_NOW, window_sizes=[SIGNAL_WINDOW_7D])

    sig = (
        session.query(TickerSignal)
        .filter_by(ticker="NVDA", signal_type=SIGNAL_TYPE_CLAIMS_ALL)
        .one()
    )
    assert sig.n_mentions == 3
    # Net polarity = (1 + -1 + 1) / 3 = 1/3
    assert sig.net_polarity == pytest.approx(1 / 3)
    assert sig.n_factual == 1
    assert sig.n_opinion == 2


def test_claims_factual_only_filters_to_factual_class(monkeypatch, engine, session):
    creator = _seed_creator(session, "Adam")
    doc = _seed_doc(session, creator, posted_at=_NOW - timedelta(days=1))
    _seed_claim(session, doc, ticker="NVDA", claim_class=CLAIM_CLASS_FACTUAL)
    _seed_claim(session, doc, ticker="NVDA", claim_class=CLAIM_CLASS_OPINION)
    _seed_claim(session, doc, ticker="NVDA", claim_class=CLAIM_CLASS_SPECULATION)
    session.commit()

    monkeypatch.setattr("app.aggregation.ticker_signals.session_scope", _scope(session))
    run_aggregation(now=_NOW, window_sizes=[SIGNAL_WINDOW_7D])

    factual = (
        session.query(TickerSignal)
        .filter_by(ticker="NVDA", signal_type=SIGNAL_TYPE_CLAIMS_FACTUAL)
        .one()
    )
    all_sig = (
        session.query(TickerSignal)
        .filter_by(ticker="NVDA", signal_type=SIGNAL_TYPE_CLAIMS_ALL)
        .one()
    )
    assert factual.n_mentions == 1
    assert all_sig.n_mentions == 3


def test_sector_only_claims_excluded_from_per_ticker_signals(monkeypatch, engine, session):
    """Claims with ticker=null (macro/sector) must not appear in per-ticker signals."""
    creator = _seed_creator(session, "Adam")
    doc = _seed_doc(session, creator, posted_at=_NOW - timedelta(days=1))
    _seed_claim(session, doc, ticker=None, sector="semiconductors",
                claim_type="sector_view", polarity=CLAIM_POLARITY_BULLISH)
    session.commit()

    monkeypatch.setattr("app.aggregation.ticker_signals.session_scope", _scope(session))
    summary = run_aggregation(now=_NOW, window_sizes=[SIGNAL_WINDOW_7D])
    assert summary["total"] == 0
    assert session.query(TickerSignal).count() == 0


def test_creator_consensus_filters_to_high_credibility(monkeypatch, engine, session):
    """Per ADR 0006: only creators above the credibility threshold contribute
    to creator_consensus."""
    high = _seed_creator(
        session, "HighCred",
        hit_rate_lower_ci=CONSENSUS_HIT_RATE_LOWER_CI_THRESHOLD + 0.1,
    )
    low = _seed_creator(
        session, "LowCred",
        hit_rate_lower_ci=CONSENSUS_HIT_RATE_LOWER_CI_THRESHOLD - 0.1,
    )
    doc_h = _seed_doc(session, high, posted_at=_NOW - timedelta(days=1), ext="hi")
    doc_l = _seed_doc(session, low, posted_at=_NOW - timedelta(days=1), ext="lo")
    _seed_claim(session, doc_h, ticker="NVDA")
    _seed_claim(session, doc_l, ticker="NVDA")
    session.commit()

    monkeypatch.setattr("app.aggregation.ticker_signals.session_scope", _scope(session))
    run_aggregation(now=_NOW, window_sizes=[SIGNAL_WINDOW_7D])

    consensus = (
        session.query(TickerSignal)
        .filter_by(ticker="NVDA", signal_type=SIGNAL_TYPE_CREATOR_CONSENSUS)
        .one()
    )
    all_sig = (
        session.query(TickerSignal)
        .filter_by(ticker="NVDA", signal_type=SIGNAL_TYPE_CLAIMS_ALL)
        .one()
    )
    assert consensus.n_mentions == 1   # only the high-cred creator
    assert all_sig.n_mentions == 2     # both


# ---------------------------------------------------------------------------
# Idempotency + provenance


def test_rerun_replaces_rows_on_natural_key(monkeypatch, engine, session):
    creator = _seed_creator(session, "Adam")
    doc = _seed_doc(session, creator, posted_at=_NOW - timedelta(days=1))
    _seed_call(session, doc, ticker="NVDA", direction="long")
    session.commit()

    monkeypatch.setattr("app.aggregation.ticker_signals.session_scope", _scope(session))
    run_aggregation(now=_NOW, window_sizes=[SIGNAL_WINDOW_7D])
    n_after_first = session.query(TickerSignal).count()
    run_aggregation(now=_NOW, window_sizes=[SIGNAL_WINDOW_7D])
    n_after_second = session.query(TickerSignal).count()
    assert n_after_first == n_after_second   # no duplicates


def test_aggregator_version_persisted(monkeypatch, engine, session):
    creator = _seed_creator(session, "Adam")
    doc = _seed_doc(session, creator, posted_at=_NOW - timedelta(days=1))
    _seed_call(session, doc, ticker="NVDA", direction="long")
    session.commit()

    monkeypatch.setattr("app.aggregation.ticker_signals.session_scope", _scope(session))
    run_aggregation(now=_NOW, window_sizes=[SIGNAL_WINDOW_7D])

    sig = session.query(TickerSignal).filter_by(ticker="NVDA").first()
    assert sig.aggregator_version == AGGREGATOR_VERSION


# ---------------------------------------------------------------------------
# Helper: rebind session_scope to a test session


def _scope(test_session):
    """Build a context manager that yields the test session in place of a
    fresh production session. Lets the aggregator under test see seed data
    we created via the conftest `session` fixture."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        try:
            yield test_session
            test_session.commit()
        except Exception:
            test_session.rollback()
            raise

    return _cm
