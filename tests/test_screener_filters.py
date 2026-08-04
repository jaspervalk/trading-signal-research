"""Unit tests for each screener filter class with known-value fixtures."""

from __future__ import annotations

import pytest

from app.screener.filters import (
    GrowthFilter,
    QualityFilter,
    TechnicalFilter,
    ValuationFilter,
    compute_flags,
)
from app.screener.schema import ScreenConfig, TickerMetrics


def _m(**overrides) -> TickerMetrics:
    return TickerMetrics(ticker="TEST", **overrides)


# ---------------------------------------------------------------------------
# Valuation


def test_valuation_passes_when_forward_pe_below_threshold():
    f = ValuationFilter(ScreenConfig())
    r = f.evaluate(_m(forward_pe=18.0))
    assert r.passed
    assert any("fwd_pe=18.0" in reason for reason in r.reasons)


def test_valuation_passes_on_peg_alone():
    f = ValuationFilter(ScreenConfig())
    r = f.evaluate(_m(forward_pe=40.0, peg_ratio=1.1))
    assert r.passed


def test_valuation_passes_on_ev_ebitda_alone():
    f = ValuationFilter(ScreenConfig())
    r = f.evaluate(_m(forward_pe=40.0, peg_ratio=3.0, ev_to_ebitda=12.0))
    assert r.passed


def test_valuation_fails_when_all_metrics_too_rich():
    f = ValuationFilter(ScreenConfig())
    r = f.evaluate(_m(forward_pe=40.0, peg_ratio=3.0, ev_to_ebitda=22.0))
    assert not r.passed


def test_valuation_returns_insufficient_data_when_all_missing():
    f = ValuationFilter(ScreenConfig())
    r = f.evaluate(_m())
    assert not r.passed
    assert r.reasons == ["insufficient_data"]


def test_valuation_respects_override_threshold():
    f = ValuationFilter(ScreenConfig(max_forward_pe=10.0))
    r = f.evaluate(_m(forward_pe=18.0))
    assert not r.passed
    f2 = ValuationFilter(ScreenConfig(max_forward_pe=30.0))
    r2 = f2.evaluate(_m(forward_pe=18.0))
    assert r2.passed


# ---------------------------------------------------------------------------
# Growth


def test_growth_passes_with_rev_and_positive_earnings():
    f = GrowthFilter(ScreenConfig())
    r = f.evaluate(_m(revenue_growth_yoy=0.20, earnings_growth_yoy=0.10))
    assert r.passed


def test_growth_passes_when_earnings_inflecting_via_qoq():
    f = GrowthFilter(ScreenConfig())
    # YoY earnings negative but QoQ revenue acceleration > YoY → inflecting
    r = f.evaluate(
        _m(
            revenue_growth_yoy=0.18,
            revenue_growth_qoq=0.25,
            earnings_growth_yoy=-0.05,
        )
    )
    assert r.passed


def test_growth_fails_when_revenue_too_slow():
    f = GrowthFilter(ScreenConfig())
    r = f.evaluate(_m(revenue_growth_yoy=0.05, earnings_growth_yoy=0.10))
    assert not r.passed


def test_growth_insufficient_data_when_revenue_missing():
    f = GrowthFilter(ScreenConfig())
    r = f.evaluate(_m(earnings_growth_yoy=0.10))
    assert not r.passed
    assert r.reasons == ["insufficient_data"]


# ---------------------------------------------------------------------------
# Quality


def test_quality_passes_when_all_three_metrics_meet():
    f = QualityFilter(ScreenConfig())
    r = f.evaluate(_m(gross_margin=0.55, roe=0.22, debt_to_equity=0.4))
    assert r.passed


def test_quality_fails_on_any_violation():
    f = QualityFilter(ScreenConfig())
    assert not f.evaluate(
        _m(gross_margin=0.30, roe=0.22, debt_to_equity=0.4)
    ).passed
    assert not f.evaluate(
        _m(gross_margin=0.55, roe=0.10, debt_to_equity=0.4)
    ).passed
    assert not f.evaluate(
        _m(gross_margin=0.55, roe=0.22, debt_to_equity=1.5)
    ).passed


def test_quality_insufficient_data_when_any_missing():
    f = QualityFilter(ScreenConfig())
    r = f.evaluate(_m(gross_margin=0.55, roe=0.22))
    assert not r.passed
    assert r.reasons == ["insufficient_data"]


# ---------------------------------------------------------------------------
# Technical


def test_technical_passes_when_uptrend_and_rs_positive():
    f = TechnicalFilter(ScreenConfig())
    r = f.evaluate(_m(pct_vs_200d=0.10, rs_vs_spy_3mo=0.05))
    assert r.passed


def test_technical_fails_when_extended_above_30pct():
    f = TechnicalFilter(ScreenConfig())
    r = f.evaluate(_m(pct_vs_200d=0.42, rs_vs_spy_3mo=0.05))
    assert not r.passed


def test_technical_fails_when_below_200d():
    f = TechnicalFilter(ScreenConfig())
    r = f.evaluate(_m(pct_vs_200d=-0.02, rs_vs_spy_3mo=0.05))
    assert not r.passed


def test_technical_fails_when_rs_negative():
    f = TechnicalFilter(ScreenConfig())
    r = f.evaluate(_m(pct_vs_200d=0.10, rs_vs_spy_3mo=-0.02))
    assert not r.passed


# ---------------------------------------------------------------------------
# Flags (informational, never gating)


def test_flag_cheap_for_a_reason():
    flags = compute_flags(
        _m(forward_pe=12.0, earnings_growth_yoy=-0.20), ScreenConfig()
    )
    assert "cheap_for_a_reason" in flags


def test_flag_earnings_proximity():
    flags = compute_flags(_m(days_to_earnings=12), ScreenConfig())
    assert any(f.startswith("earnings_in_") for f in flags)


def test_flag_extended_above_200d_is_informational():
    flags = compute_flags(_m(pct_vs_200d=0.45), ScreenConfig())
    assert "extended_above_200d" in flags


def test_no_flags_when_metrics_clean():
    flags = compute_flags(
        _m(forward_pe=18.0, earnings_growth_yoy=0.20, pct_vs_200d=0.10),
        ScreenConfig(),
    )
    assert flags == []
