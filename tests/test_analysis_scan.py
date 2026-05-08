"""Unit tests for the multi-ticker scan + ranking + snapshot persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.analysis.scan import ScanResult, _sort_key, scan_tickers
from app.analysis.schema import (
    ActionSignal,
    DecisionSupportStatus,
    EntryZoneCandidate,
    IdentityCoverage,
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
    SetupClassification,
    StyleFitPanel,
    TickerResearchView,
    TranscriptContext,
)
from app.models import Base, ResearchSnapshot


@pytest.fixture
def in_memory_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _stub_view(
    ticker: str,
    *,
    status: str = "research_candidate",
    status_conf: str = "high",
    setup: str = "breakout_candidate",
    setup_conf: str = "medium",
    primary_style: str | None = "base_breakout",
    last_close: float = 100.0,
    return_5d: float | None = 0.03,
    rsi: float | None = 65.0,
    n_claims: int = 2,
) -> TickerResearchView:
    return TickerResearchView(
        ticker=ticker,
        as_of=datetime(2026, 5, 7, tzinfo=UTC),
        identity=IdentityCoverage(
            ticker=ticker,
            in_universe=True,
            has_transcript_signals=False,
            has_extracted_calls=False,
            has_extracted_claims=n_claims > 0,
            n_bars_loaded=300,
            enough_history_for_full_analysis=True,
        ),
        market=MarketSnapshotPanel(
            as_of=datetime(2026, 5, 7, tzinfo=UTC),
            last_close=last_close,
            return_5d=return_5d,
            return_21d=0.05,
        ),
        indicators=IndicatorPanel(
            ma_alignment="bullish_stack",
            rsi_14=rsi,
            atr_14_pct=0.02,
        ),
        levels=LevelsPanel(),
        setup=SetupClassification(
            setup_type=setup,
            confidence=setup_conf,
        ),
        style_fit=StyleFitPanel(items=[], primary_style=primary_style),
        status=DecisionSupportStatus(
            status=status,
            confidence=status_conf,
            summary=f"stub for {ticker}",
        ),
        action=ActionSignal(
            label="HOLD",
            confidence=status_conf,
            derivation="stub",
        ),
        entry_zone=EntryZoneCandidate(available=False),
        transcript=TranscriptContext(
            has_data=n_claims > 0,
            n_signals=0,
            n_calls=0,
            n_claims=n_claims,
            confirms_or_contradicts="confirms" if n_claims > 0 else "unknown",
            summary="stub",
        ),
    )


# ---------------------------------------------------------------------------
# Sort-key ranking


def test_sort_key_research_candidate_high_beats_research_candidate_medium():
    a = _row(_stub_view("AAA", status_conf="high"))
    b = _row(_stub_view("BBB", status_conf="medium"))
    assert _sort_key(a) < _sort_key(b)


def test_sort_key_research_candidate_beats_watch_beats_skip():
    rc = _row(_stub_view("RC", status="research_candidate", status_conf="medium"))
    w = _row(_stub_view("W", status="watch", status_conf="medium"))
    s = _row(_stub_view("S", status="skip_for_now", status_conf="medium"))
    assert _sort_key(rc) < _sort_key(w) < _sort_key(s)


def test_sort_key_break_ties_alphabetically():
    a = _row(_stub_view("AAA"))
    b = _row(_stub_view("BBB"))
    assert _sort_key(a) < _sort_key(b)


# ---------------------------------------------------------------------------
# scan_tickers integration (mocked research view)


def test_scan_tickers_dedupes_input(in_memory_session: Session):
    with patch("app.analysis.scan.build_ticker_research_view") as mock_build:
        mock_build.return_value = _stub_view("AAPL")
        result = scan_tickers(
            ["AAPL", "aapl", "AAPL"],
            session=in_memory_session,
            persist=False,
        )
    assert result.tickers_requested == 1
    assert len(result.rows) == 1
    assert mock_build.call_count == 1


def test_scan_tickers_persists_when_persist_true(in_memory_session: Session):
    with patch("app.analysis.scan.build_ticker_research_view") as mock_build:
        mock_build.return_value = _stub_view("AAPL")
        scan_tickers(["AAPL"], session=in_memory_session, persist=True)
    in_memory_session.commit()
    rows = list(in_memory_session.scalars(select(ResearchSnapshot)).all())
    assert len(rows) == 1
    assert rows[0].ticker == "AAPL"
    assert rows[0].status == "research_candidate"


def test_scan_tickers_does_not_persist_when_persist_false(in_memory_session: Session):
    with patch("app.analysis.scan.build_ticker_research_view") as mock_build:
        mock_build.return_value = _stub_view("AAPL")
        scan_tickers(["AAPL"], session=in_memory_session, persist=False)
    in_memory_session.commit()
    rows = list(in_memory_session.scalars(select(ResearchSnapshot)).all())
    assert rows == []


def test_scan_tickers_records_error_row_on_failure(in_memory_session: Session):
    with patch("app.analysis.scan.build_ticker_research_view") as mock_build:
        mock_build.side_effect = RuntimeError("yfinance exploded")
        result = scan_tickers(
            ["AAPL"], session=in_memory_session, persist=False
        )
    assert result.n_errors == 1
    assert result.rows[0].error == "yfinance exploded"
    assert result.rows[0].status == "insufficient_data"


def test_scan_tickers_summary_counts_match(in_memory_session: Session):
    views = {
        "AAA": _stub_view("AAA", status="research_candidate"),
        "BBB": _stub_view("BBB", status="watch"),
        "CCC": _stub_view("CCC", status="watch"),
        "DDD": _stub_view("DDD", status="skip_for_now"),
    }
    with patch("app.analysis.scan.build_ticker_research_view") as mock_build:
        mock_build.side_effect = lambda t, **kw: views[t]
        result = scan_tickers(
            list(views), session=in_memory_session, persist=False
        )
    assert result.n_research_candidates == 1
    assert result.n_watch == 2
    assert result.n_skip == 1
    # First row should be the research_candidate.
    assert result.rows[0].ticker == "AAA"


# ---------------------------------------------------------------------------
# Helpers


def _row(view):
    """Convert a stub view to a ScanRow via the same shaping function the scanner uses."""
    from app.analysis.scan import _row_from_view
    return _row_from_view(view)
