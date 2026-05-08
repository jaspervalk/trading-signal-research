"""Entry-zone candidate logic.

Builds an `EntryZoneCandidate` for setups that have a usable trigger or
pullback context. Not a recommendation — strictly a "research zone" with
ATR-anchored invalidation and a transparent risk/reward estimate.

V1 mapping (setup_type → method):
- `breakout_candidate` → trigger above the recent 63-bar high; risk
  reference at the consolidation low (or 1.5×ATR below the close,
  whichever is closer to entry).
- `uptrend_pullback`   → research zone is the band around the 21-EMA / 50-SMA
  the price is approaching; invalidation 1×ATR below the 50-SMA.
- `strong_uptrend`     → if a healthy pullback is forming, same as
  uptrend_pullback; otherwise no entry zone (chase risk).
- `range_bound`        → research zone near the lower boundary of the
  20-bar range; invalidation 0.5×ATR below the range low.
- everything else      → no entry zone; emits a clear `reason_unavailable`.

Output language is decision-support: "candidate research zone",
"setup trigger level", "invalidation reference", "risk reference",
"risk/reward estimate". Never "buy" / "sell" / "stop loss" / "target".
"""

from __future__ import annotations

from app.analysis.schema import (
    EntryZoneCandidate,
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
    SetupClassification,
)


def build_entry_zone(
    setup: SetupClassification,
    indicators: IndicatorPanel,
    levels: LevelsPanel,
    market: MarketSnapshotPanel,
) -> EntryZoneCandidate:
    last_close = market.last_close
    atr = indicators.atr_14

    if last_close is None or atr is None:
        return EntryZoneCandidate(
            available=False,
            reason_unavailable="Last close or ATR(14) is unavailable.",
            method_notes=[],
        )

    if setup.setup_type == "breakout_candidate":
        return _breakout_zone(setup, indicators, levels, market, last_close=last_close, atr=atr)
    if setup.setup_type == "uptrend_pullback":
        return _pullback_zone(setup, indicators, levels, market, last_close=last_close, atr=atr)
    if setup.setup_type == "strong_uptrend":
        # Only emit a zone if there's a healthy pullback forming; otherwise
        # the most honest answer is "no zone — chase risk."
        pullback = levels.pullback_pct_from_recent_high
        if pullback is not None and pullback >= 0.04:
            return _pullback_zone(
                setup, indicators, levels, market, last_close=last_close, atr=atr
            )
        return EntryZoneCandidate(
            available=False,
            reason_unavailable=(
                "Strong uptrend with no meaningful pullback; an entry here would chase. "
                "Wait for a tag of the 21-EMA or 50-SMA."
            ),
            nearest_resistance=levels.nearest_resistance,
            method_notes=[],
        )
    if setup.setup_type == "range_bound":
        return _range_zone(setup, indicators, levels, market, last_close=last_close, atr=atr)

    return EntryZoneCandidate(
        available=False,
        reason_unavailable=_default_reason(setup.setup_type),
        nearest_resistance=levels.nearest_resistance,
        method_notes=[],
    )


# ---------------------------------------------------------------------------
# Zone builders


def _breakout_zone(
    setup: SetupClassification,
    indicators: IndicatorPanel,
    levels: LevelsPanel,
    market: MarketSnapshotPanel,
    *,
    last_close: float,
    atr: float,
) -> EntryZoneCandidate:
    notes: list[str] = []
    trigger = levels.recent_high_63d
    if trigger is None:
        return EntryZoneCandidate(
            available=False,
            reason_unavailable="Recent 63-bar high unavailable for the trigger reference.",
            method_notes=[],
        )

    # Research zone = a small band above the trigger.
    band_low = trigger
    band_high = trigger + 0.5 * atr
    notes.append(
        "Setup trigger = 63-bar recent high. Research zone = the half-ATR band above the trigger."
    )

    # Invalidation = consolidation low or 1.5×ATR below trigger, whichever is tighter.
    candidates: list[float] = []
    if levels.base_low is not None:
        candidates.append(levels.base_low)
    candidates.append(trigger - 1.5 * atr)
    invalidation = max(candidates)
    notes.append(
        "Invalidation reference = max(base low, trigger − 1.5×ATR(14)). The tighter of the two wins."
    )

    risk_pct = (band_low - invalidation) / band_low if band_low > 0 else None
    risk_atrs = (band_low - invalidation) / atr if atr > 0 else None

    nearest_res = levels.nearest_resistance
    rr = None
    if nearest_res is not None and nearest_res > band_low and band_low > invalidation:
        rr = (nearest_res - band_low) / (band_low - invalidation)

    return EntryZoneCandidate(
        available=True,
        setup_trigger_level=trigger,
        candidate_research_zone_low=band_low,
        candidate_research_zone_high=band_high,
        invalidation_reference=invalidation,
        risk_reference_pct=risk_pct,
        risk_reference_atrs=risk_atrs,
        nearest_resistance=nearest_res,
        risk_reward_estimate=rr,
        method_notes=notes,
    )


def _pullback_zone(
    setup: SetupClassification,
    indicators: IndicatorPanel,
    levels: LevelsPanel,
    market: MarketSnapshotPanel,
    *,
    last_close: float,
    atr: float,
) -> EntryZoneCandidate:
    notes: list[str] = []
    # Research zone is anchored at the 21-EMA → 50-SMA band, whichever is below.
    ema_21 = indicators.ema_21
    sma_50 = indicators.sma_50
    if ema_21 is None and sma_50 is None:
        return EntryZoneCandidate(
            available=False,
            reason_unavailable="Neither 21-EMA nor 50-SMA available — no anchor for the pullback zone.",
            method_notes=[],
        )

    anchors = [v for v in (ema_21, sma_50) if v is not None]
    band_low = min(anchors)
    band_high = max(anchors)
    if band_low == band_high:
        # Single anchor — widen by half an ATR to make a band.
        band_high = band_low + 0.5 * atr
    notes.append(
        "Pullback research zone = band between the 21-EMA and 50-SMA (or ±0.5×ATR around a single anchor)."
    )

    # Invalidation = max(nearest support, 50-SMA − 1×ATR). The user wants the
    # tighter (closer-to-entry) reference. If neither exists, fall back to
    # band_low − 1.5×ATR.
    inval_candidates: list[float] = []
    if levels.nearest_support is not None and levels.nearest_support < band_low:
        inval_candidates.append(levels.nearest_support)
    if sma_50 is not None:
        inval_candidates.append(sma_50 - 1.0 * atr)
    inval_candidates.append(band_low - 1.5 * atr)
    invalidation = max(inval_candidates)
    notes.append(
        "Invalidation reference = max(nearest support, 50-SMA − 1×ATR, zone-low − 1.5×ATR)."
    )

    risk_pct = (band_low - invalidation) / band_low if band_low > 0 else None
    risk_atrs = (band_low - invalidation) / atr if atr > 0 else None

    nearest_res = levels.nearest_resistance
    rr = None
    if nearest_res is not None and nearest_res > band_low and band_low > invalidation:
        rr = (nearest_res - band_low) / (band_low - invalidation)

    return EntryZoneCandidate(
        available=True,
        setup_trigger_level=ema_21 if ema_21 is not None else sma_50,
        candidate_research_zone_low=band_low,
        candidate_research_zone_high=band_high,
        invalidation_reference=invalidation,
        risk_reference_pct=risk_pct,
        risk_reference_atrs=risk_atrs,
        nearest_resistance=nearest_res,
        risk_reward_estimate=rr,
        method_notes=notes,
    )


def _range_zone(
    setup: SetupClassification,
    indicators: IndicatorPanel,
    levels: LevelsPanel,
    market: MarketSnapshotPanel,
    *,
    last_close: float,
    atr: float,
) -> EntryZoneCandidate:
    notes: list[str] = []
    range_low = levels.base_low
    range_high = levels.base_high
    if range_low is None or range_high is None:
        return EntryZoneCandidate(
            available=False,
            reason_unavailable="Range boundaries unavailable.",
            method_notes=[],
        )

    band_low = range_low
    band_high = range_low + 0.5 * atr
    invalidation = range_low - 0.5 * atr
    notes.append(
        "Range research zone = the half-ATR band above the lower boundary of the 60-bar range."
    )
    notes.append(
        "Invalidation = range low − 0.5×ATR. A break below should disqualify the range thesis."
    )

    risk_pct = (band_low - invalidation) / band_low if band_low > 0 else None
    risk_atrs = (band_low - invalidation) / atr if atr > 0 else None
    rr = (range_high - band_low) / (band_low - invalidation) if band_low > invalidation else None

    return EntryZoneCandidate(
        available=True,
        setup_trigger_level=range_low,
        candidate_research_zone_low=band_low,
        candidate_research_zone_high=band_high,
        invalidation_reference=invalidation,
        risk_reference_pct=risk_pct,
        risk_reference_atrs=risk_atrs,
        nearest_resistance=range_high,
        risk_reward_estimate=rr,
        method_notes=notes,
    )


def _default_reason(setup_type: str) -> str:
    return {
        "extended_momentum": "Trend is stretched — chase risk is too high to define a clean research zone.",
        "downtrend": "Bearish trend — long-only research zones are not appropriate here.",
        "high_volatility_unstable": "Volatility is too high to anchor a meaningful invalidation reference.",
        "low_liquidity": "Liquidity is below threshold; clean fills are unlikely.",
        "insufficient_data": "Not enough data to compute a research zone.",
        "unclear": "No coherent setup; no research zone to anchor.",
    }.get(setup_type, "No applicable rule for this setup type.")


def build_dual_entry_zones(
    setup: SetupClassification,
    indicators: IndicatorPanel,
    levels: LevelsPanel,
    market: MarketSnapshotPanel,
) -> tuple[EntryZoneCandidate | None, EntryZoneCandidate | None]:
    """Return `(breakout_zone, pullback_zone)` — either may be None.

    Used by `app.research` to feed BOTH zones to the LLM at once. The
    standard `build_entry_zone` keeps emitting a single zone for the existing
    `TickerResearchView` consumers.

    A breakout zone is built whenever a `recent_high_63d` and ATR exist; a
    pullback zone whenever a 21-EMA or 50-SMA + ATR exist. Either can be
    None if the supporting data is missing. We deliberately don't gate on
    `setup.setup_type` here — the caller is showing both zones as research
    options, and an "extended_momentum" ticker still has a useful pullback
    band even though `build_entry_zone` would suppress it.
    """
    last_close = market.last_close
    atr = indicators.atr_14
    if last_close is None or atr is None:
        return (None, None)

    breakout = _breakout_zone(
        setup, indicators, levels, market, last_close=last_close, atr=atr
    )
    pullback = _pullback_zone(
        setup, indicators, levels, market, last_close=last_close, atr=atr
    )
    # Either builder may have returned an unavailable candidate; surface as None.
    breakout = breakout if breakout.available else None
    pullback = pullback if pullback.available else None
    return (breakout, pullback)


__all__ = ["build_dual_entry_zones", "build_entry_zone"]
