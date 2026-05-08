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
