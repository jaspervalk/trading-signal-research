"""Tests for Layer B: the constraint join, the cut, and the score.

Every assertion here is either a hand-computed worked example (score must
equal the literal product of its components) or a structural claim from the
brief: a ticker absent from any constraint's exposures never appears in
output; a stale constraint is still scored, just flagged; confidence maps to
its fixed weight.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.supply.constraints import Constraint, ConstraintExposure
from app.supply.layer_b import CONFIDENCE_WEIGHTS, run_layer_b
from app.supply.metrics import SupplyMetrics


def _constraint(
    *,
    id: str = "test_constraint",
    deficit_pct: float | None = 0.05,
    expansion_lead_months: float | None = 18.0,
    confidence: str = "high",
    is_stale: bool = False,
    exposures: list[ConstraintExposure] | None = None,
) -> Constraint:
    c = Constraint(
        id=id,
        market="widgets",
        deficit_pct=deficit_pct,
        deficit_source="Some Report (2025)",
        deficit_horizon="2025-2026",
        expansion_lead_months=expansion_lead_months,
        demand_driver="test driver",
        capacity_history="test history",
        confidence=confidence,
        last_reviewed=date(2026, 1, 1),
        exposures=exposures or [],
    )
    c.is_stale = is_stale
    return c


def _exposure(
    ticker: str, revenue_exposure_pct: float = 0.5, is_pure_play: bool = False
) -> ConstraintExposure:
    return ConstraintExposure(
        ticker=ticker,
        revenue_exposure_pct=revenue_exposure_pct,
        exposure_source="unverified estimate",
        is_pure_play=is_pure_play,
    )


def _metrics(earnings_torque: float | None, coverage_multiplier: float = 1.0) -> SupplyMetrics:
    return SupplyMetrics(
        quarters_of_history=30,
        sufficient_history=earnings_torque is not None,
        earnings_torque=earnings_torque,
        capital_intensity=0.6,
        coverage_multiplier=coverage_multiplier,
    )


# --- the worked example ------------------------------------------------------


def test_score_equals_the_hand_computed_product():
    """earnings_torque=0.30, deficit_pct=0.045 (-> 0.045/0.05=0.9),
    expansion_lead_months=24 (-> min(24/18, 2.0)=1.3333...),
    revenue_exposure_pct=0.7, confidence=high (-> 1.0),
    coverage_multiplier=1.5.

    score = 0.30 * 0.9 * (24/18) * 0.7 * 1.0 * 1.5
          = 0.30 * 0.9 * 1.333333... * 0.7 * 1.0 * 1.5
          = 0.3780000...
    """
    constraint = _constraint(
        deficit_pct=0.045,
        expansion_lead_months=24.0,
        confidence="high",
        exposures=[_exposure("WDC", revenue_exposure_pct=0.7)],
    )
    metrics_by_ticker = {"WDC": _metrics(earnings_torque=0.30, coverage_multiplier=1.5)}

    results = run_layer_b([constraint], metrics_by_ticker)
    assert len(results) == 1
    r = results[0]

    expected = 0.30 * (0.045 / 0.05) * min(24.0 / 18.0, 2.0) * 0.7 * 1.0 * 1.5
    assert r.score == pytest.approx(expected)
    assert r.score == pytest.approx(0.3780, abs=1e-6)

    # Every component is visible, not folded away.
    assert r.earnings_torque == pytest.approx(0.30)
    assert r.deficit_pct == pytest.approx(0.045)
    assert r.expansion_lead_months == pytest.approx(24.0)
    assert r.expansion_lead_factor == pytest.approx(24.0 / 18.0)
    assert r.revenue_exposure_pct == pytest.approx(0.7)
    assert r.confidence == "high"
    assert r.confidence_weight == pytest.approx(1.0)
    assert r.coverage_multiplier == pytest.approx(1.5)


def test_expansion_lead_factor_is_capped_at_two():
    """36 months -> 36/18 = 2.0 exactly; 60 months would be 3.33 but must cap at 2.0."""
    constraint = _constraint(
        expansion_lead_months=60.0, exposures=[_exposure("WDC", revenue_exposure_pct=1.0)]
    )
    metrics_by_ticker = {"WDC": _metrics(earnings_torque=0.5, coverage_multiplier=1.0)}
    results = run_layer_b([constraint], metrics_by_ticker)
    assert results[0].expansion_lead_factor == pytest.approx(2.0)


# --- confidence weight mapping -----------------------------------------------


@pytest.mark.parametrize(
    "confidence,expected_weight",
    [("high", 1.0), ("medium", 0.6), ("low", 0.3)],
)
def test_confidence_weight_mapping(confidence, expected_weight):
    assert CONFIDENCE_WEIGHTS[confidence] == pytest.approx(expected_weight)

    constraint = _constraint(
        confidence=confidence, exposures=[_exposure("WDC", revenue_exposure_pct=1.0)]
    )
    metrics_by_ticker = {"WDC": _metrics(earnings_torque=1.0, coverage_multiplier=1.0)}
    results = run_layer_b([constraint], metrics_by_ticker)
    assert results[0].confidence_weight == pytest.approx(expected_weight)
    # torque=1, deficit_ratio=1 (default 0.05/0.05), lead_factor=1 (18/18),
    # exposure=1, coverage=1 -> score reduces to exactly the confidence weight.
    assert results[0].score == pytest.approx(expected_weight)


# --- the cut: no exposure, no output -----------------------------------------


def test_ticker_with_no_constraint_exposure_is_absent_from_output():
    constraint = _constraint(exposures=[_exposure("WDC")])
    metrics_by_ticker = {
        "WDC": _metrics(earnings_torque=0.5),
        "AAPL": _metrics(earnings_torque=0.9),  # never exposed to any constraint
    }
    results = run_layer_b([constraint], metrics_by_ticker)
    tickers = {r.ticker for r in results}
    assert tickers == {"WDC"}
    assert "AAPL" not in tickers


def test_no_constraints_yields_no_output():
    assert run_layer_b([], {"WDC": _metrics(earnings_torque=0.5)}) == []


# --- stale constraints are included but flagged ------------------------------


def test_stale_constraint_is_still_scored_but_flagged():
    constraint = _constraint(is_stale=True, exposures=[_exposure("WDC", revenue_exposure_pct=1.0)])
    metrics_by_ticker = {"WDC": _metrics(earnings_torque=0.4, coverage_multiplier=1.0)}
    results = run_layer_b([constraint], metrics_by_ticker)
    assert len(results) == 1
    assert results[0].is_stale is True
    assert results[0].score is not None  # scored, not excluded


def test_fresh_constraint_is_not_flagged_stale():
    constraint = _constraint(is_stale=False, exposures=[_exposure("WDC")])
    metrics_by_ticker = {"WDC": _metrics(earnings_torque=0.4)}
    results = run_layer_b([constraint], metrics_by_ticker)
    assert results[0].is_stale is False


# --- missing Layer A coverage: recorded, not dropped -------------------------


def test_exposed_ticker_with_no_layer_a_metrics_is_recorded_with_none_score():
    constraint = _constraint(exposures=[_exposure("ZZZZ")])
    results = run_layer_b([constraint], metrics_by_ticker={})
    assert len(results) == 1
    assert results[0].ticker == "ZZZZ"
    assert results[0].score is None
    assert results[0].reason is not None


def test_exposed_ticker_with_insufficient_history_is_recorded_with_none_score():
    constraint = _constraint(exposures=[_exposure("WDC")])
    metrics_by_ticker = {"WDC": _metrics(earnings_torque=None)}
    results = run_layer_b([constraint], metrics_by_ticker)
    assert results[0].score is None
    assert "insufficient history" in results[0].reason


# --- deficit_pct is optional: a constraint with capacity destruction but no
# credible projected deficit can never produce a score (Change 3) ------------


def test_null_deficit_pct_yields_no_score_but_still_a_recorded_result():
    """The 2026-08-06 TiO2 entry shape: real Layer A coverage (torque
    present) but no deficit_pct -- must be recorded with score=None and a
    reason, not silently dropped or crashed on."""
    constraint = _constraint(
        deficit_pct=None, expansion_lead_months=None,
        exposures=[_exposure("TROX", revenue_exposure_pct=0.95)],
    )
    metrics_by_ticker = {"TROX": _metrics(earnings_torque=0.5, coverage_multiplier=1.0)}
    results = run_layer_b([constraint], metrics_by_ticker)
    assert len(results) == 1
    r = results[0]
    assert r.score is None
    assert r.reason is not None
    assert "deficit_pct" in r.reason
    # Every input still travels with the result, even when unscoreable.
    assert r.deficit_pct is None
    assert r.expansion_lead_months is None
    assert r.expansion_lead_factor is None
    assert r.earnings_torque == pytest.approx(0.5)


def test_null_deficit_pct_takes_priority_reason_over_missing_torque_is_still_distinct():
    """When BOTH torque and deficit_pct are missing, the earnings_torque
    reason wins (torque is checked first) -- still recorded, still distinct
    reasons, never crashes."""
    constraint = _constraint(
        deficit_pct=None, expansion_lead_months=None, exposures=[_exposure("ZZZZ")]
    )
    results = run_layer_b([constraint], metrics_by_ticker={})
    assert results[0].score is None
    assert "earnings_torque" in results[0].reason


def test_present_deficit_pct_with_torque_scores_normally_even_when_other_constraint_is_null():
    """A null deficit_pct on one constraint must not affect scoring of a
    different constraint with a real deficit_pct."""
    null_constraint = _constraint(
        id="null_one", deficit_pct=None, expansion_lead_months=None,
        exposures=[_exposure("TROX")],
    )
    real_constraint = _constraint(
        id="real_one", deficit_pct=0.05, expansion_lead_months=18.0,
        exposures=[_exposure("WDC")],
    )
    metrics_by_ticker = {
        "TROX": _metrics(earnings_torque=0.5),
        "WDC": _metrics(earnings_torque=0.5),
    }
    results = run_layer_b([null_constraint, real_constraint], metrics_by_ticker)
    trox = next(r for r in results if r.ticker == "TROX")
    wdc = next(r for r in results if r.ticker == "WDC")
    assert trox.score is None
    assert wdc.score is not None


# --- ticker matching is case-insensitive -------------------------------------


def test_ticker_matching_is_case_insensitive():
    constraint = _constraint(exposures=[_exposure("wdc")])
    metrics_by_ticker = {"WDC": _metrics(earnings_torque=0.5)}
    results = run_layer_b([constraint], metrics_by_ticker)
    assert len(results) == 1
    assert results[0].ticker == "WDC"
    assert results[0].score is not None


# --- multiple exposures per constraint, multiple constraints ----------------


def test_multiple_exposures_and_constraints_all_appear():
    c1 = _constraint(id="c1", exposures=[_exposure("WDC"), _exposure("MU")])
    c2 = _constraint(id="c2", exposures=[_exposure("WDC")])
    metrics_by_ticker = {"WDC": _metrics(earnings_torque=0.3), "MU": _metrics(earnings_torque=0.2)}
    results = run_layer_b([c1, c2], metrics_by_ticker)
    assert len(results) == 3
    pairs = {(r.constraint_id, r.ticker) for r in results}
    assert pairs == {("c1", "WDC"), ("c1", "MU"), ("c2", "WDC")}
