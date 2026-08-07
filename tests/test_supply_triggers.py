"""Tests for the Layer C gross-margin inflection trigger.

Definition under test (from `app.supply.triggers` module docstring): two
consecutive quarters of sequential (QoQ) gross-margin expansion, immediately
following a 4-quarter window (right before the expansion streak began) that
shows a net decline (first quarter of that window > its last quarter).

All margin series here are chronological, oldest first, matching
`MarginHistory.gross_margins`.
"""

from __future__ import annotations

import pytest

from app.supply.triggers import (
    MIN_QUARTERS,
    evaluate_gross_margin_inflection,
)

# --- the four scenarios named in the brief ----------------------------------


def test_clean_inflection_after_a_decline_fires():
    # 4-quarter decline: .30 -> .20 (net decline), then 2 expanding quarters.
    margins = [0.30, 0.28, 0.26, 0.20, 0.22, 0.25]
    result = evaluate_gross_margin_inflection(margins)
    assert result.status == "firing"
    assert result.expansion_streak == 2
    assert result.prior_decline_pp == pytest.approx(10.0)


def test_one_expanding_quarter_does_not_fire():
    # Only the most recent quarter expanded; the one before it did not
    # extend the streak (0.20 < 0.26) so the streak length is exactly 1.
    margins = [0.32, 0.30, 0.28, 0.26, 0.20, 0.22]
    result = evaluate_gross_margin_inflection(margins)
    assert result.status == "armed"
    assert result.expansion_streak == 1
    assert result.status not in ("firing", "confirmed")


def test_expansion_with_no_prior_decline_does_not_fire():
    # Exactly 2 expanding quarters (streak capped at 2 by the .20 <= .25
    # break), but the 4 quarters before them are flat (0.20 -> 0.20), not a
    # net decline.
    margins = [0.20, 0.18, 0.25, 0.20, 0.22, 0.25]
    result = evaluate_gross_margin_inflection(margins)
    assert result.expansion_streak == 2
    assert result.status == "not_armed"
    assert result.status not in ("firing", "confirmed")


@pytest.mark.parametrize("n", [0, 1, 3, 5])
def test_insufficient_history_returns_a_well_defined_cannot_evaluate(n):
    margins = [0.20 + 0.01 * i for i in range(n)]
    result = evaluate_gross_margin_inflection(margins)
    assert result.status == "insufficient_history"
    assert result.quarters_of_history == n
    # A "cannot evaluate" must never masquerade as "evaluated, no signal."
    assert result.status not in ("not_armed", "armed", "firing", "confirmed")


def test_exactly_min_quarters_boundary_is_evaluated_not_insufficient():
    assert MIN_QUARTERS == 6
    margins = [0.30, 0.28, 0.26, 0.20, 0.22, 0.25]
    assert len(margins) == MIN_QUARTERS
    result = evaluate_gross_margin_inflection(margins)
    assert result.status != "insufficient_history"


# --- confirmed: streak extends beyond the minimum 2 quarters ----------------


def test_three_expanding_quarters_after_a_decline_is_confirmed():
    # Decline over the 4 quarters ending at .20 (0.30 -> 0.20), then THREE
    # consecutive expanding quarters: .22, .25, .29.
    margins = [0.30, 0.28, 0.26, 0.20, 0.22, 0.25, 0.29]
    result = evaluate_gross_margin_inflection(margins)
    assert result.status == "confirmed"
    assert result.expansion_streak == 3
    assert result.prior_decline_pp == pytest.approx(10.0)


# --- no expansion at all -----------------------------------------------------


def test_flat_or_declining_most_recent_quarter_is_not_armed():
    margins = [0.20, 0.22, 0.24, 0.26, 0.28, 0.27]  # last quarter declined
    result = evaluate_gross_margin_inflection(margins)
    assert result.status == "not_armed"
    assert result.expansion_streak == 0
    assert result.prior_decline_pp is None


# --- streak eats into all available history ----------------------------------


def test_streak_with_no_room_for_a_prior_decline_check_is_not_armed():
    # Every quarter after the first is an increase over the previous one --
    # the streak runs all the way to the start of the series, leaving no
    # 4-quarter window before it to check for a decline.
    margins = [0.10, 0.12, 0.14, 0.16, 0.18, 0.20]
    result = evaluate_gross_margin_inflection(margins)
    assert result.status == "not_armed"
    assert result.expansion_streak == 5


# --- draws_attention property -------------------------------------------------


@pytest.mark.parametrize(
    "margins,expected",
    [
        ([0.30, 0.28, 0.26, 0.20, 0.22, 0.25], True),  # firing
        ([0.30, 0.28, 0.26, 0.20, 0.22, 0.25, 0.29], True),  # confirmed
        ([0.32, 0.30, 0.28, 0.26, 0.20, 0.22], False),  # armed
        ([0.20, 0.22, 0.24, 0.26, 0.28, 0.27], False),  # not_armed
    ],
)
def test_draws_attention_only_true_for_firing_or_confirmed(margins, expected):
    result = evaluate_gross_margin_inflection(margins)
    assert result.draws_attention is expected


def test_insufficient_history_does_not_draw_attention():
    result = evaluate_gross_margin_inflection([0.20, 0.22])
    assert result.draws_attention is False
