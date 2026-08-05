"""PanelDigest: flat, unit-tagged, citable panel measures."""

from __future__ import annotations

from datetime import UTC, datetime

from app.analysis.digest import Measure, PanelDigest, build_panel_digest
from app.analysis.schema import (
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
    TickerResearchView,
    ValuationPanel,
)

AS_OF = datetime(2026, 8, 5, tzinfo=UTC)


def _view(**overrides):
    kwargs = dict(
        ticker="NVDA",
        as_of=AS_OF,
        market=MarketSnapshotPanel(as_of=AS_OF, last_close=100.0, return_21d=0.12),
        valuation=ValuationPanel(
            forward_pe=30.0,
            revenue_growth_yoy=0.25,
            dividend_yield=0.38,
            market_cap=1.0e12,
            sector="Technology",
            fetched_at=AS_OF,
        ),
        indicators=IndicatorPanel(rsi_14=71.0, atr_14=3.5),
        levels=LevelsPanel(nearest_support=95.0),
    )
    kwargs.update(overrides)
    return TickerResearchView(**kwargs)


def test_digest_is_flat_and_citable():
    d = build_panel_digest(_view())
    by_field = {m.field: m for m in d.measures}
    assert "valuation.forward_pe" in by_field
    assert by_field["valuation.forward_pe"].value == 30.0
    assert d.ticker == "NVDA"


def test_dividend_yield_is_tagged_percent_not_fraction():
    """yfinance pre-multiplies dividendYield; every other rate is a fraction.
    A model told 'fraction' would render 0.38% as 38%."""
    d = build_panel_digest(_view())
    by_field = {m.field: m for m in d.measures}
    assert by_field["valuation.dividend_yield"].unit == "percent"
    assert by_field["valuation.revenue_growth_yoy"].unit == "fraction"


def test_units_are_assigned_per_field():
    d = build_panel_digest(_view())
    by_field = {m.field: m for m in d.measures}
    assert by_field["valuation.forward_pe"].unit == "ratio"
    assert by_field["valuation.market_cap"].unit == "usd"
    assert by_field["indicators.rsi_14"].unit == "index_0_100"
    assert by_field["indicators.atr_14"].unit == "price"
    assert by_field["market.return_21d"].unit == "fraction"
    assert by_field["valuation.sector"].unit == "categorical"


def test_missing_fields_are_named_not_silently_dropped():
    d = build_panel_digest(_view())
    assert "valuation.trailing_pe" in d.missing
    assert all(m.field != "valuation.trailing_pe" for m in d.measures)


def test_every_measure_carries_a_timezone_aware_as_of():
    d = build_panel_digest(_view())
    assert d.measures
    for m in d.measures:
        assert m.as_of.tzinfo is not None


def test_valuation_measures_inherit_panel_fetched_at():
    stamp = datetime(2026, 8, 1, tzinfo=UTC)
    d = build_panel_digest(_view(valuation=ValuationPanel(forward_pe=30.0, fetched_at=stamp)))
    by_field = {m.field: m for m in d.measures}
    assert by_field["valuation.forward_pe"].as_of == stamp


def test_valuation_without_fetched_at_falls_back_to_view_as_of():
    d = build_panel_digest(_view(valuation=ValuationPanel(forward_pe=30.0)))
    by_field = {m.field: m for m in d.measures}
    assert by_field["valuation.forward_pe"].as_of == AS_OF


def test_digest_handles_a_fully_empty_view():
    view = TickerResearchView(ticker="EMPTY", as_of=AS_OF, market=MarketSnapshotPanel(as_of=AS_OF))
    d = build_panel_digest(view)
    assert d.measures == [] or all(m.value is not None for m in d.measures)
    assert "valuation.forward_pe" in d.missing


def test_valuation_block_renders_only_present_fields():
    block = build_panel_digest(_view()).valuation_block()
    assert "forward_pe" in block
    assert "30.0" in block
    assert "trailing_pe" not in block


def test_valuation_block_states_units_and_age():
    block = build_panel_digest(_view()).valuation_block()
    assert "percent" in block or "%" in block
    assert "as of" in block.lower() or "fetched" in block.lower()


def test_valuation_block_is_explicit_when_nothing_is_available():
    view = TickerResearchView(ticker="EMPTY", as_of=AS_OF, market=MarketSnapshotPanel(as_of=AS_OF))
    block = build_panel_digest(view).valuation_block()
    assert "no valuation data" in block.lower()


def test_measure_rejects_an_unknown_unit():
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        Measure(field="x.y", value=1.0, unit="furlongs", as_of=AS_OF)


def test_digest_is_json_serialisable():
    d = build_panel_digest(_view())
    assert PanelDigest.model_validate_json(d.model_dump_json()).ticker == "NVDA"
