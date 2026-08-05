"""Target weights, 5/25 bands and factor concentration."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.portfolio.policy import (
    Policy,
    bands_for,
    build_policy_view,
)
from app.portfolio.schema import PortfolioView, PositionView

AS_OF = datetime(2026, 8, 5, tzinfo=UTC)

POLICY = Policy(
    base_currency="EUR",
    target_weights={"CRWV": 0.06, "GOOGL": 0.13, "RHM.DE": 0.09},
    untargeted_status={"KEEL": "exit", "MSFT": "outside_target"},
    factors={
        "AI_PLATFORM": ["GOOGL", "MSFT"],
        "AI_CAPEX": ["CRWV", "KEEL"],
        "DEFENSE": ["RHM.DE"],
    },
    ai_factors=["AI_PLATFORM", "AI_CAPEX"],
    ai_target_max=0.65,
)


def _pos(ticker: str, eur: float | None, currency: str = "USD") -> PositionView:
    return PositionView(
        ticker=ticker,
        currency=currency,
        quantity=1.0,
        avg_cost=1.0,
        cost_basis=1.0,
        realized_pnl=0.0,
        first_traded_at=AS_OF,
        last_traded_at=AS_OF,
        trade_count=1,
        market_value=eur,
        market_value_eur=eur,
    )


def _view(*positions: PositionView) -> PortfolioView:
    return PortfolioView(open_positions=list(positions), closed_positions=[], as_of=AS_OF)


# --- bands ----------------------------------------------------------------


def test_relative_band_binds_below_a_twenty_percent_target():
    """A 6% target: absolute gives 1-11%, relative gives 4.5-7.5%. Tighter wins."""
    low, high = bands_for(0.06, POLICY)
    assert low == pytest.approx(0.045)
    assert high == pytest.approx(0.075)


def test_absolute_band_binds_on_a_large_target():
    """At a 40% target the 5pp band (35-45) is tighter than 25% relative (30-50)."""
    low, high = bands_for(0.40, POLICY)
    assert low == pytest.approx(0.35)
    assert high == pytest.approx(0.45)


# --- the spec's own validation oracle --------------------------------------


def test_crwv_above_its_band_is_flagged_for_trimming():
    """The check the brief named: CRWV near 11% against a 6% target must trim."""
    view = _view(_pos("CRWV", 110.0), _pos("GOOGL", 890.0))
    result = build_policy_view(view, policy=POLICY)
    crwv = next(p for p in result.positions if p.ticker == "CRWV")
    assert crwv.weight == pytest.approx(0.11)
    assert crwv.band_status == "trim"
    assert crwv.deviation_pp == pytest.approx(5.0)


def test_a_position_inside_its_band_is_not_flagged():
    view = _view(_pos("CRWV", 70.0), _pos("GOOGL", 930.0))
    result = build_policy_view(view, policy=POLICY)
    assert next(p for p in result.positions if p.ticker == "CRWV").band_status == "in_band"


def test_a_position_below_its_band_is_flagged_to_add():
    view = _view(_pos("CRWV", 30.0), _pos("GOOGL", 970.0))
    result = build_policy_view(view, policy=POLICY)
    assert next(p for p in result.positions if p.ticker == "CRWV").band_status == "add"


# --- untargeted holdings ---------------------------------------------------


def test_untargeted_positions_carry_a_status_not_a_deviation():
    view = _view(_pos("KEEL", 100.0), _pos("GOOGL", 900.0))
    result = build_policy_view(view, policy=POLICY)
    keel = next(p for p in result.positions if p.ticker == "KEEL")
    assert keel.band_status == "no_target"
    assert keel.target is None
    assert keel.deviation_pp is None
    assert keel.status == "exit"


def test_targets_you_hold_none_of_appear_as_fully_underweight():
    """While migrating toward the policy these are the entire buy order."""
    view = _view(_pos("GOOGL", 1000.0))
    result = build_policy_view(view, policy=POLICY)
    rhm = next(p for p in result.positions if p.ticker == "RHM.DE")
    assert rhm.weight == 0.0
    assert rhm.band_status == "add"
    assert rhm.deviation_pp == pytest.approx(-9.0)
    assert "RHM.DE" in result.missing_targets


def test_buy_order_is_most_underweight_first():
    """RHM.DE is unheld (-9pp), CRWV is 5pp over, GOOGL is 76pp over."""
    view = _view(_pos("CRWV", 110.0), _pos("GOOGL", 890.0))
    result = build_policy_view(view, policy=POLICY)
    assert [p.ticker for p in result.most_underweight] == ["RHM.DE", "CRWV", "GOOGL"]


# --- factor concentration --------------------------------------------------


def test_ai_concentration_sums_the_ai_buckets():
    view = _view(_pos("GOOGL", 500.0), _pos("CRWV", 300.0), _pos("RHM.DE", 200.0))
    result = build_policy_view(view, policy=POLICY)
    assert result.ai_weight == pytest.approx(0.80)
    assert result.ai_excess_pp == pytest.approx(15.0)


def test_concentration_below_the_ceiling_reports_negative_excess():
    view = _view(_pos("GOOGL", 500.0), _pos("RHM.DE", 500.0))
    result = build_policy_view(view, policy=POLICY)
    assert result.ai_weight == pytest.approx(0.50)
    assert result.ai_excess_pp == pytest.approx(-15.0)


def test_cash_carries_weight_so_other_weights_are_not_inflated():
    view = _view(_pos("GOOGL", 900.0))
    result = build_policy_view(view, policy=POLICY, cash_base=100.0)
    googl = next(p for p in result.positions if p.ticker == "GOOGL")
    assert googl.weight == pytest.approx(0.90)
    assert result.total_base == pytest.approx(1000.0)


# --- the mixed-currency guard ---------------------------------------------


def test_weights_are_withheld_when_any_position_lacks_a_base_value():
    """One denominator or none: a weight over a subset is a wrong number."""
    view = _view(_pos("GOOGL", 900.0), _pos("RHM.DE", None))
    result = build_policy_view(view, policy=POLICY)
    assert result.available is False
    assert "RHM.DE" in (result.reason or "")
    assert result.positions == []


def test_empty_portfolio_is_unavailable_not_zero():
    result = build_policy_view(_view(), policy=POLICY)
    assert result.available is False
    assert result.ai_weight == 0.0
