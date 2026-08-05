"""PanelDigest: flat, unit-tagged, citable panel measures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.analysis.digest import Measure, PanelDigest, build_panel_digest, render_measure
from app.analysis.schema import (
    ActionSignal,
    DecisionSupportStatus,
    EntryZoneCandidate,
    IdentityCoverage,
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
    SetupClassification,
    StyleFitPanel,
    TickerResearchView,
    TranscriptContext,
    ValuationPanel,
)

AS_OF = datetime(2026, 8, 5, tzinfo=UTC)


def _complete_view(**overrides):
    """Build a fully-populated TickerResearchView.

    `TickerResearchView` is the FastAPI response_model for
    `GET /tickers/{ticker}/research`; `identity`/`setup`/`style_fit`/
    `status`/`action`/`entry_zone`/`transcript`/`indicators`/`levels` are
    all required with no defaults on purpose — they are always computed by
    the pipeline, so a caller that fails to populate one should raise
    rather than silently serialize a placeholder. This fixture supplies
    minimal-but-valid instances for all of them so digest tests can focus
    on `market`/`valuation`/`indicators`/`levels`, the only panels the
    digest actually reads.
    """
    kwargs = dict(
        ticker="NVDA",
        as_of=AS_OF,
        identity=IdentityCoverage(
            ticker="NVDA",
            in_universe=True,
            has_transcript_signals=False,
            has_extracted_calls=False,
            has_extracted_claims=False,
            n_bars_loaded=300,
            enough_history_for_full_analysis=True,
        ),
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
        setup=SetupClassification(setup_type="breakout_candidate", confidence="medium"),
        style_fit=StyleFitPanel(),
        status=DecisionSupportStatus(status="watch", confidence="medium", summary="test fixture"),
        action=ActionSignal(label="HOLD", confidence="medium", derivation="test fixture"),
        entry_zone=EntryZoneCandidate(available=False),
        transcript=TranscriptContext(has_data=False, n_signals=0, n_calls=0, n_claims=0),
    )
    kwargs.update(overrides)
    return TickerResearchView(**kwargs)


def _view(**overrides):
    return _complete_view(**overrides)


def _empty_view(ticker: str) -> TickerResearchView:
    """A complete view whose data-bearing panels (market/valuation/indicators/
    levels) are all bare, so the digest has nothing to report on those axes,
    while the structural panels (identity/setup/.../transcript) are still
    fully populated, exactly as the real pipeline always produces them."""
    return _complete_view(
        ticker=ticker,
        market=MarketSnapshotPanel(as_of=AS_OF),
        valuation=ValuationPanel(),
        indicators=IndicatorPanel(),
        levels=LevelsPanel(),
    )


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
    """`_empty_view` bare-constructs every data-bearing panel, but
    `IndicatorPanel()` defaults `ma_alignment` to the real categorical value
    "insufficient" rather than None — that field is legitimately present, not
    missing, so it is the one measure a "fully empty" view still produces.
    Every other field on the bare panels defaults to None and lands in
    `missing`.
    """
    view = _empty_view("EMPTY")
    d = build_panel_digest(view)
    assert [m.field for m in d.measures] == ["indicators.ma_alignment"]
    assert d.measures[0].value == "insufficient"
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
    view = _empty_view("EMPTY")
    block = build_panel_digest(view).valuation_block()
    assert "no valuation data" in block.lower()


def test_measure_rejects_an_unknown_unit():
    import pydantic
    import pytest

    with pytest.raises(pydantic.ValidationError):
        Measure(field="x.y", value=1.0, unit="furlongs", as_of=AS_OF)


def test_dividend_yield_pre_multiplied_form_passes_through_unchanged():
    """0.38 already reads as 0.38% — the common yfinance form — so the
    normaliser must leave it alone."""
    d = build_panel_digest(_view(valuation=ValuationPanel(dividend_yield=0.38, fetched_at=AS_OF)))
    m = d.by_field()["valuation.dividend_yield"]
    assert m.value == 0.38
    assert m.unit == "percent"


def test_dividend_yield_fraction_form_is_scaled_to_percent():
    """Some tickers return dividendYield as a bare fraction (0.0038 == 0.38%).
    The normaliser must catch that case so the "percent" tag stays true."""
    d = build_panel_digest(_view(valuation=ValuationPanel(dividend_yield=0.0038, fetched_at=AS_OF)))
    m = d.by_field()["valuation.dividend_yield"]
    assert m.value == pytest.approx(0.38)
    assert m.unit == "percent"


def test_dividend_yield_zero_is_left_at_zero():
    """Guard the `0 <` bound in the normaliser: a zero yield must not be
    scaled (0 * 100 is still 0, but the guard exists so this stays explicit).
    `_collect` only treats None/blank-string as missing, so a real 0.0 value
    is collected as a measure, not dropped into `missing`."""
    d = build_panel_digest(_view(valuation=ValuationPanel(dividend_yield=0.0, fetched_at=AS_OF)))
    by_field = d.by_field()
    assert "valuation.dividend_yield" in by_field
    m = by_field["valuation.dividend_yield"]
    assert m.value == 0.0
    assert m.unit == "percent"


def test_dividend_yield_normaliser_does_not_affect_other_fields():
    """The normaliser map is keyed by dotted path, so a coincidentally
    small value on an unrelated field must not be rescaled."""
    d = build_panel_digest(
        _view(valuation=ValuationPanel(revenue_growth_yoy=0.001, fetched_at=AS_OF))
    )
    m = d.by_field()["valuation.revenue_growth_yoy"]
    assert m.value == 0.001
    assert m.unit == "fraction"


def test_render_measure_matches_across_valuation_and_peer_surfaces():
    """`render_measure` is the one canonical formatter — a float-valued
    valuation measure and a float-valued peer measure must render with the
    identical convention (used by both `valuation_block()` and the judge's
    peer loop)."""
    valuation_measure = Measure(
        field="valuation.forward_pe", value=41.0, unit="ratio", as_of=AS_OF
    )
    peer_measure = Measure(
        field="peers.median_forward_pe", value=18.0, unit="ratio", as_of=AS_OF
    )
    assert render_measure(valuation_measure) == "- forward_pe: 41.0 (ratio)"
    assert render_measure(peer_measure) == "- median_forward_pe: 18.0 (ratio)"


def test_digest_is_json_serialisable():
    d = build_panel_digest(_view())
    assert PanelDigest.model_validate_json(d.model_dump_json()).ticker == "NVDA"
