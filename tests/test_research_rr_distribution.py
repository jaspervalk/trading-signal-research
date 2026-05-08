"""Tests for compute_rr_distribution() and RRDistribution / RRCombo schemas.

The R/R distribution surfaces the *range* of plausible R/Rs across all
candidate combinations, so the user sees that the headline R/R is one
point in a distribution — not the answer.
"""
from app.research.schema import RRCombo, RRDistribution


def test_rr_distribution_schema_importable():
    combo = RRCombo(
        entry_kind="breakout",
        primary_index=0,
        runner_index=None,
        invalidation_index=0,
        entry_label="63d high band",
        primary_label="nearest resistance",
        runner_label=None,
        invalidation_label="nearest support",
        rr_primary=1.5,
        rr_runner=None,
        rr_blended=1.5,
        is_chosen=True,
    )
    dist = RRDistribution(
        min_rr=0.8, median_rr=1.5, max_rr=2.4, n_combos=12, combos=[combo]
    )
    assert dist.min_rr == 0.8
    assert dist.median_rr == 1.5
    assert dist.max_rr == 2.4
    assert dist.combos[0].is_chosen is True


from app.research.rr_distribution import (
    Picks,
    compute_rr_distribution,
)
from app.research.schema import CandidateLevels, ZoneBand


def _candidates() -> CandidateLevels:
    """A small, fully-specified candidate grid — easy to enumerate by hand."""
    return CandidateLevels(
        breakout_entry=ZoneBand(low=108.0, high=109.0, method="63d high"),
        pullback_entry=ZoneBand(low=98.0, high=100.0, method="50-SMA"),
        primary_exit_candidates=[
            ZoneBand(low=105.0, high=105.5, method="nearest_resistance"),
            ZoneBand(low=112.0, high=112.5, method="63d high"),
        ],
        runner_exit_candidates=[
            ZoneBand(low=118.0, high=118.5, method="primary + 1.5xATR"),
        ],
        invalidation_candidates=[92.0, 95.0],
        last_close=104.0,
        atr_14=2.0,
    )


def test_distribution_enumerates_all_combos():
    picks = Picks(
        entry_kind="breakout",
        primary_index=0,
        runner_index=0,
        invalidation_index=0,
    )
    dist = compute_rr_distribution(_candidates(), picks)
    # 2 entries x 2 primaries x (1 runner + None) x 2 invalidations = 16 cartesian.
    # But breakout entry (high=109) vs primary[0] (low=105) gives reward<=0, so
    # those 4 combos (1 entry x 1 primary x 2 runner-states x 2 inv) are skipped.
    assert dist.n_combos == 12


def test_distribution_marks_chosen_combo():
    picks = Picks(
        entry_kind="pullback",
        primary_index=1,
        runner_index=0,
        invalidation_index=1,
    )
    dist = compute_rr_distribution(_candidates(), picks)
    chosen = [c for c in dist.combos if c.is_chosen]
    assert len(chosen) == 1
    assert chosen[0].entry_kind == "pullback"
    assert chosen[0].primary_index == 1
    assert chosen[0].runner_index == 0
    assert chosen[0].invalidation_index == 1


def test_distribution_min_max_median_match_combos():
    picks = Picks(
        entry_kind="breakout",
        primary_index=0,
        runner_index=None,
        invalidation_index=0,
    )
    dist = compute_rr_distribution(_candidates(), picks)
    rrs = sorted(c.rr_blended for c in dist.combos)
    assert dist.min_rr == rrs[0]
    assert dist.max_rr == rrs[-1]
    # Median for even-length list = average of middle two, rounded to 2dp.
    mid = len(rrs) // 2
    expected_median = round((rrs[mid - 1] + rrs[mid]) / 2, 2)
    assert dist.median_rr == expected_median


def test_distribution_handles_no_runner():
    picks = Picks(
        entry_kind="breakout",
        primary_index=0,
        runner_index=None,
        invalidation_index=0,
    )
    cl = _candidates()
    cl.runner_exit_candidates = []
    dist = compute_rr_distribution(cl, picks)
    # 2 entries x 2 primaries x 2 invalidations = 8 cartesian. Breakout entry
    # (high=109) vs primary[0] (low=105) gives reward<=0, so 2 combos skipped.
    assert dist.n_combos == 6
    for c in dist.combos:
        assert c.runner_index is None
        assert c.runner_label is None
        assert c.rr_runner is None


def test_distribution_skips_combos_with_invalid_risk():
    """If `entry.low - invalidation <= 0` the combo is skipped (R/R undefined)."""
    cl = CandidateLevels(
        breakout_entry=ZoneBand(low=100.0, high=101.0, method="x"),
        primary_exit_candidates=[ZoneBand(low=110.0, high=111.0, method="x")],
        invalidation_candidates=[95.0, 105.0],  # 105.0 is ABOVE entry.low -> skip
        last_close=99.0,
        atr_14=2.0,
    )
    picks = Picks(
        entry_kind="breakout",
        primary_index=0,
        runner_index=None,
        invalidation_index=0,
    )
    dist = compute_rr_distribution(cl, picks)
    assert dist.n_combos == 1  # only the 95.0 invalidation produces a valid R/R


def test_distribution_skips_combos_with_invalid_reward():
    """If `exit.low - entry.high <= 0` the combo is skipped (R/R undefined or zero)."""
    cl = CandidateLevels(
        breakout_entry=ZoneBand(low=110.0, high=111.0, method="entry"),
        primary_exit_candidates=[
            ZoneBand(low=120.0, high=121.0, method="valid_target"),
            ZoneBand(low=105.0, high=106.0, method="below_entry"),  # reward <= 0
        ],
        invalidation_candidates=[100.0],
        last_close=110.0,
        atr_14=2.0,
    )
    picks = Picks(
        entry_kind="breakout",
        primary_index=0,
        runner_index=None,
        invalidation_index=0,
    )
    dist = compute_rr_distribution(cl, picks)
    # 1 entry x 2 primaries x 1 invalidation = 2 cartesian, but 1 has invalid reward -> skip -> 1 combo
    assert dist.n_combos == 1
    # The single surviving combo's primary_index should be 0 (the valid one), not 1 (the broken one)
    assert dist.combos[0].primary_index == 0
    assert dist.combos[0].rr_blended > 0
