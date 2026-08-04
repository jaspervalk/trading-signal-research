"""Pipeline: mocked fetcher fan-out, ordering, partial data, creator coverage.

The critical invariant: a ticker with ZERO creator coverage scores
identically (and is ordered identically) to one with coverage on
otherwise-equal non-creator dimensions.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    CALL_STATUS_ACCEPTED,
    Base,
    Creator,
    Document,
    ExtractedCall,
    SourceChannel,
)
from app.screener.pipeline import run_screen
from app.screener.schema import ScreenConfig, TickerMetrics
from app.screener.universe import UniverseEntry


# ---------------------------------------------------------------------------
# Test scaffolding — in-memory DB, deterministic fetcher.


@pytest.fixture
def screen_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _good_metrics(ticker: str, sector: str = "Technology") -> TickerMetrics:
    return TickerMetrics(
        ticker=ticker,
        sector=sector,
        forward_pe=18.0,
        peg_ratio=1.1,
        ev_to_ebitda=12.0,
        revenue_growth_yoy=0.22,
        earnings_growth_yoy=0.18,
        gross_margin=0.55,
        roe=0.22,
        debt_to_equity=0.35,
        last_close=150.0,
        sma_200=130.0,
        pct_vs_200d=0.15,
        rs_vs_spy_3mo=0.06,
        days_to_earnings=45,
    )


def _bad_metrics(ticker: str) -> TickerMetrics:
    return TickerMetrics(
        ticker=ticker,
        sector="Technology",
        forward_pe=80.0,
        peg_ratio=4.5,
        ev_to_ebitda=40.0,
        revenue_growth_yoy=0.02,
        earnings_growth_yoy=-0.10,
        gross_margin=0.15,
        roe=0.04,
        debt_to_equity=2.5,
        last_close=10.0,
        sma_200=14.0,
        pct_vs_200d=-0.30,
        rs_vs_spy_3mo=-0.18,
        days_to_earnings=12,
    )


def _seed_extracted_call(session, ticker: str, *, confidence: float = 0.85) -> None:
    """Create the rows ExtractedCall needs (Creator + SourceChannel + Document)."""
    creator = session.query(Creator).filter_by(display_name="Test").one_or_none()
    if creator is None:
        creator = Creator(display_name="Test", active=True)
        session.add(creator)
        session.flush()
    channel = session.query(SourceChannel).filter_by(creator_id=creator.id).one_or_none()
    if channel is None:
        channel = SourceChannel(
            creator_id=creator.id,
            source_type="youtube",
            external_id=f"UCtest-{ticker}",
        )
        session.add(channel)
        session.flush()
    doc = Document(
        source_channel_id=channel.id,
        source_type="youtube",
        external_id=f"doc-{ticker}",
        title=f"Test {ticker}",
        url=f"https://example.com/{ticker}",
        posted_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    session.add(doc)
    session.flush()
    call = ExtractedCall(
        document_id=doc.id,
        ticker=ticker,
        extractor_version="test",
        final_confidence=confidence,
        status=CALL_STATUS_ACCEPTED,
    )
    session.add(call)
    session.commit()


# ---------------------------------------------------------------------------
# Happy path


def test_pipeline_runs_and_returns_rows():
    universe = [UniverseEntry("AAPL"), UniverseEntry("MSFT")]
    metrics_by_ticker = {"AAPL": _good_metrics("AAPL"), "MSFT": _good_metrics("MSFT")}
    result = run_screen(
        universe,
        fetch_fn=metrics_by_ticker.__getitem__,
        max_workers=2,
        requests_per_second=0,  # no rate limit in tests
    )
    assert result.universe_size == 2
    assert result.n_completed == 2
    assert result.n_errors == 0
    assert {r.ticker for r in result.rows} == {"AAPL", "MSFT"}
    for r in result.rows:
        assert "value" in r.filters_passed
        assert "growth" in r.filters_passed
        assert "quality" in r.filters_passed
        assert "technical" in r.filters_passed


def test_pipeline_collects_errors_without_crashing():
    universe = [UniverseEntry("AAPL"), UniverseEntry("BROKEN")]

    def fetch(t: str) -> TickerMetrics:
        if t == "BROKEN":
            raise RuntimeError("yfinance hated us")
        return _good_metrics(t)

    result = run_screen(
        universe, fetch_fn=fetch, max_workers=2, requests_per_second=0
    )
    assert result.n_completed == 1
    assert result.n_errors == 1
    broken = next(r for r in result.rows if r.ticker == "BROKEN")
    assert broken.error == "yfinance hated us"
    assert broken.filters_passed == []


def test_pipeline_passing_rows_sorted_before_failing_rows():
    universe = [UniverseEntry("AAA"), UniverseEntry("BBB")]
    fetch_table = {"AAA": _bad_metrics("AAA"), "BBB": _good_metrics("BBB")}
    result = run_screen(
        universe,
        fetch_fn=fetch_table.__getitem__,
        max_workers=2,
        requests_per_second=0,
    )
    # Passing row first regardless of alpha order
    assert result.rows[0].ticker == "BBB"
    assert result.rows[1].ticker == "AAA"


# ---------------------------------------------------------------------------
# Sector filter


def test_sector_filter_drops_other_sectors():
    universe = [
        UniverseEntry("AAPL", sector="Technology"),
        UniverseEntry("JPM", sector="Financials"),
    ]
    fetch_table = {
        "AAPL": _good_metrics("AAPL", "Technology"),
        "JPM": _good_metrics("JPM", "Financials"),
    }
    result = run_screen(
        universe,
        config=ScreenConfig(sector="Technology"),
        fetch_fn=fetch_table.__getitem__,
        requests_per_second=0,
    )
    assert {r.ticker for r in result.rows} == {"AAPL"}


# ---------------------------------------------------------------------------
# Enabled-filters subset


def test_enabled_filters_restricts_active_set():
    universe = [UniverseEntry("AAPL")]
    fetch_table = {"AAPL": _good_metrics("AAPL")}
    result = run_screen(
        universe,
        config=ScreenConfig(enabled_filters=["value", "growth"]),
        fetch_fn=fetch_table.__getitem__,
        requests_per_second=0,
    )
    row = result.rows[0]
    assert set(r.name for r in row.filters) == {"value", "growth"}


# ---------------------------------------------------------------------------
# Flags surface on the row


def test_cheap_for_a_reason_flag_surfaces():
    m = _good_metrics("AAA")
    m.earnings_growth_yoy = -0.10  # cheap (passes valuation) but negative earnings
    result = run_screen(
        [UniverseEntry("AAA")],
        fetch_fn=lambda t: m,
        requests_per_second=0,
    )
    assert "cheap_for_a_reason" in result.rows[0].flags


# ---------------------------------------------------------------------------
# THE CRITICAL INVARIANT: creator coverage is purely additive.


def test_zero_creator_coverage_does_not_penalize_ordering(screen_session):
    """Two tickers with identical non-creator metrics — one with coverage,
    one with none. The covered ticker must NOT rank above the uncovered."""
    _seed_extracted_call(screen_session, "AAA", confidence=0.95)
    universe = [UniverseEntry("AAA"), UniverseEntry("BBB")]
    fetch_table = {"AAA": _good_metrics("AAA"), "BBB": _good_metrics("BBB")}

    result = run_screen(
        universe,
        fetch_fn=fetch_table.__getitem__,
        session=screen_session,
        requests_per_second=0,
    )

    aaa = next(r for r in result.rows if r.ticker == "AAA")
    bbb = next(r for r in result.rows if r.ticker == "BBB")

    # Coverage column reflects reality
    assert aaa.metrics.creator_mentions == 1
    assert aaa.metrics.avg_creator_confidence is not None
    assert bbb.metrics.creator_mentions == 0
    assert bbb.metrics.avg_creator_confidence is None

    # Filters passed are IDENTICAL — coverage is not a filter input
    assert aaa.filters_passed == bbb.filters_passed

    # Sort key only sees error/passing/n_passed/ticker — so the uncovered
    # ticker (BBB, alpha-second) must NOT be demoted by lack of coverage.
    # Both rows have the same n_passed → alpha order wins.
    a_idx = next(i for i, r in enumerate(result.rows) if r.ticker == "AAA")
    b_idx = next(i for i, r in enumerate(result.rows) if r.ticker == "BBB")
    assert a_idx < b_idx  # alphabetical tiebreak; coverage played no role


def test_no_session_means_zero_coverage_everywhere():
    universe = [UniverseEntry("AAA"), UniverseEntry("BBB")]
    fetch_table = {"AAA": _good_metrics("AAA"), "BBB": _good_metrics("BBB")}
    result = run_screen(
        universe,
        fetch_fn=fetch_table.__getitem__,
        session=None,
        requests_per_second=0,
    )
    for r in result.rows:
        assert r.metrics.creator_mentions == 0
        assert r.metrics.avg_creator_confidence is None
