"""Deterministic exit-zone math tests.

Pure function — no DB, no network, no LLM.
"""

from __future__ import annotations

from app.analysis.schema import (
    EntryZoneCandidate,
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
)
from app.research.exits import build_candidate_levels


def _market(last: float = 100.0) -> MarketSnapshotPanel:
    from datetime import UTC, datetime
    return MarketSnapshotPanel(as_of=datetime(2026, 5, 7, tzinfo=UTC), last_close=last)


def _indicators(atr: float | None = 2.0) -> IndicatorPanel:
    return IndicatorPanel(atr_14=atr)


def _levels(
    *,
    nearest_resistance: float | None = 105.0,
    recent_high_63d: float | None = 108.0,
    base_low: float | None = 80.0,
    nearest_support: float | None = 95.0,
) -> LevelsPanel:
    return LevelsPanel(
        nearest_resistance=nearest_resistance,
        recent_high_63d=recent_high_63d,
        base_low=base_low,
        nearest_support=nearest_support,
    )


# ---------------------------------------------------------------------------
# Primary exits


def test_primary_exits_emit_resistance_band_with_atr_cushion():
    cl = build_candidate_levels(
        indicators=_indicators(atr=2.0),
        levels=_levels(nearest_resistance=105.0),
        market=_market(last=100.0),
        breakout_entry=None,
        pullback_entry=None,
    )
    # First candidate = nearest resistance (105.0) → 105.0 + 0.25*2.0 = 105.5.
    first = cl.primary_exit_candidates[0]
    assert first.low == 105.0
    assert abs(first.high - 105.5) < 1e-9
    assert "resistance" in first.method


def test_primary_exits_skip_recent_high_when_too_close_to_resistance():
    # 105 (resistance) and 106 (recent high) are within 1×ATR (=2). The recent-high
    # candidate should be suppressed to avoid duplicating.
    cl = build_candidate_levels(
        indicators=_indicators(atr=2.0),
        levels=_levels(nearest_resistance=105.0, recent_high_63d=106.0),
        market=_market(last=100.0),
        breakout_entry=None,
        pullback_entry=None,
    )
    methods = [z.method for z in cl.primary_exit_candidates]
    # Only one of the two near-overlapping anchors should appear in primary_exit_candidates.
    assert sum("resistance" in m or "63-bar" in m for m in methods[:2]) == 1


def test_primary_exits_emit_recent_high_when_distinct_from_resistance():
    cl = build_candidate_levels(
        indicators=_indicators(atr=2.0),
        levels=_levels(nearest_resistance=103.0, recent_high_63d=120.0),
        market=_market(last=100.0),
        breakout_entry=None,
        pullback_entry=None,
    )
    methods = " | ".join(z.method for z in cl.primary_exit_candidates)
    assert "resistance" in methods
    assert "63-bar" in methods


def test_primary_exits_include_fib_extension():
    cl = build_candidate_levels(
        indicators=_indicators(atr=2.0),
        levels=_levels(base_low=80.0, recent_high_63d=120.0),
        market=_market(last=100.0),
        breakout_entry=None,
        pullback_entry=None,
    )
    fibs = [z for z in cl.primary_exit_candidates if "Fibonacci" in z.method]
    assert len(fibs) == 1
    # 80 + (120 - 80) * 1.272 = 130.88
    assert abs(fibs[0].low - 130.88) < 1e-2


def test_primary_exits_skip_resistance_below_price():
    # Resistance below price is not an exit candidate.
    cl = build_candidate_levels(
        indicators=_indicators(atr=2.0),
        levels=_levels(nearest_resistance=95.0, recent_high_63d=120.0),
        market=_market(last=100.0),
        breakout_entry=None,
        pullback_entry=None,
    )
    methods = [z.method for z in cl.primary_exit_candidates]
    assert not any("nearest resistance" in m for m in methods)


# ---------------------------------------------------------------------------
# Runner exits


def test_runner_exits_offset_each_primary_by_1_5_atr():
    cl = build_candidate_levels(
        indicators=_indicators(atr=2.0),
        levels=_levels(nearest_resistance=105.0, recent_high_63d=130.0),
        market=_market(last=100.0),
        breakout_entry=None,
        pullback_entry=None,
    )
    # Each primary high gets a runner low at +1.5×ATR (= +3.0 here).
    primaries = cl.primary_exit_candidates
    runners = cl.runner_exit_candidates
    assert any(abs(r.low - (primaries[0].high + 3.0)) < 1e-6 for r in runners)


def test_runner_exits_include_fib_1618():
    cl = build_candidate_levels(
        indicators=_indicators(atr=2.0),
        levels=_levels(base_low=80.0, recent_high_63d=120.0),
        market=_market(last=100.0),
        breakout_entry=None,
        pullback_entry=None,
    )
    fibs = [z for z in cl.runner_exit_candidates if "1.618" in z.method]
    assert len(fibs) == 1
    # 80 + (120 - 80) * 1.618 = 144.72
    assert abs(fibs[0].low - 144.72) < 1e-2


def test_runner_exits_sorted_by_low():
    cl = build_candidate_levels(
        indicators=_indicators(atr=2.0),
        levels=_levels(nearest_resistance=105.0, recent_high_63d=130.0, base_low=80.0),
        market=_market(last=100.0),
        breakout_entry=None,
        pullback_entry=None,
    )
    lows = [z.low for z in cl.runner_exit_candidates]
    assert lows == sorted(lows)


# ---------------------------------------------------------------------------
# Invalidations


def test_invalidations_aggregate_from_entries_and_levels():
    breakout = EntryZoneCandidate(
        available=True,
        candidate_research_zone_low=110.0,
        candidate_research_zone_high=111.0,
        invalidation_reference=104.0,
    )
    pullback = EntryZoneCandidate(
        available=True,
        candidate_research_zone_low=98.0,
        candidate_research_zone_high=100.0,
        invalidation_reference=92.0,
    )
    cl = build_candidate_levels(
        indicators=_indicators(atr=2.0),
        levels=_levels(nearest_support=95.0, base_low=80.0),
        market=_market(last=100.0),
        breakout_entry=breakout,
        pullback_entry=pullback,
    )
    # Entries' invalidation refs + nearest_support + (base_low - 0.5*ATR) = 79.0
    assert 104.0 in cl.invalidation_candidates
    assert 92.0 in cl.invalidation_candidates
    assert 95.0 in cl.invalidation_candidates
    assert 79.0 in cl.invalidation_candidates


def test_invalidations_dedupe():
    # If the breakout's invalidation == nearest_support, only one entry.
    breakout = EntryZoneCandidate(
        available=True,
        candidate_research_zone_low=110.0,
        candidate_research_zone_high=111.0,
        invalidation_reference=95.0,
    )
    cl = build_candidate_levels(
        indicators=_indicators(atr=2.0),
        levels=_levels(nearest_support=95.0, base_low=None),
        market=_market(last=100.0),
        breakout_entry=breakout,
        pullback_entry=None,
    )
    assert cl.invalidation_candidates.count(95.0) == 1


# ---------------------------------------------------------------------------
# Entry pass-through


def test_breakout_and_pullback_entries_passthrough():
    breakout = EntryZoneCandidate(
        available=True,
        candidate_research_zone_low=110.0,
        candidate_research_zone_high=111.0,
    )
    pullback = EntryZoneCandidate(
        available=True,
        candidate_research_zone_low=98.0,
        candidate_research_zone_high=100.0,
    )
    cl = build_candidate_levels(
        indicators=_indicators(atr=2.0),
        levels=_levels(),
        market=_market(last=100.0),
        breakout_entry=breakout,
        pullback_entry=pullback,
    )
    assert cl.breakout_entry is not None
    assert cl.breakout_entry.low == 110.0
    assert cl.pullback_entry is not None
    assert cl.pullback_entry.low == 98.0


def test_unavailable_entry_zones_become_none():
    breakout = EntryZoneCandidate(available=False)
    cl = build_candidate_levels(
        indicators=_indicators(atr=2.0),
        levels=_levels(),
        market=_market(last=100.0),
        breakout_entry=breakout,
        pullback_entry=None,
    )
    # We pass through the breakout candidate even if marked unavailable —
    # but its values are zeroed since they were None on the input.
    # The Quick path filters it via available=False before this is called.
    assert cl.breakout_entry is None or (
        cl.breakout_entry.low == 0.0 and cl.breakout_entry.high == 0.0
    )


# ---------------------------------------------------------------------------
# Empty-input edge cases


def test_no_atr_no_last_close_yields_no_runners():
    cl = build_candidate_levels(
        indicators=_indicators(atr=None),
        levels=_levels(),
        market=_market(last=100.0),
        breakout_entry=None,
        pullback_entry=None,
    )
    assert cl.runner_exit_candidates == []


def test_no_levels_yields_no_primaries():
    cl = build_candidate_levels(
        indicators=_indicators(atr=2.0),
        levels=LevelsPanel(),
        market=_market(last=100.0),
        breakout_entry=None,
        pullback_entry=None,
    )
    assert cl.primary_exit_candidates == []
    assert cl.invalidation_candidates == []
