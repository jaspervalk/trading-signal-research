"""Tests for the prefilter — candidate window selection."""

from __future__ import annotations

from dataclasses import dataclass

from app.extract.prefilter import find_candidate_windows, score_window
from app.normalize.tickers import Universe


@dataclass
class Seg:
    id: int
    start_seconds: float
    end_seconds: float
    text: str


def _toy_universe() -> Universe:
    u = Universe()
    for t in ["NVDA", "AAPL", "AMD", "SPY", "TSLA"]:
        u.by_ticker[t] = {"name": t.title(), "sector": "X"}
    return u


def test_score_call_like_window():
    u = _toy_universe()
    score, mentions, verbs, prices = score_window(
        "I'm watching $NVDA above 920 — looks like a clean breakout setup.", u
    )
    assert score >= 4.0
    assert any(m.ticker == "NVDA" for m in mentions)
    assert verbs >= 2  # "watching", "above", "breakout", "setup"
    assert prices >= 1


def test_score_irrelevant_window():
    u = _toy_universe()
    score, _, _, _ = score_window("Today we're going to talk about CPI inflation data.", u)
    assert score < 2.0


def test_find_candidates_filters_below_threshold():
    u = _toy_universe()
    segs = [
        Seg(1, 0.0, 5.0, "Welcome to the channel"),
        Seg(2, 5.0, 10.0, "today we discuss fed policy"),
        Seg(3, 30.0, 35.0, "watching $NVDA above 920"),  # gap > 3 → new window
        Seg(4, 35.0, 40.0, "with target 950 and stop 905"),
    ]
    cands = find_candidate_windows(segs, u, min_score=2.0)
    assert len(cands) == 1
    cand = cands[0]
    assert any(m.ticker == "NVDA" for m in cand.ticker_mentions)
    assert cand.score >= 3.0


def test_find_candidates_includes_context():
    u = _toy_universe()
    segs = [
        Seg(1, 0.0, 5.0, "fed talk"),
        Seg(2, 100.0, 105.0, "$NVDA above 920 setup looks great target 950"),
        Seg(3, 200.0, 205.0, "later commentary"),
    ]
    cands = find_candidate_windows(segs, u, min_score=2.0, context_before=120, context_after=120)
    assert len(cands) == 1
    cand = cands[0]
    assert "fed talk" in cand.context.text
    assert "later commentary" in cand.context.text


def test_empty_segments():
    u = _toy_universe()
    assert find_candidate_windows([], u) == []


def test_adjacent_qualifying_windows_merge_into_one_cluster():
    """A long discussion that produces several qualifying windows should
    yield ONE candidate (one LLM call), not many duplicates."""
    u = _toy_universe()
    # Build a continuous ~120s stretch of NVDA-call language broken into
    # ~25s chunks. With merge_gap_seconds defaulting to 60, all should cluster.
    segs = []
    for i in range(6):
        start = i * 20.0
        segs.append(Seg(i + 1, start, start + 5.0, "watching $NVDA above 920 setup"))
        segs.append(Seg(i + 100, start + 5.0, start + 10.0, "target 950 stop 905"))
    cands = find_candidate_windows(segs, u, min_score=2.0)
    assert len(cands) == 1


def test_distant_qualifying_windows_remain_separate_clusters():
    """Two separate discussions far apart should produce two candidates."""
    u = _toy_universe()
    segs = [
        Seg(1, 0.0, 5.0, "watching $NVDA above 920 setup"),
        Seg(2, 5.0, 10.0, "target 950 stop 905"),
        # 5 minute gap — well past merge_gap_seconds (default 60)
        Seg(3, 600.0, 605.0, "now $AAPL breakout setup above 195"),
        Seg(4, 605.0, 610.0, "target 210 stop 188"),
    ]
    cands = find_candidate_windows(segs, u, min_score=2.0, merge_gap_seconds=60.0)
    assert len(cands) == 2
    tickers = {m.ticker for c in cands for m in c.ticker_mentions}
    assert tickers == {"NVDA", "AAPL"}
