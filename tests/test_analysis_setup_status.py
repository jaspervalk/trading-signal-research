"""Unit tests for setup classifier + decision-support status rubric.

Each test pins a specific (indicator, level, market) combination and
asserts the classifier picks the right setup_type and the rubric
arrives at the right status.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.analysis.entry import build_entry_zone
from app.analysis.schema import (
    IdentityCoverage,
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
)
from app.analysis.setup import classify_setup
from app.analysis.status import derive_status
from app.analysis.style import evaluate_style_fit


def _identity(ticker: str = "TEST", n_bars: int = 300) -> IdentityCoverage:
    return IdentityCoverage(
        ticker=ticker,
        in_universe=True,
        has_transcript_signals=False,
        has_extracted_calls=False,
        has_extracted_claims=False,
        n_bars_loaded=n_bars,
        enough_history_for_full_analysis=n_bars >= 252,
    )


def _market(price: float = 100.0, dollar_volume: float = 50_000_000) -> MarketSnapshotPanel:
    return MarketSnapshotPanel(
        as_of=datetime(2026, 5, 6, tzinfo=UTC),
        last_close=price,
        daily_volume=int(dollar_volume / price),
        avg_volume_20d=int(dollar_volume / price),
        dollar_volume=dollar_volume,
    )


# ---------------------------------------------------------------------------
# Setup classifier branches


def test_setup_classifier_insufficient_data_when_not_enough_bars():
    indicators = IndicatorPanel()
    levels = LevelsPanel()
    market = _market()
    setup = classify_setup(indicators, levels, market, n_bars=100)
    assert setup.setup_type == "insufficient_data"
    assert setup.data_sufficiency == "insufficient"


def test_setup_classifier_low_liquidity_blocks_other_paths():
    indicators = IndicatorPanel(
        sma_50=100,
        sma_200=90,
        ma_alignment="bullish_stack",
        sma_50_slope_21d_pct=0.01,
        rsi_14=60,
        atr_14_pct=0.02,
    )
    levels = LevelsPanel()
    market = _market(dollar_volume=1_000_000)  # below 5M threshold
    setup = classify_setup(indicators, levels, market, n_bars=300)
    assert setup.setup_type == "low_liquidity"


def test_setup_classifier_high_volatility_unstable():
    indicators = IndicatorPanel(
        sma_50=100,
        sma_200=90,
        ma_alignment="bullish_stack",
        atr_14_pct=0.12,  # 12% > 8% threshold
    )
    levels = LevelsPanel()
    market = _market()
    setup = classify_setup(indicators, levels, market, n_bars=300)
    assert setup.setup_type == "high_volatility_unstable"


def test_setup_classifier_strong_uptrend_with_positive_slope():
    indicators = IndicatorPanel(
        sma_50=100,
        sma_200=90,
        ma_alignment="bullish_stack",
        sma_50_slope_21d_pct=0.02,
        rsi_14=60,
        atr_14_pct=0.02,
        dist_to_sma_50_pct=0.05,  # not extended
    )
    levels = LevelsPanel(
        pullback_pct_from_recent_high=0.0,
        consolidation_range_pct=0.20,  # not tight
        breakout_distance_pct=-0.05,  # below recent high but not near it
    )
    market = _market()
    setup = classify_setup(indicators, levels, market, n_bars=300)
    assert setup.setup_type == "strong_uptrend"


def test_setup_classifier_uptrend_pullback():
    indicators = IndicatorPanel(
        sma_50=100,
        sma_200=90,
        ma_alignment="bullish_stack",
        sma_50_slope_21d_pct=0.02,
        rsi_14=55,
        atr_14_pct=0.02,
        dist_to_sma_50_pct=0.01,
    )
    levels = LevelsPanel(pullback_pct_from_recent_high=0.08)  # in [4%, 18%]
    market = _market()
    setup = classify_setup(indicators, levels, market, n_bars=300)
    assert setup.setup_type == "uptrend_pullback"


def test_setup_classifier_breakout_candidate_fresh_breakout():
    indicators = IndicatorPanel(
        sma_50=100,
        sma_200=90,
        ma_alignment="bullish_stack",
        sma_50_slope_21d_pct=0.01,
        rsi_14=65,
        atr_14_pct=0.02,
        dist_to_sma_50_pct=0.05,
    )
    levels = LevelsPanel(
        consolidation_range_pct=0.05,
        breakout_distance_pct=0.015,  # 1.5% above recent high — fresh breakout
        recent_high_63d=100,
        base_low=92,
    )
    market = _market()
    setup = classify_setup(indicators, levels, market, n_bars=300)
    assert setup.setup_type == "breakout_candidate"


def test_setup_classifier_extended_momentum():
    indicators = IndicatorPanel(
        sma_50=100,
        sma_200=90,
        ma_alignment="bullish_stack",
        sma_50_slope_21d_pct=0.02,
        rsi_14=80,
        atr_14_pct=0.02,
        dist_to_sma_50_pct=0.20,  # 20% above
    )
    levels = LevelsPanel()
    market = _market()
    setup = classify_setup(indicators, levels, market, n_bars=300)
    assert setup.setup_type == "extended_momentum"


def test_setup_classifier_strict_bearish_downtrend():
    indicators = IndicatorPanel(
        sma_50=90,
        sma_200=100,
        ma_alignment="bearish_stack",
        sma_50_slope_21d_pct=-0.02,
        sma_200_slope_63d_pct=-0.05,
        relative_strength_vs_spy_63d=-0.10,
        relative_strength_vs_spy_126d=-0.20,
        atr_14_pct=0.02,
    )
    levels = LevelsPanel()
    market = _market(price=80)
    setup = classify_setup(indicators, levels, market, n_bars=300)
    assert setup.setup_type == "downtrend"
    assert setup.confidence == "medium"  # strict bearish path


def test_setup_classifier_broken_above_50_downtrend():
    """Counter-trend bounce: price above 50d but below 200d, slopes negative, weak RS."""
    indicators = IndicatorPanel(
        sma_50=95,
        sma_200=110,
        ma_alignment="mixed",
        sma_50_slope_21d_pct=-0.01,
        sma_200_slope_63d_pct=-0.02,
        dist_to_sma_200_pct=-0.10,  # >3% below 200d
        relative_strength_vs_spy_126d=-0.20,
        atr_14_pct=0.02,
    )
    levels = LevelsPanel()
    market = _market(price=100)
    setup = classify_setup(indicators, levels, market, n_bars=300)
    assert setup.setup_type == "downtrend"
    assert setup.confidence == "low"  # broken-above-50 path


def test_setup_classifier_range_bound():
    indicators = IndicatorPanel(
        sma_50=100,
        sma_200=100,
        ma_alignment="mixed",
        atr_14_pct=0.02,
    )
    levels = LevelsPanel(consolidation_range_pct=0.05)
    market = _market()
    setup = classify_setup(indicators, levels, market, n_bars=300)
    assert setup.setup_type == "range_bound"


# ---------------------------------------------------------------------------
# Status rubric


def test_status_research_candidate_when_setup_and_rubric_aligned():
    indicators = IndicatorPanel(
        sma_50=100,
        sma_200=90,
        ma_alignment="bullish_stack",
        sma_50_slope_21d_pct=0.02,
        rsi_14=60,
        relative_strength_vs_spy_63d=0.05,
        relative_strength_vs_spy_126d=0.10,
        atr_14_pct=0.02,
        dist_to_sma_50_pct=0.05,
    )
    levels = LevelsPanel(
        consolidation_range_pct=0.05,
        breakout_distance_pct=0.015,
        recent_high_63d=100,
        is_in_tight_range=True,
        nearest_support=92,
        base_low=92,
    )
    market = _market()
    setup = classify_setup(indicators, levels, market, n_bars=300)
    style_fit = evaluate_style_fit(setup, indicators, levels)
    status = derive_status(setup, style_fit, indicators, market, levels, _identity())
    assert setup.setup_type == "breakout_candidate"
    assert status.status == "research_candidate"


def test_status_skip_for_now_for_downtrend():
    indicators = IndicatorPanel(
        sma_50=90,
        sma_200=100,
        ma_alignment="bearish_stack",
        sma_50_slope_21d_pct=-0.02,
        sma_200_slope_63d_pct=-0.05,
        relative_strength_vs_spy_126d=-0.20,
        atr_14_pct=0.02,
    )
    levels = LevelsPanel()
    market = _market(price=80)
    setup = classify_setup(indicators, levels, market, n_bars=300)
    style_fit = evaluate_style_fit(setup, indicators, levels)
    status = derive_status(setup, style_fit, indicators, market, levels, _identity())
    assert status.status == "skip_for_now"


def test_status_extended_risk_for_extended_momentum():
    indicators = IndicatorPanel(
        sma_50=100,
        sma_200=90,
        ma_alignment="bullish_stack",
        sma_50_slope_21d_pct=0.02,
        rsi_14=80,
        atr_14_pct=0.02,
        dist_to_sma_50_pct=0.20,
    )
    levels = LevelsPanel()
    market = _market()
    setup = classify_setup(indicators, levels, market, n_bars=300)
    style_fit = evaluate_style_fit(setup, indicators, levels)
    status = derive_status(setup, style_fit, indicators, market, levels, _identity())
    assert status.status == "extended_risk"


def test_status_insufficient_data_when_setup_says_so():
    indicators = IndicatorPanel()
    levels = LevelsPanel()
    market = _market()
    setup = classify_setup(indicators, levels, market, n_bars=100)
    style_fit = evaluate_style_fit(setup, indicators, levels)
    status = derive_status(setup, style_fit, indicators, market, levels, _identity(n_bars=100))
    assert status.status == "insufficient_data"


# ---------------------------------------------------------------------------
# Entry zone


def test_entry_zone_unavailable_for_downtrend():
    indicators = IndicatorPanel(atr_14=2.0)
    levels = LevelsPanel()
    market = _market(price=80)
    setup = classify_setup(
        IndicatorPanel(
            sma_50=90,
            sma_200=100,
            ma_alignment="bearish_stack",
            sma_50_slope_21d_pct=-0.02,
            sma_200_slope_63d_pct=-0.05,
            relative_strength_vs_spy_126d=-0.20,
            atr_14_pct=0.02,
        ),
        levels,
        market,
        n_bars=300,
    )
    e = build_entry_zone(setup, indicators, levels, market)
    assert e.available is False
    assert "long-only" in (e.reason_unavailable or "").lower()


def test_entry_zone_for_breakout_candidate_has_atr_anchored_invalidation():
    indicators = IndicatorPanel(
        atr_14=2.0,
        atr_14_pct=0.02,
        ema_21=99.0,
        sma_50=98.0,
    )
    levels = LevelsPanel(
        recent_high_63d=100.0,
        base_low=92.0,
        nearest_support=95.0,
        nearest_resistance=110.0,
    )
    market = _market(price=100.0)
    # Force breakout_candidate
    indicators_for_setup = IndicatorPanel(
        sma_50=98,
        sma_200=90,
        ma_alignment="bullish_stack",
        sma_50_slope_21d_pct=0.01,
        rsi_14=65,
        atr_14_pct=0.02,
        dist_to_sma_50_pct=0.02,
    )
    levels_for_setup = LevelsPanel(
        consolidation_range_pct=0.05,
        breakout_distance_pct=-0.01,
        is_in_tight_range=True,
        recent_high_63d=100,
        base_low=92,
    )
    setup = classify_setup(indicators_for_setup, levels_for_setup, market, n_bars=300)
    assert setup.setup_type == "breakout_candidate"

    e = build_entry_zone(setup, indicators, levels, market)
    assert e.available is True
    assert e.setup_trigger_level == pytest.approx(100.0)
    assert e.invalidation_reference is not None
    # Invalidation should be max(base_low, trigger - 1.5*ATR) = max(92, 97) = 97.
    assert e.invalidation_reference == pytest.approx(97.0)
    # R/R = (110 - 100) / (100 - 97) ≈ 3.33
    assert e.risk_reward_estimate == pytest.approx((110 - 100) / (100 - 97), rel=0.01)
