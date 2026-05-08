"""Decision-support status — the rule-based rubric.

Maps `(SetupClassification, IndicatorPanel, MarketSnapshotPanel,
LevelsPanel, IdentityCoverage)` → `DecisionSupportStatus`.

The status is one of:
- `research_candidate` — high-quality setup with clean entry context
- `watch` — promising but missing a clear trigger
- `wait_for_setup` — no setup right now; check back later
- `skip_for_now` — actively bad setup
- `extended_risk` — momentum is real but chase risk is high
- `insufficient_data` — not enough information to read

Importantly: this is NOT a magic composite score. Every component of the
rubric is named, has a value + threshold, and explicitly says whether it
passed or failed. The final status is rule-based: counts of "passed"
high-weight components plus a few hard-blockers.

Decision-support language only — no buy/sell. The user pulls the trigger.
"""

from __future__ import annotations

from app.analysis.schema import (
    DecisionRubricEntry,
    DecisionSupportStatus,
    IdentityCoverage,
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
    SetupClassification,
    StyleFitPanel,
)


def _rule(
    name: str,
    *,
    value: float | str | None,
    threshold: float | str | None,
    passed: bool | None,
    weight: str = "medium",
    note: str | None = None,
) -> DecisionRubricEntry:
    return DecisionRubricEntry(
        name=name,
        value=value,
        threshold=threshold,
        passed=passed,
        weight=weight,
        note=note,
    )


def derive_status(
    setup: SetupClassification,
    style_fit: StyleFitPanel,
    indicators: IndicatorPanel,
    market: MarketSnapshotPanel,
    levels: LevelsPanel,
    identity: IdentityCoverage,
) -> DecisionSupportStatus:
    """Run the rubric. Returns a `DecisionSupportStatus` with show-your-work fields."""

    # --- Hard blockers (override the rubric) ---
    if setup.setup_type == "insufficient_data":
        return DecisionSupportStatus(
            status="insufficient_data",
            confidence="low",
            summary="Not enough price history loaded to evaluate this ticker yet.",
            rubric=[
                _rule(
                    "data_sufficiency",
                    value=identity.n_bars_loaded,
                    threshold=200,
                    passed=False,
                    weight="high",
                    note="Need ≥200 daily bars for the long-trend stack.",
                )
            ],
            caveats=[
                "Increase history_days when fetching, or wait for more data to land.",
            ],
        )

    if setup.setup_type == "low_liquidity":
        return DecisionSupportStatus(
            status="skip_for_now",
            confidence="medium",
            summary="Liquidity is below the threshold for clean fills — skip until volume picks up.",
            rubric=[
                _rule(
                    "dollar_volume_min",
                    value=market.dollar_volume,
                    threshold=5_000_000,
                    passed=False,
                    weight="high",
                )
            ],
            caveats=[
                "Liquidity is dynamic; a catalyst can change this quickly.",
            ],
        )

    if setup.setup_type == "high_volatility_unstable":
        return DecisionSupportStatus(
            status="skip_for_now",
            confidence="medium",
            summary="Volatility is too high to size a position with a meaningful stop. Skip until ATR contracts.",
            rubric=[
                _rule(
                    "atr_pct_under_high",
                    value=indicators.atr_14_pct,
                    threshold=0.08,
                    passed=False,
                    weight="high",
                )
            ],
            caveats=[
                "High vol on a constructive base sometimes precedes a clean breakout — chart context matters.",
            ],
        )

    if setup.setup_type == "downtrend":
        return DecisionSupportStatus(
            status="skip_for_now",
            confidence="medium",
            summary="Bearish MA stack with weak slope — not a long-only setup.",
            rubric=[
                _rule(
                    "ma_alignment_bullish",
                    value=indicators.ma_alignment,
                    threshold="bullish_stack",
                    passed=False,
                    weight="high",
                ),
                _rule(
                    "sma_50_slope_positive",
                    value=indicators.sma_50_slope_21d_pct,
                    threshold=0.0,
                    passed=(
                        indicators.sma_50_slope_21d_pct is not None
                        and indicators.sma_50_slope_21d_pct > 0
                    ),
                    weight="high",
                ),
            ],
            caveats=[
                "Downtrends end. Re-evaluate once a base forms.",
            ],
        )

    if setup.setup_type == "extended_momentum":
        return DecisionSupportStatus(
            status="extended_risk",
            confidence="medium",
            summary=(
                "Strong trend but price is stretched well above the 50-SMA with overbought RSI — "
                "chase risk is high; wait for a pullback."
            ),
            rubric=[
                _rule(
                    "dist_to_sma_50_below_extended",
                    value=indicators.dist_to_sma_50_pct,
                    threshold=0.15,
                    passed=False,
                    weight="high",
                ),
                _rule(
                    "rsi_under_75",
                    value=indicators.rsi_14,
                    threshold=75,
                    passed=(
                        indicators.rsi_14 is not None and indicators.rsi_14 <= 75
                    ),
                    weight="medium",
                ),
            ],
            caveats=[
                "Strong trends can stay extended; this is risk-management framing, not a top call.",
            ],
        )

    # --- Composable rubric for the constructive cases ---
    rubric: list[DecisionRubricEntry] = []

    bullish_stack = indicators.ma_alignment == "bullish_stack"
    rubric.append(
        _rule(
            "ma_alignment_bullish",
            value=indicators.ma_alignment,
            threshold="bullish_stack",
            passed=bullish_stack,
            weight="high",
        )
    )

    slope_50 = indicators.sma_50_slope_21d_pct
    rubric.append(
        _rule(
            "sma_50_slope_positive",
            value=slope_50,
            threshold=0.0,
            passed=(slope_50 is not None and slope_50 > 0),
            weight="high",
        )
    )

    rs_63 = indicators.relative_strength_vs_spy_63d
    rubric.append(
        _rule(
            "rs_vs_spy_63d_positive",
            value=rs_63,
            threshold=0.0,
            passed=(rs_63 is not None and rs_63 > 0),
            weight="medium",
        )
    )

    rsi = indicators.rsi_14
    rubric.append(
        _rule(
            "rsi_in_range_40_70",
            value=rsi,
            threshold="40-70",
            passed=(rsi is not None and 40 <= rsi <= 70),
            weight="low",
            note="Inside the band = healthy; below 40 = oversold; above 70 = extended.",
        )
    )

    has_zone = (
        levels.nearest_support is not None
        or levels.base_low is not None
    )
    rubric.append(
        _rule(
            "has_clean_risk_reference",
            value="yes" if has_zone else "no",
            threshold="yes",
            passed=has_zone,
            weight="medium",
            note="An identifiable swing low or base low for placing an invalidation reference.",
        )
    )

    style_high = any(it.fit_level == "high" for it in style_fit.items if it.style != "not_suitable_now")
    style_med_or_high = any(
        it.fit_level in ("high", "medium") for it in style_fit.items if it.style != "not_suitable_now"
    )
    rubric.append(
        _rule(
            "style_fit_present",
            value=style_fit.primary_style or "none",
            threshold="non-low",
            passed=style_med_or_high,
            weight="medium",
        )
    )

    transcript_freshness_caveat: str | None = None
    if not identity.has_transcript_signals and not identity.has_extracted_calls:
        transcript_freshness_caveat = (
            "No transcript signals — read is technicals-only."
        )

    # --- Bucket the status ---
    high_rules = [r for r in rubric if r.weight == "high"]
    high_passed = sum(1 for r in high_rules if r.passed)
    medium_rules = [r for r in rubric if r.weight == "medium"]
    medium_passed = sum(1 for r in medium_rules if r.passed)

    caveats: list[str] = []
    if transcript_freshness_caveat:
        caveats.append(transcript_freshness_caveat)
    if not has_zone:
        caveats.append(
            "No clean swing-low / base-low reference — entry-zone confidence is reduced."
        )

    if (
        setup.setup_type in ("strong_uptrend", "uptrend_pullback", "breakout_candidate")
        and high_passed == len(high_rules)
        and medium_passed >= 1
        and style_high
    ):
        return DecisionSupportStatus(
            status="research_candidate",
            confidence="high" if medium_passed >= 2 else "medium",
            summary=(
                f"Constructive {setup.setup_type.replace('_', ' ')} with clean trend + style fit. "
                "Worth a closer look."
            ),
            rubric=rubric,
            caveats=caveats,
        )

    if (
        setup.setup_type in ("strong_uptrend", "uptrend_pullback", "breakout_candidate")
        and high_passed >= len(high_rules) - 1
    ):
        return DecisionSupportStatus(
            status="watch",
            confidence="medium",
            summary=(
                "Trend is intact but the trigger isn't fully formed — keep on the watchlist "
                "and revisit when the missing components show up."
            ),
            rubric=rubric,
            caveats=caveats,
        )

    if setup.setup_type == "range_bound":
        return DecisionSupportStatus(
            status="wait_for_setup",
            confidence="medium",
            summary="Range-bound. Wait for either a clean break or a tag of the range boundary with confirmation.",
            rubric=rubric,
            caveats=caveats,
        )

    if setup.setup_type == "unclear":
        return DecisionSupportStatus(
            status="wait_for_setup",
            confidence="low",
            summary="Indicators don't form a coherent setup right now.",
            rubric=rubric,
            caveats=caveats,
        )

    return DecisionSupportStatus(
        status="watch",
        confidence="low",
        summary="Mixed signals — placeholder watch status; revisit after more data.",
        rubric=rubric,
        caveats=caveats,
    )


__all__ = ["derive_status"]
