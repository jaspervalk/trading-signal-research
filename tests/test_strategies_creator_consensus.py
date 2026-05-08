"""Unit tests for CreatorConsensus."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.strategies.base import StrategyContext
from app.strategies.creator_consensus import CreatorConsensus


class _StubReader:
    def daily_bars(self, *a, **k):
        import pandas as pd
        return pd.DataFrame()

    def benchmark_bars(self, *a, **k):
        import pandas as pd
        return pd.DataFrame()


def _call(
    *,
    ticker: str,
    direction: str,
    creator_id: int,
    days_ago: int = 1,
    call_id: int,
) -> dict[str, Any]:
    base = datetime(2025, 6, 1, tzinfo=UTC)
    return {
        "id": call_id,
        "ticker": ticker,
        "direction": direction,
        "entry_price": None,
        "target_price": None,
        "stop_price": None,
        "final_confidence": 0.7,
        "posted_at": base - timedelta(days=days_ago),
        "creator_id": creator_id,
    }


def _ctx(
    *,
    calls: list[dict[str, Any]],
    universe: list[str],
    scorecards: dict[int, dict[str, Any]],
) -> StrategyContext:
    return StrategyContext(
        as_of=datetime(2025, 6, 1, tzinfo=UTC),
        universe=universe,
        ticker_signals={},
        creator_scorecards=scorecards,
        recent_calls=calls,
        recent_claims=[],
        market=_StubReader(),
    )


def _calibrated(creator_id: int, *, lci: float = 0.6) -> tuple[int, dict]:
    return creator_id, {"creator_id": creator_id, "hit_rate_lower_ci": lci}


def test_no_decisions_when_no_calibrated_creators():
    strat = CreatorConsensus(min_calibrated_creators=2)
    calls = [_call(ticker="AAPL", direction="long", creator_id=1, call_id=1)]
    ctx = _ctx(calls=calls, universe=["AAPL"], scorecards={})
    assert strat.evaluate(ctx) == []


def test_requires_min_calibrated_creators_agreeing():
    strat = CreatorConsensus(min_calibrated_creators=2, credibility_threshold=0.55)
    cid1, sc1 = _calibrated(1)
    cid2, sc2 = _calibrated(2)
    cid3, sc3 = _calibrated(3, lci=0.50)  # not calibrated (below threshold)
    calls = [
        _call(ticker="AAPL", direction="long", creator_id=cid1, call_id=1),
        _call(ticker="AAPL", direction="long", creator_id=cid3, call_id=2),
    ]
    ctx = _ctx(calls=calls, universe=["AAPL"], scorecards={cid1: sc1, cid2: sc2, cid3: sc3})
    # Only one calibrated creator (cid1) issued a call → no consensus.
    assert strat.evaluate(ctx) == []


def test_emits_long_consensus():
    strat = CreatorConsensus(min_calibrated_creators=2, credibility_threshold=0.55)
    cid1, sc1 = _calibrated(1)
    cid2, sc2 = _calibrated(2)
    calls = [
        _call(ticker="AAPL", direction="long", creator_id=cid1, call_id=1),
        _call(ticker="AAPL", direction="long", creator_id=cid2, call_id=2),
    ]
    ctx = _ctx(calls=calls, universe=["AAPL"], scorecards={cid1: sc1, cid2: sc2})
    out = strat.evaluate(ctx)
    assert len(out) == 1
    assert out[0].ticker == "AAPL"
    assert out[0].direction == "long"
    assert out[0].score == 2.0


def test_emits_short_consensus():
    strat = CreatorConsensus(min_calibrated_creators=2, credibility_threshold=0.55)
    cid1, sc1 = _calibrated(1)
    cid2, sc2 = _calibrated(2)
    calls = [
        _call(ticker="AAPL", direction="short", creator_id=cid1, call_id=1),
        _call(ticker="AAPL", direction="short", creator_id=cid2, call_id=2),
    ]
    ctx = _ctx(calls=calls, universe=["AAPL"], scorecards={cid1: sc1, cid2: sc2})
    out = strat.evaluate(ctx)
    assert len(out) == 1
    assert out[0].direction == "short"


def test_drops_contradicted_consensus():
    """If both directions have ≥ min calibrated agreeing creators, drop entirely."""
    strat = CreatorConsensus(min_calibrated_creators=2, credibility_threshold=0.55)
    cid1, sc1 = _calibrated(1)
    cid2, sc2 = _calibrated(2)
    cid3, sc3 = _calibrated(3)
    cid4, sc4 = _calibrated(4)
    calls = [
        _call(ticker="AAPL", direction="long", creator_id=cid1, call_id=1),
        _call(ticker="AAPL", direction="long", creator_id=cid2, call_id=2),
        _call(ticker="AAPL", direction="short", creator_id=cid3, call_id=3),
        _call(ticker="AAPL", direction="short", creator_id=cid4, call_id=4),
    ]
    ctx = _ctx(
        calls=calls,
        universe=["AAPL"],
        scorecards={cid1: sc1, cid2: sc2, cid3: sc3, cid4: sc4},
    )
    assert strat.evaluate(ctx) == []


def test_drops_non_universe_tickers():
    strat = CreatorConsensus(min_calibrated_creators=2)
    cid1, sc1 = _calibrated(1)
    cid2, sc2 = _calibrated(2)
    calls = [
        _call(ticker="ETSY", direction="long", creator_id=cid1, call_id=1),
        _call(ticker="ETSY", direction="long", creator_id=cid2, call_id=2),
    ]
    ctx = _ctx(calls=calls, universe=["AAPL"], scorecards={cid1: sc1, cid2: sc2})
    assert strat.evaluate(ctx) == []


def test_invalid_credibility_threshold_raises():
    with pytest.raises(ValueError):
        CreatorConsensus(credibility_threshold=-0.1)
    with pytest.raises(ValueError):
        CreatorConsensus(credibility_threshold=1.1)
