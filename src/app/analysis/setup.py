"""Rule-based setup classifier.

Maps `(IndicatorPanel, LevelsPanel, MarketSnapshotPanel)` → `SetupClassification`.

Rules are deliberately transparent and ordered. The classifier evaluates
explicit conditions and returns the first match. Each branch records
`reasons`, `counterarguments`, and the `supporting_metrics` it relied on
so the caller can show its work.

Setup taxonomy (V1, see schema.py for the canonical list):
- `strong_uptrend`: bullish MA stack, healthy slope, decent RS, not extended
- `uptrend_pullback`: bullish stack but price has pulled back near sma_50/ema_21
- `breakout_candidate`: tight base near recent highs, not yet broken out
- `extended_momentum`: bullish stack but price is far above the 50/200 SMAs
  AND RSI is high — late-stage move
- `range_bound`: no MA alignment, consolidation_range_pct small, no breakout
- `downtrend`: bearish stack, weak RS, slope negative
- `high_volatility_unstable`: ATR% high or realized vol high relative to typical
- `low_liquidity`: average dollar volume below threshold
- `insufficient_data`: not enough bars / NaN-heavy panel
- `unclear`: data is fine but rules disagree

Tunable thresholds live as module-level constants. Tweak with care; every
threshold change should be justified by either domain reasoning or
backtest data.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.schema import (
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
    SetupClassification,
)

# ---------------------------------------------------------------------------
# Thresholds (V1, hand-picked)


@dataclass(frozen=True)
class SetupThresholds:
    # Liquidity gate
    min_dollar_volume: float = 5_000_000.0
    # Extended-momentum gates
    extended_dist_to_sma_50_pct: float = 0.15  # >15% above 50dma
    extended_rsi: float = 75.0
    # High-volatility gate
    high_atr_pct: float = 0.08  # ATR(14)/price > 8% daily
    high_realized_vol_annualized: float = 0.80  # >80% annualized
    # Pullback gate
    pullback_min_pct: float = 0.04  # ≥4% off the 63d high
    pullback_max_pct: float = 0.18  # but not more than 18% (otherwise it's broken)
    # Breakout-candidate gate. Range covers both "near pivot below the recent
    # high" (still building) and "just broke out today" (fresh breakout).
    near_high_low_pct: float = -0.03  # within 3% below recent 63d high
    near_high_high_pct: float = 0.03  # or up to 3% above (fresh breakout)
    tight_consolidation_pct: float = 0.08  # 20-bar range / midprice ≤ 8%
    # Range-bound gate
    range_bound_consolidation_pct: float = 0.10
    # Trend slope gates
    uptrend_50_slope_min: float = 0.005  # +0.5% over 21 bars on the 50sma
    downtrend_50_slope_max: float = -0.005


DEFAULT_THRESHOLDS = SetupThresholds()


def _add_metric(d: dict, name: str, value):
    if value is not None:
        d[name] = value


def classify_setup(
    indicators: IndicatorPanel,
    levels: LevelsPanel,
    market: MarketSnapshotPanel,
    *,
    n_bars: int,
    thresholds: SetupThresholds = DEFAULT_THRESHOLDS,
) -> SetupClassification:
    """Run the rule cascade. Returns a `SetupClassification` with show-your-work fields."""
    reasons: list[str] = []
    counterarguments: list[str] = []
    metrics: dict = {}

    # --- 1. Sufficiency gate ---
    enough_history = n_bars >= 200 and indicators.sma_200 is not None
    if not enough_history:
        return SetupClassification(
            setup_type="insufficient_data",
            confidence="low",
            reasons=[
                f"Only {n_bars} bars available; need ≥200 to evaluate the long-trend stack.",
            ],
            counterarguments=[],
            supporting_metrics={"n_bars": n_bars},
            data_sufficiency="insufficient",
        )

    # --- 2. Liquidity gate ---
    if (
        market.dollar_volume is not None
        and market.dollar_volume < thresholds.min_dollar_volume
    ):
        return SetupClassification(
            setup_type="low_liquidity",
            confidence="medium",
            reasons=[
                f"Dollar volume {_fmt_dollars(market.dollar_volume)} below "
                f"{_fmt_dollars(thresholds.min_dollar_volume)} threshold.",
            ],
            counterarguments=[],
            supporting_metrics={
                "dollar_volume": market.dollar_volume,
                "avg_volume_20d": market.avg_volume_20d,
            },
            data_sufficiency="sufficient",
        )

    # --- 3. High-volatility / unstable gate ---
    if (
        indicators.atr_14_pct is not None
        and indicators.atr_14_pct > thresholds.high_atr_pct
    ) or (
        indicators.realized_vol_21d_annualized is not None
        and indicators.realized_vol_21d_annualized
        > thresholds.high_realized_vol_annualized
    ):
        return SetupClassification(
            setup_type="high_volatility_unstable",
            confidence="medium",
            reasons=[
                f"ATR(14) is {(indicators.atr_14_pct or 0) * 100:.1f}% of price "
                f"or realized vol is {(indicators.realized_vol_21d_annualized or 0) * 100:.0f}% "
                f"annualized — both stops and entries become wide.",
            ],
            counterarguments=[
                "High volatility on a strong base sometimes precedes large breakouts; rule out only after checking the chart.",
            ],
            supporting_metrics={
                "atr_14_pct": indicators.atr_14_pct,
                "realized_vol_21d_annualized": indicators.realized_vol_21d_annualized,
            },
            data_sufficiency="sufficient",
        )

    # --- 4. Trend direction (the big fork) ---
    is_bullish_stack = indicators.ma_alignment == "bullish_stack"
    is_bearish_stack = indicators.ma_alignment == "bearish_stack"
    slope_50 = indicators.sma_50_slope_21d_pct
    slope_200 = indicators.sma_200_slope_63d_pct

    _add_metric(metrics, "ma_alignment", indicators.ma_alignment)
    _add_metric(metrics, "sma_50_slope_21d_pct", slope_50)
    _add_metric(metrics, "sma_200_slope_63d_pct", slope_200)
    _add_metric(metrics, "rsi_14", indicators.rsi_14)
    _add_metric(metrics, "atr_14_pct", indicators.atr_14_pct)
    _add_metric(metrics, "dollar_volume", market.dollar_volume)
    _add_metric(metrics, "relative_strength_vs_spy_63d", indicators.relative_strength_vs_spy_63d)
    _add_metric(metrics, "relative_strength_vs_spy_126d", indicators.relative_strength_vs_spy_126d)
    _add_metric(metrics, "pullback_pct_from_recent_high", levels.pullback_pct_from_recent_high)
    _add_metric(metrics, "consolidation_range_pct", levels.consolidation_range_pct)
    _add_metric(metrics, "breakout_distance_pct", levels.breakout_distance_pct)

    # Downtrend — two ways to qualify:
    #   (a) strict bearish stack (price < 50 < 200) with negative 50d slope, OR
    #   (b) "broken" trend: price < 200d with both slopes negative and material
    #       RS underperformance, even if a counter-trend bounce has put price
    #       above the 50d. This catches the MSFT-style "weak with a bounce" case
    #       that mixed-stack rules silently fall through.
    is_strict_bearish = is_bearish_stack and (
        slope_50 is None or slope_50 < thresholds.downtrend_50_slope_max
    )
    is_broken_above_50 = (
        indicators.dist_to_sma_200_pct is not None
        and indicators.dist_to_sma_200_pct < -0.03
        and slope_200 is not None
        and slope_200 < -0.005
        and indicators.relative_strength_vs_spy_126d is not None
        and indicators.relative_strength_vs_spy_126d < -0.10
    )
    if is_strict_bearish or is_broken_above_50:
        if is_strict_bearish:
            reasons.append("MAs are stacked bearishly (price < 50 < 200).")
        else:
            reasons.append(
                f"Price is {(indicators.dist_to_sma_200_pct or 0) * 100:.1f}% below "
                "the 200-SMA with the 200-day slope still negative."
            )
        if slope_50 is not None:
            reasons.append(
                f"50-day slope is {slope_50 * 100:.1f}% over 21 bars — pointing down."
            )
        if (
            indicators.relative_strength_vs_spy_126d is not None
            and indicators.relative_strength_vs_spy_126d < 0
        ):
            reasons.append(
                f"Underperformed SPY by {abs(indicators.relative_strength_vs_spy_126d) * 100:.1f}% over 126 bars."
            )
        return SetupClassification(
            setup_type="downtrend",
            confidence="medium" if is_strict_bearish else "low",
            reasons=reasons,
            counterarguments=[
                "Downtrends end. Look for capitulation + base before re-engaging.",
                "Counter-trend bounces can be sharp; don't confuse a bounce for a regime change.",
            ]
            if not is_strict_bearish
            else ["Downtrends end. Look for capitulation + base before re-engaging."],
            supporting_metrics=metrics,
            data_sufficiency="sufficient",
        )

    # Uptrend family
    if is_bullish_stack:
        # Extended momentum: too far above 50dma and stretched RSI
        if (
            indicators.dist_to_sma_50_pct is not None
            and indicators.dist_to_sma_50_pct > thresholds.extended_dist_to_sma_50_pct
            and indicators.rsi_14 is not None
            and indicators.rsi_14 > thresholds.extended_rsi
        ):
            reasons.append(
                f"Price is {indicators.dist_to_sma_50_pct * 100:.0f}% above the 50-day MA — extended."
            )
            reasons.append(f"RSI(14) at {indicators.rsi_14:.0f} — overbought.")
            counterarguments.append(
                "Strong trends can stay extended longer than feels reasonable; not always a top."
            )
            return SetupClassification(
                setup_type="extended_momentum",
                confidence="medium",
                reasons=reasons,
                counterarguments=counterarguments,
                supporting_metrics=metrics,
                data_sufficiency="sufficient",
            )

        # Pullback inside an uptrend
        pullback = levels.pullback_pct_from_recent_high
        if (
            pullback is not None
            and thresholds.pullback_min_pct <= pullback <= thresholds.pullback_max_pct
        ):
            reasons.append(
                f"Bullish MA stack with a {pullback * 100:.1f}% pullback from the 63-bar high."
            )
            if slope_50 is not None and slope_50 > thresholds.uptrend_50_slope_min:
                reasons.append(
                    f"50-day slope {slope_50 * 100:.1f}% over 21 bars — uptrend still intact."
                )
            counterarguments.append(
                "Pullbacks of this depth sometimes precede a deeper correction; watch the 50-day MA hold."
            )
            return SetupClassification(
                setup_type="uptrend_pullback",
                confidence="medium",
                reasons=reasons,
                counterarguments=counterarguments,
                supporting_metrics=metrics,
                data_sufficiency="sufficient",
            )

        # Breakout candidate: near recent highs (or just over) + tight consolidation,
        # OR just-broken-out fresh from a recent base.
        breakout = levels.breakout_distance_pct
        is_tight = (
            levels.consolidation_range_pct is not None
            and levels.consolidation_range_pct <= thresholds.tight_consolidation_pct
        )
        is_near_high = (
            breakout is not None
            and thresholds.near_high_low_pct <= breakout <= thresholds.near_high_high_pct
        )
        is_fresh_breakout = (
            breakout is not None and 0 < breakout <= thresholds.near_high_high_pct
        )
        if (is_tight and is_near_high) or is_fresh_breakout:
            if is_fresh_breakout:
                reasons.append(
                    f"Fresh breakout: closed {breakout * 100:.1f}% above the 63-bar recent high."
                )
            else:
                reasons.append(
                    f"Within {abs((breakout or 0)) * 100:.1f}% of the 63-bar recent high — breakout setup."
                )
            if is_tight:
                reasons.append(
                    f"Tight consolidation ({(levels.consolidation_range_pct or 0) * 100:.1f}% range over 20 bars)."
                )
            counterarguments.append(
                "Breakouts fail more than they succeed; size and risk discipline matter."
            )
            return SetupClassification(
                setup_type="breakout_candidate",
                confidence="medium",
                reasons=reasons,
                counterarguments=counterarguments,
                supporting_metrics=metrics,
                data_sufficiency="sufficient",
            )

        # Default uptrend bucket
        if slope_50 is not None and slope_50 > thresholds.uptrend_50_slope_min:
            reasons.append("Bullish MA stack (price > 20 > 50 > 200).")
            reasons.append(
                f"50-day slope {slope_50 * 100:.1f}% over 21 bars — upward sloping."
            )
            counterarguments.append(
                "No clean entry trigger from this snapshot alone; entries either chase or require pullback patience."
            )
            return SetupClassification(
                setup_type="strong_uptrend",
                confidence="medium",
                reasons=reasons,
                counterarguments=counterarguments,
                supporting_metrics=metrics,
                data_sufficiency="sufficient",
            )

    # Range-bound
    if (
        indicators.ma_alignment in ("mixed", "insufficient")
        and levels.consolidation_range_pct is not None
        and levels.consolidation_range_pct <= thresholds.range_bound_consolidation_pct
    ):
        reasons.append("MAs are not stacked; price is consolidating in a tight range.")
        return SetupClassification(
            setup_type="range_bound",
            confidence="medium",
            reasons=reasons,
            counterarguments=[
                "Range trading rewards patience; ranges break eventually — direction is unknown.",
            ],
            supporting_metrics=metrics,
            data_sufficiency="sufficient",
        )

    return SetupClassification(
        setup_type="unclear",
        confidence="low",
        reasons=["No rule branch matched this combination of indicators + levels."],
        counterarguments=[],
        supporting_metrics=metrics,
        data_sufficiency="sufficient",
    )


def _fmt_dollars(x: float) -> str:
    if x >= 1e9:
        return f"${x / 1e9:.1f}B"
    if x >= 1e6:
        return f"${x / 1e6:.1f}M"
    if x >= 1e3:
        return f"${x / 1e3:.0f}K"
    return f"${x:.0f}"


__all__ = ["DEFAULT_THRESHOLDS", "SetupThresholds", "classify_setup"]
