"""Style fit — which research style does the current setup most resemble?

Maps `(SetupClassification, IndicatorPanel, LevelsPanel)` →
`StyleFitPanel`. Every style in the catalog gets a fit level; the
"primary" style is the highest-fit one, with a setup-driven tie-breaker.

Catalog:
- `momentum_breakout` — tight base near recent highs + decent RS
- `trend_pullback` — bullish stack + pulled back near 21-EMA / 50-SMA
- `mean_reversion` — RSI extremes inside a range / against the trend
- `base_breakout` — long base (3+ months consolidation) close to highs
- `relative_strength_leader` — strongest 63d-126d RS vs SPY, regardless of setup
- `not_suitable_now` — none of the above; data-driven "skip"

The output of this layer is decision-support context — never a trade
instruction. Every style includes invalidation conditions and "what
would improve it" notes so the user can read the work.
"""

from __future__ import annotations

from app.analysis.schema import (
    IndicatorPanel,
    LevelsPanel,
    SetupClassification,
    StyleFitItem,
    StyleFitPanel,
)


def _fit(level: str) -> str:
    return level


def _safe_pct(x: float | None, digits: int = 1) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:.{digits}f}%"


def evaluate_style_fit(
    setup: SetupClassification,
    indicators: IndicatorPanel,
    levels: LevelsPanel,
) -> StyleFitPanel:
    """Score every style in the catalog. Returns a `StyleFitPanel`."""
    items: list[StyleFitItem] = []

    items.append(_momentum_breakout(setup, indicators, levels))
    items.append(_trend_pullback(setup, indicators, levels))
    items.append(_mean_reversion(setup, indicators, levels))
    items.append(_base_breakout(setup, indicators, levels))
    items.append(_relative_strength_leader(setup, indicators, levels))
    items.append(_not_suitable_now(setup, indicators, levels))

    # Pick a primary: highest fit, with a setup-driven tie-breaker.
    fit_rank = {"high": 3, "medium": 2, "low": 1}
    setup_to_preferred_style = {
        "strong_uptrend": "trend_pullback",
        "uptrend_pullback": "trend_pullback",
        "breakout_candidate": "momentum_breakout",
        "extended_momentum": "relative_strength_leader",
        "range_bound": "mean_reversion",
        "downtrend": "not_suitable_now",
        "high_volatility_unstable": "not_suitable_now",
        "low_liquidity": "not_suitable_now",
        "insufficient_data": "not_suitable_now",
        "unclear": "not_suitable_now",
    }
    preferred = setup_to_preferred_style.get(setup.setup_type, "not_suitable_now")

    best = None
    for it in items:
        if best is None:
            best = it
            continue
        if fit_rank[it.fit_level] > fit_rank[best.fit_level]:
            best = it
        elif fit_rank[it.fit_level] == fit_rank[best.fit_level]:
            # tie-break toward the setup-preferred style
            if it.style == preferred and best.style != preferred:
                best = it

    primary_style = best.style if best and best.fit_level != "low" else None

    return StyleFitPanel(items=items, primary_style=primary_style)


# ---------------------------------------------------------------------------
# Per-style scorers


def _momentum_breakout(
    setup: SetupClassification,
    indicators: IndicatorPanel,
    levels: LevelsPanel,
) -> StyleFitItem:
    reasons: list[str] = []
    weak: list[str] = []
    levels_dict: dict[str, float | None] = {
        "recent_high_63d": levels.recent_high_63d,
        "consolidation_range_pct": levels.consolidation_range_pct,
    }

    fit = "low"
    is_breakout = setup.setup_type == "breakout_candidate"
    near_high = (
        levels.breakout_distance_pct is not None
        and -0.05 <= levels.breakout_distance_pct <= 0.02
    )
    is_tight = levels.is_in_tight_range
    rs_ok = (
        indicators.relative_strength_vs_spy_63d is not None
        and indicators.relative_strength_vs_spy_63d > 0
    )

    if is_breakout and is_tight and rs_ok:
        fit = "high"
        reasons.append("Setup is a breakout candidate.")
        reasons.append(
            f"Tight 20-bar range ({_safe_pct(levels.consolidation_range_pct)})."
        )
        reasons.append(
            f"Outperforming SPY by {_safe_pct(indicators.relative_strength_vs_spy_63d)} over 63 bars."
        )
    elif (is_breakout or near_high) and (is_tight or rs_ok):
        fit = "medium"
        if is_breakout:
            reasons.append("Setup is a breakout candidate.")
        elif near_high:
            reasons.append("Within striking distance of the recent 63-bar high.")
        if is_tight:
            reasons.append(
                f"20-bar range is {_safe_pct(levels.consolidation_range_pct)} — moderately tight."
            )
        if rs_ok:
            reasons.append(
                f"Positive RS vs SPY over 63 bars ({_safe_pct(indicators.relative_strength_vs_spy_63d)})."
            )
    else:
        if not near_high:
            weak.append("Price is not near a recent breakout level.")
        if not is_tight:
            weak.append("No tight consolidation range to break out of.")

    return StyleFitItem(
        style="momentum_breakout",
        fit_level=fit,
        reasons=reasons,
        relevant_levels=levels_dict,
        invalidation_conditions=[
            "Close below the consolidation low.",
            "Loss of the breakout pivot on volume.",
        ],
        what_would_improve=[
            "A clean breakout on above-average volume.",
            "Continued tightening of the range with RS holding up.",
        ],
        what_would_weaken=weak
        or [
            "Price drifting away from the recent high without a base.",
            "RS turning negative.",
        ],
    )


def _trend_pullback(
    setup: SetupClassification,
    indicators: IndicatorPanel,
    levels: LevelsPanel,
) -> StyleFitItem:
    reasons: list[str] = []
    weak: list[str] = []
    levels_dict: dict[str, float | None] = {
        "ema_21": indicators.ema_21,
        "sma_50": indicators.sma_50,
    }

    bullish = indicators.ma_alignment == "bullish_stack"
    pullback = levels.pullback_pct_from_recent_high
    healthy_pullback = pullback is not None and 0.04 <= pullback <= 0.18
    near_50_or_ema21 = (
        (
            indicators.dist_to_ema_21_pct is not None
            and -0.06 <= indicators.dist_to_ema_21_pct <= 0.04
        )
        or (
            indicators.dist_to_sma_50_pct is not None
            and -0.08 <= indicators.dist_to_sma_50_pct <= 0.04
        )
    )

    fit = "low"
    if setup.setup_type == "uptrend_pullback":
        if near_50_or_ema21:
            fit = "high"
            reasons.append(
                f"Pulled back {_safe_pct(pullback)} from the 63-bar high — into the 21-EMA / 50-SMA zone."
            )
            reasons.append("Bullish MA stack with the trend still up.")
        else:
            fit = "medium"
            reasons.append("Setup is an uptrend pullback, but price isn't sitting on a key MA yet.")
    elif setup.setup_type == "strong_uptrend":
        if healthy_pullback:
            fit = "medium"
            reasons.append(
                "Strong uptrend with a modest pullback — borderline trend-pullback fit."
            )
        else:
            fit = "low"
            weak.append("No meaningful pullback yet — entry would chase.")
    elif bullish and healthy_pullback:
        fit = "medium"
    else:
        weak.append("No bullish trend to pull back into.")

    return StyleFitItem(
        style="trend_pullback",
        fit_level=fit,
        reasons=reasons,
        relevant_levels=levels_dict,
        invalidation_conditions=[
            "Close below the 50-SMA on volume.",
            "Pullback exceeds 20% from the recent high (becomes a correction).",
        ],
        what_would_improve=[
            "Tag of the 21-EMA or 50-SMA followed by a reclaim.",
            "Volume-light pullback into support.",
        ],
        what_would_weaken=weak
        or [
            "MA stack flips to bearish.",
            "Pullback widens past 20%.",
        ],
    )


def _mean_reversion(
    setup: SetupClassification,
    indicators: IndicatorPanel,
    levels: LevelsPanel,
) -> StyleFitItem:
    reasons: list[str] = []
    levels_dict: dict[str, float | None] = {
        "nearest_support": levels.nearest_support,
        "nearest_resistance": levels.nearest_resistance,
        "rsi_14": indicators.rsi_14,
    }
    fit = "low"
    rsi_low = indicators.rsi_14 is not None and indicators.rsi_14 <= 30
    rsi_high = indicators.rsi_14 is not None and indicators.rsi_14 >= 70

    if setup.setup_type == "range_bound" and (rsi_low or rsi_high):
        fit = "high" if rsi_low else "medium"
        reasons.append("Range-bound setup with stretched RSI.")
        reasons.append(f"RSI(14) at {indicators.rsi_14:.0f}.")
    elif rsi_low and setup.setup_type in ("uptrend_pullback", "strong_uptrend"):
        # Buying-the-dip is mean-reversion-ish, but only when the trend is intact.
        fit = "medium"
        reasons.append(f"Oversold RSI ({indicators.rsi_14:.0f}) inside an uptrend.")
    return StyleFitItem(
        style="mean_reversion",
        fit_level=fit,
        reasons=reasons,
        relevant_levels=levels_dict,
        invalidation_conditions=[
            "Break of range on volume — mean-reversion turns into trend.",
            "RSI failing to rebound on price recovery.",
        ],
        what_would_improve=[
            "Sharp tag of the lower band on capitulation volume.",
            "Reversal candle at the boundary of the range.",
        ],
        what_would_weaken=[
            "Range expanding (volatility breakout in either direction).",
            "Trend re-establishing in the direction of the stretched move.",
        ],
    )


def _base_breakout(
    setup: SetupClassification,
    indicators: IndicatorPanel,
    levels: LevelsPanel,
) -> StyleFitItem:
    reasons: list[str] = []
    fit = "low"
    levels_dict: dict[str, float | None] = {
        "base_low": levels.base_low,
        "base_high": levels.base_high,
        "recent_high_63d": levels.recent_high_63d,
    }
    if levels.base_low is not None and levels.base_high is not None and levels.base_high > 0:
        base_pct = (levels.base_high - levels.base_low) / levels.base_high
        long_base = base_pct < 0.20  # base depth ≤20% on a ~3-month window
        near_high = (
            levels.breakout_distance_pct is not None
            and -0.05 <= levels.breakout_distance_pct <= 0.02
        )
        if long_base and near_high and setup.setup_type in ("breakout_candidate", "strong_uptrend"):
            fit = "high"
            reasons.append(
                f"3-month base depth ≈ {_safe_pct(base_pct)} — constructive base."
            )
            reasons.append("Price within striking distance of the breakout pivot.")
        elif long_base:
            fit = "medium"
            reasons.append("Constructive 3-month base, but the breakout pivot isn't close yet.")
    return StyleFitItem(
        style="base_breakout",
        fit_level=fit,
        reasons=reasons,
        relevant_levels=levels_dict,
        invalidation_conditions=[
            "Close below the base low.",
            "Failure to clear the base high after multiple attempts.",
        ],
        what_would_improve=[
            "Higher lows inside the base (cup-with-handle).",
            "Volume drying up into the breakout pivot.",
        ],
        what_would_weaken=[
            "Choppy, wide-ranging base (more like a downtrend than a base).",
        ],
    )


def _relative_strength_leader(
    setup: SetupClassification,
    indicators: IndicatorPanel,
    levels: LevelsPanel,
) -> StyleFitItem:
    reasons: list[str] = []
    fit = "low"
    rs_63 = indicators.relative_strength_vs_spy_63d
    rs_126 = indicators.relative_strength_vs_spy_126d
    if rs_63 is not None and rs_126 is not None and rs_63 > 0.05 and rs_126 > 0.10:
        # Notable outperformance on both windows
        if setup.setup_type in ("strong_uptrend", "uptrend_pullback", "breakout_candidate"):
            fit = "high"
            reasons.append(
                f"Outperforming SPY by {_safe_pct(rs_63)} over 63 bars and "
                f"{_safe_pct(rs_126)} over 126 bars — a leader."
            )
        else:
            fit = "medium"
            reasons.append("RS leadership without a constructive setup — leadership without a trigger.")
    elif rs_63 is not None and rs_63 > 0.03:
        fit = "medium" if setup.setup_type in ("strong_uptrend", "uptrend_pullback") else "low"
        if fit == "medium":
            reasons.append(f"Modestly outperforming SPY ({_safe_pct(rs_63)} over 63 bars).")
    return StyleFitItem(
        style="relative_strength_leader",
        fit_level=fit,
        reasons=reasons,
        relevant_levels={
            "relative_strength_vs_spy_63d": rs_63,
            "relative_strength_vs_spy_126d": rs_126,
        },
        invalidation_conditions=[
            "RS turning flat or negative.",
            "Price losing the 50-SMA while peers hold.",
        ],
        what_would_improve=[
            "RS expansion accompanied by a base or breakout setup.",
        ],
        what_would_weaken=[
            "Mean-reversion of relative performance against the index.",
        ],
    )


def _not_suitable_now(
    setup: SetupClassification,
    indicators: IndicatorPanel,
    levels: LevelsPanel,
) -> StyleFitItem:
    reasons: list[str] = []
    blockers = {
        "downtrend": "Bearish trend — not a long-only setup.",
        "high_volatility_unstable": "Volatility is too high to size a meaningful position safely.",
        "low_liquidity": "Insufficient liquidity for clean fills.",
        "insufficient_data": "Not enough history to evaluate a setup.",
        "extended_momentum": "Trend is intact but stretched — chasing risk is high.",
        "unclear": "Indicators don't form a coherent setup.",
    }
    if setup.setup_type in blockers:
        reasons.append(blockers[setup.setup_type])
        return StyleFitItem(
            style="not_suitable_now",
            fit_level="high",
            reasons=reasons,
            invalidation_conditions=[],
            what_would_improve=["A change in setup classification."],
            what_would_weaken=[],
        )
    return StyleFitItem(
        style="not_suitable_now",
        fit_level="low",
        reasons=[],
        invalidation_conditions=[],
        what_would_improve=[],
        what_would_weaken=[],
    )


__all__ = ["evaluate_style_fit"]
