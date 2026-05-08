"""Unit tests for BullishCatalystAggregator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.strategies.base import StrategyContext
from app.strategies.bullish_catalyst import BullishCatalystAggregator


class _StubReader:
    def daily_bars(self, *a, **k):
        import pandas as pd
        return pd.DataFrame()

    def benchmark_bars(self, *a, **k):
        import pandas as pd
        return pd.DataFrame()


def _claim(
    *,
    ticker: str,
    polarity: str = "bullish",
    claim_type: str = "catalyst",
    claim_class: str = "factual",
    days_ago: int = 1,
    creator_id: int,
    claim_id: int,
) -> dict[str, Any]:
    base = datetime(2025, 6, 1, tzinfo=UTC)
    return {
        "id": claim_id,
        "ticker": ticker,
        "polarity": polarity,
        "claim_class": claim_class,
        "claim_type": claim_type,
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


def test_no_decisions_when_no_recent_claims():
    strat = BullishCatalystAggregator()
    ctx = _ctx(claims=[], universe=["AAPL"])
    assert strat.evaluate(ctx) == []


def test_requires_min_distinct_creators():
    strat = BullishCatalystAggregator(min_distinct_creators=2)
    # Only one creator → no qualifying ticker.
    claims = [
        _claim(ticker="AAPL", days_ago=1, creator_id=1, claim_id=1),
        _claim(ticker="AAPL", days_ago=2, creator_id=1, claim_id=2),
    ]
    ctx = _ctx(claims=claims, universe=["AAPL"])
    assert strat.evaluate(ctx) == []


def test_qualifies_with_two_distinct_creators():
    strat = BullishCatalystAggregator(min_distinct_creators=2)
    claims = [
        _claim(ticker="AAPL", days_ago=1, creator_id=1, claim_id=1),
        _claim(ticker="AAPL", days_ago=2, creator_id=2, claim_id=2),
    ]
    ctx = _ctx(claims=claims, universe=["AAPL"])
    out = strat.evaluate(ctx)
    assert len(out) == 1
    assert out[0].ticker == "AAPL"
    assert out[0].direction == "long"
    assert out[0].score == 2.0  # n_distinct_creators


def test_filters_to_catalyst_claim_type():
    strat = BullishCatalystAggregator(min_distinct_creators=2)
    # One catalyst, one risk → only one qualifying creator on the catalyst.
    claims = [
        _claim(ticker="AAPL", claim_type="catalyst", days_ago=1, creator_id=1, claim_id=1),
        _claim(ticker="AAPL", claim_type="risk", days_ago=1, creator_id=2, claim_id=2),
    ]
    ctx = _ctx(claims=claims, universe=["AAPL"])
    assert strat.evaluate(ctx) == []


def test_filters_to_allowed_classes():
    strat = BullishCatalystAggregator(
        min_distinct_creators=2, claim_classes_allowed=("factual",)
    )
    claims = [
        _claim(ticker="AAPL", claim_class="factual", days_ago=1, creator_id=1, claim_id=1),
        _claim(ticker="AAPL", claim_class="speculation", days_ago=1, creator_id=2, claim_id=2),
    ]
    ctx = _ctx(claims=claims, universe=["AAPL"])
    assert strat.evaluate(ctx) == []


def test_drops_non_universe_tickers():
    strat = BullishCatalystAggregator(min_distinct_creators=2)
    claims = [
        _claim(ticker="ETSY", days_ago=1, creator_id=1, claim_id=1),
        _claim(ticker="ETSY", days_ago=1, creator_id=2, claim_id=2),
    ]
    ctx = _ctx(claims=claims, universe=["AAPL"])
    assert strat.evaluate(ctx) == []


def test_filters_to_lookback_window():
    strat = BullishCatalystAggregator(lookback_days=14, min_distinct_creators=2)
    claims = [
        _claim(ticker="AAPL", days_ago=20, creator_id=1, claim_id=1),  # outside window
        _claim(ticker="AAPL", days_ago=2, creator_id=2, claim_id=2),
    ]
    ctx = _ctx(claims=claims, universe=["AAPL"])
    assert strat.evaluate(ctx) == []


def test_equal_weights():
    strat = BullishCatalystAggregator(min_distinct_creators=2)
    claims = []
    cid = 0
    for ticker in ["AAA", "BBB", "CCC"]:
        for c in (1, 2):
            cid += 1
            claims.append(_claim(ticker=ticker, days_ago=1, creator_id=c * 10 + (0 if ticker == "AAA" else c), claim_id=cid))
    ctx = _ctx(claims=claims, universe=["AAA", "BBB", "CCC"])
    out = strat.evaluate(ctx)
    weights = [d.weight for d in out]
    assert all(abs(w - weights[0]) < 1e-9 for w in weights)
    assert sum(weights) == pytest.approx(1.0)
