"""Unit tests for the MentionMomentum strategy.

Stub the StrategyContext with synthetic claims; assert the strategy returns
the right number of decisions in the right ticker order. No real market
data, no DB access.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.strategies.base import StrategyContext
from app.strategies.mention_momentum import MentionMomentum


class _StubReader:
    def daily_bars(self, ticker: str, *, lookback_days: int):
        import pandas as pd
        return pd.DataFrame()

    def benchmark_bars(self, *, lookback_days: int):
        import pandas as pd
        return pd.DataFrame()


def _claim(*, ticker: str, polarity: str, days_ago: int, creator_id: int, claim_id: int) -> dict[str, Any]:
    base = datetime(2025, 6, 1, tzinfo=UTC)
    return {
        "id": claim_id,
        "ticker": ticker,
        "polarity": polarity,
        "claim_class": "opinion",
        "claim_type": "thesis",
        "final_confidence": 0.8,
        "posted_at": base - timedelta(days=days_ago),
        "creator_id": creator_id,
    }


def _ctx(*, claims: list[dict[str, Any]], universe: list[str]) -> StrategyContext:
    return StrategyContext(
        as_of=datetime(2025, 6, 1, tzinfo=UTC),
        universe=universe,
        ticker_signals={},
        creator_scorecards={},
        recent_calls=[],
        recent_claims=claims,
        market=_StubReader(),
    )


def test_returns_no_decisions_when_no_recent_claims():
    strat = MentionMomentum()
    ctx = _ctx(claims=[], universe=["AAPL", "NVDA"])
    assert strat.evaluate(ctx) == []


def test_filters_to_lookback_window():
    strat = MentionMomentum(lookback_days=7)
    claims = [
        _claim(ticker="AAPL", polarity="bullish", days_ago=20, creator_id=1, claim_id=1),
        _claim(ticker="AAPL", polarity="bullish", days_ago=2, creator_id=1, claim_id=2),
    ]
    ctx = _ctx(claims=claims, universe=["AAPL"])
    out = strat.evaluate(ctx)
    # Only the second claim is inside the 7d window.
    assert len(out) == 1
    assert out[0].ticker == "AAPL"
    # Evidence carries only the in-window claim id.
    assert [e.id for e in out[0].evidence] == [2]


def test_drops_tickers_outside_universe():
    strat = MentionMomentum()
    claims = [
        _claim(ticker="AAPL", polarity="bullish", days_ago=2, creator_id=1, claim_id=1),
        _claim(ticker="ETSY", polarity="bullish", days_ago=2, creator_id=1, claim_id=2),
    ]
    ctx = _ctx(claims=claims, universe=["AAPL"])  # ETSY not in universe
    out = strat.evaluate(ctx)
    assert len(out) == 1
    assert out[0].ticker == "AAPL"


def test_long_only_v1_skips_bearish():
    strat = MentionMomentum()
    claims = [
        _claim(ticker="AAPL", polarity="bearish", days_ago=2, creator_id=1, claim_id=1),
    ]
    ctx = _ctx(claims=claims, universe=["AAPL"])
    out = strat.evaluate(ctx)
    assert out == []


def test_picks_top_decile_by_score():
    strat = MentionMomentum(top_fraction=0.5)  # top half
    # Build 4 tickers with different scores.
    claims = []
    cid = 0
    for ticker, pol_count in [("AAA", 1), ("BBB", 2), ("CCC", 3), ("DDD", 4)]:
        for n in range(pol_count):
            cid += 1
            claims.append(
                _claim(
                    ticker=ticker,
                    polarity="bullish",
                    days_ago=1,
                    creator_id=n + 100,
                    claim_id=cid,
                )
            )
    ctx = _ctx(claims=claims, universe=["AAA", "BBB", "CCC", "DDD"])
    out = strat.evaluate(ctx)
    # Top half = 2 names; expect DDD + CCC.
    assert sorted(d.ticker for d in out) == ["CCC", "DDD"]


def test_equal_weights_sum_to_one():
    strat = MentionMomentum(top_fraction=1.0)
    claims = [
        _claim(ticker=t, polarity="bullish", days_ago=1, creator_id=i + 10, claim_id=i + 1)
        for i, t in enumerate(["AAA", "BBB", "CCC"])
    ]
    ctx = _ctx(claims=claims, universe=["AAA", "BBB", "CCC"])
    out = strat.evaluate(ctx)
    total = sum(d.weight for d in out)
    assert total == pytest.approx(1.0, rel=1e-6)


def test_min_creators_per_ticker_gate():
    strat = MentionMomentum(min_creators_per_ticker=2)
    # AAPL has 1 creator, NVDA has 2.
    claims = [
        _claim(ticker="AAPL", polarity="bullish", days_ago=1, creator_id=1, claim_id=1),
        _claim(ticker="NVDA", polarity="bullish", days_ago=1, creator_id=2, claim_id=2),
        _claim(ticker="NVDA", polarity="bullish", days_ago=1, creator_id=3, claim_id=3),
    ]
    ctx = _ctx(claims=claims, universe=["AAPL", "NVDA"])
    out = strat.evaluate(ctx)
    assert [d.ticker for d in out] == ["NVDA"]


def test_required_lookback_days_floor():
    strat = MentionMomentum(lookback_days=3)
    # The harness needs at least 14 days even when lookback_days < 14.
    assert strat.required_lookback_days == 14


def test_invalid_top_fraction_raises():
    with pytest.raises(ValueError):
        MentionMomentum(top_fraction=0)
    with pytest.raises(ValueError):
        MentionMomentum(top_fraction=1.1)
