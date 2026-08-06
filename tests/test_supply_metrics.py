"""Tests for Layer A screening metrics (Task 3).

Every formula here is pinned exactly as specified in the task brief. Do not
adjust an assertion to make a "nicer" number pass — if a formula looks wrong,
fix the production code or ask, don't massage the test.
"""

from __future__ import annotations

import pytest

from app.supply.fundamentals import MarginHistory
from app.supply.metrics import (
    MIN_QUARTERS,
    SupplyMetrics,
    Thresholds,
    compute_metrics,
    load_thresholds,
    passes,
)

# 24 quarters (the MIN_QUARTERS floor), four repeated margin levels so the
# percentile and headroom math is easy to hand-verify:
#   6x 0.10, 6x 0.20, 6x 0.30, 6x 0.40
SUFFICIENT_MARGINS = [0.10] * 6 + [0.20] * 6 + [0.30] * 6 + [0.40] * 6


def _margin_history(margins: list[float]) -> MarginHistory:
    return MarginHistory(
        quarters=len(margins),
        gross_margins=margins,
        revenues=[1_000.0] * len(margins),
        first_end=None,
        last_end=None,
    )


def _compute(
    margins: list[float],
    *,
    ttm_gross_margin: float = 0.25,
    ttm_revenue: float = 1_000.0,
    market_cap: float = 2_000.0,
    ppe: float = 500.0,
    cash: float = 100.0,
    ttm_operating_cash_flow: float = 50.0,
    quarterly_operating_cash_flows: list[float] | None = None,
    analyst_count: int | None = 5,
    thresholds: Thresholds | None = None,
) -> SupplyMetrics:
    return compute_metrics(
        _margin_history(margins),
        ttm_gross_margin=ttm_gross_margin,
        ttm_revenue=ttm_revenue,
        market_cap=market_cap,
        ppe=ppe,
        cash=cash,
        ttm_operating_cash_flow=ttm_operating_cash_flow,
        quarterly_operating_cash_flows=quarterly_operating_cash_flows or [10.0, 20.0],
        analyst_count=analyst_count,
        thresholds=thresholds,
    )


def test_earnings_torque_is_headroom_times_revenue_over_market_cap():
    """20pp of headroom on 1000 revenue against a 2000 market cap returns
    0.10 of market cap in annual gross profit: 0.20 * 1000 / 2000 == 0.10."""
    margins = [0.40] * 24  # flat history: peak == 0.40
    metrics = _compute(margins, ttm_gross_margin=0.20, ttm_revenue=1_000.0, market_cap=2_000.0)
    assert metrics.margin_headroom_pp == pytest.approx(20.0)
    assert metrics.earnings_torque == pytest.approx(0.10)


def test_percentile_places_current_margin_within_its_own_history():
    """12 of the 24 historical quarters (the 0.10 and 0.20 bands) sit below
    a current TTM GM of 0.25 -> percentile 0.5."""
    metrics = _compute(SUFFICIENT_MARGINS, ttm_gross_margin=0.25)
    assert metrics.gm_percentile == pytest.approx(0.5)


def test_headroom_is_zero_at_the_historical_peak():
    metrics = _compute(SUFFICIENT_MARGINS, ttm_gross_margin=0.40)
    assert metrics.margin_headroom_pp == pytest.approx(0.0)


def test_survivability_is_infinite_for_a_cash_generative_company():
    """No negative quarterly OCF at all -> not burning -> infinite runway,
    represented as None (documented sentinel), never 0 or a ZeroDivisionError."""
    metrics = _compute(
        SUFFICIENT_MARGINS,
        quarterly_operating_cash_flows=[10.0, 20.0, 15.0, 30.0],
        ttm_operating_cash_flow=75.0,
        cash=100.0,
    )
    assert metrics.survivability_quarters is None


def test_survivability_counts_quarters_of_runway_when_burning():
    """burn = mean(negative quarters) = mean(-10, -20, -30) = -20, magnitude 20.
    ttm_ocf = -55 -> max(ttm_ocf, 0) = 0. (cash=100 + 0) / 20 == 5.0 quarters."""
    metrics = _compute(
        SUFFICIENT_MARGINS,
        quarterly_operating_cash_flows=[-10.0, -20.0, -30.0, 5.0],
        ttm_operating_cash_flow=-55.0,
        cash=100.0,
    )
    assert metrics.survivability_quarters == pytest.approx(5.0)


def test_a_short_history_is_rejected_rather_than_percentiled():
    """Fewer than MIN_QUARTERS makes a percentile meaningless. SanDisk has 12
    quarters and would otherwise score against its own two-year window."""
    short_margins = [0.10, 0.20, 0.30] * 4  # 12 quarters
    assert len(short_margins) < MIN_QUARTERS
    metrics = _compute(short_margins)

    assert metrics.sufficient_history is False
    assert metrics.quarters_of_history == 12
    assert metrics.gm_percentile is None
    assert metrics.margin_headroom_pp is None
    assert metrics.earnings_torque is None
    assert metrics.gm_volatility_pp is None

    thresholds = load_thresholds()
    ok, reasons = passes(metrics, thresholds)
    assert ok is False
    assert any("insufficient_history" in r for r in reasons)
    # Refusal is explicit and singular, not silently mixed with other reasons.
    assert len(reasons) == 1


def test_min_quarters_history_is_read_from_thresholds_not_hardcoded():
    """The YAML value must be the actual gate, not just message decoration.
    A 26-quarter history clears the module default (MIN_QUARTERS=24) but
    must be refused once min_quarters_history is configured to 30."""
    margins = SUFFICIENT_MARGINS + [0.25, 0.25]  # 26 quarters
    assert len(margins) == 26
    assert len(margins) > MIN_QUARTERS

    thresholds = Thresholds(
        gm_percentile_max=0.40,
        margin_headroom_min_pp=10.0,
        earnings_torque_min=0.25,
        gm_volatility_min_pp=5.0,
        capital_intensity_min=0.5,
        survivability_quarters_min=8,
        analyst_count_max=12,
        min_quarters_history=30,
    )
    metrics = compute_metrics(
        _margin_history(margins),
        ttm_gross_margin=0.25,
        ttm_revenue=1_000.0,
        market_cap=2_000.0,
        ppe=500.0,
        cash=100.0,
        ttm_operating_cash_flow=50.0,
        quarterly_operating_cash_flows=[10.0, 20.0],
        analyst_count=5,
        thresholds=thresholds,
    )

    assert metrics.sufficient_history is False
    assert metrics.quarters_of_history == 26
    assert metrics.gm_percentile is None

    ok, reasons = passes(metrics, thresholds)
    assert ok is False
    assert any("insufficient_history" in r for r in reasons)


def test_passes_returns_every_failing_reason_not_just_the_first():
    """A near-miss list is only useful if every failing criterion shows up."""
    thresholds = Thresholds(
        gm_percentile_max=0.40,
        margin_headroom_min_pp=10.0,
        earnings_torque_min=0.25,
        gm_volatility_min_pp=5.0,
        capital_intensity_min=0.5,
        survivability_quarters_min=8,
        analyst_count_max=12,
        min_quarters_history=24,
    )
    metrics = SupplyMetrics(
        quarters_of_history=30,
        sufficient_history=True,
        gm_percentile=0.90,  # fails: > 0.40
        margin_headroom_pp=2.0,  # fails: < 10.0
        earnings_torque=0.01,  # fails: < 0.25
        gm_volatility_pp=1.0,  # fails: < 5.0
        capital_intensity=0.1,  # fails: < 0.5
        survivability_quarters=2.0,  # fails: < 8
        analyst_count=50,  # fails: > 12
    )
    ok, reasons = passes(metrics, thresholds)
    assert ok is False
    assert len(reasons) == 7


def test_passes_true_when_every_metric_clears_its_threshold():
    thresholds = Thresholds(
        gm_percentile_max=0.40,
        margin_headroom_min_pp=10.0,
        earnings_torque_min=0.25,
        gm_volatility_min_pp=5.0,
        capital_intensity_min=0.5,
        survivability_quarters_min=8,
        analyst_count_max=12,
        min_quarters_history=24,
    )
    metrics = SupplyMetrics(
        quarters_of_history=30,
        sufficient_history=True,
        gm_percentile=0.10,
        margin_headroom_pp=20.0,
        earnings_torque=0.30,
        gm_volatility_pp=8.0,
        capital_intensity=0.8,
        survivability_quarters=None,  # infinite -> always clears
        analyst_count=3,
    )
    ok, reasons = passes(metrics, thresholds)
    assert ok is True
    assert reasons == []


def test_missing_analyst_count_does_not_fail_but_is_caveated():
    """An uncovered ticker is exactly the kind of name this screen hunts
    for — Layer A is a coarse net Layer B refines. Missing coverage must
    not silently sink the row; it must show up as a caveat instead.

    Every other criterion is tuned to exactly clear its threshold so a
    failure here can only come from the (missing) analyst_count check."""
    thresholds = load_thresholds()
    metrics = _compute(
        SUFFICIENT_MARGINS,
        ttm_gross_margin=0.15,  # percentile 6/24=0.25 <= max 0.40
        ttm_revenue=1_000.0,
        market_cap=1_000.0,  # headroom 25pp * 1000 / 1000 = 0.25 == torque_min
        ppe=500.0,  # capital_intensity 0.5 == min
        analyst_count=None,
    )

    assert metrics.analyst_count is None
    assert any("analyst_count" in c for c in metrics.caveats)

    ok, reasons = passes(metrics, thresholds)
    assert ok is True
    assert reasons == []


def test_analyst_count_present_and_above_threshold_still_fails():
    thresholds = load_thresholds()
    metrics = _compute(SUFFICIENT_MARGINS, analyst_count=50)

    assert metrics.caveats == []  # coverage IS known, nothing to caveat
    ok, reasons = passes(metrics, thresholds)
    assert ok is False
    assert any("analyst_count" in r for r in reasons)


def test_analyst_count_present_and_within_threshold_has_no_caveat():
    metrics = _compute(SUFFICIENT_MARGINS, analyst_count=3)
    assert metrics.caveats == []


def test_thresholds_come_from_config_not_from_code():
    thresholds = load_thresholds()
    assert thresholds.gm_percentile_max == pytest.approx(0.40)
    assert thresholds.margin_headroom_min_pp == pytest.approx(10.0)
    assert thresholds.earnings_torque_min == pytest.approx(0.25)
    assert thresholds.gm_volatility_min_pp == pytest.approx(5.0)
    assert thresholds.capital_intensity_min == pytest.approx(0.5)
    assert thresholds.survivability_quarters_min == pytest.approx(8)
    assert thresholds.analyst_count_max == 12
    assert thresholds.min_quarters_history == 24


def test_thresholds_load_from_an_arbitrary_config_path(tmp_path):
    """Proves thresholds are genuinely data-driven, not hardcoded with a
    yaml-shaped facade."""
    custom = tmp_path / "custom_screen.yaml"
    custom.write_text(
        "gm_percentile_max: 0.55\n"
        "margin_headroom_min_pp: 3.0\n"
        "earnings_torque_min: 0.05\n"
        "gm_volatility_min_pp: 1.0\n"
        "capital_intensity_min: 0.1\n"
        "survivability_quarters_min: 2\n"
        "analyst_count_max: 40\n"
        "min_quarters_history: 24\n"
    )
    thresholds = load_thresholds(custom)
    assert thresholds.gm_percentile_max == pytest.approx(0.55)
    assert thresholds.analyst_count_max == 40


def test_capital_intensity_is_ppe_over_ttm_revenue():
    metrics = _compute(SUFFICIENT_MARGINS, ppe=500.0, ttm_revenue=1_000.0)
    assert metrics.capital_intensity == pytest.approx(0.5)


def test_gm_volatility_is_population_stdev_in_percentage_points():
    import statistics

    metrics = _compute(SUFFICIENT_MARGINS, ttm_gross_margin=0.25)
    expected_pp = statistics.pstdev(SUFFICIENT_MARGINS) * 100
    assert metrics.gm_volatility_pp == pytest.approx(expected_pp)
