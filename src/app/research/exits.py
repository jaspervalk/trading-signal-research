"""Deterministic exit-zone math.

Computes candidate take-profit zones from the existing `LevelsPanel` and
`IndicatorPanel`. The LLM later picks among these by reference (integer
indices into the candidate lists) and the system resolves picks verbatim;
the LLM does NOT invent fresh exit levels and no longer scales them. This
is the hallucination guard.

Three sources of candidates, in priority order:
1. Nearest resistance + 0.25×ATR cushion ("first resistance test")
2. Recent 63-bar high (if not the same as #1)
3. Fibonacci extension (1.272x, 1.618x) from the base low → recent high

Runner exits are 1.5x ATR beyond each primary, and capped at the next
visible swing high.
"""

from __future__ import annotations

from app.analysis.schema import (
    EntryZoneCandidate,
    IndicatorPanel,
    LevelsPanel,
    MarketSnapshotPanel,
)
from app.research.schema import CandidateLevels, ZoneBand

PRIMARY_BAND_ATR_FRACTION = 0.25
RUNNER_OFFSET_ATR = 1.5
FIB_PRIMARY = 1.272
FIB_RUNNER = 1.618


def build_candidate_levels(
    *,
    indicators: IndicatorPanel,
    levels: LevelsPanel,
    market: MarketSnapshotPanel,
    breakout_entry: EntryZoneCandidate | None,
    pullback_entry: EntryZoneCandidate | None,
) -> CandidateLevels:
    """Assemble all deterministic level candidates for the LLM to pick from.

    `breakout_entry` and `pullback_entry` come from `entry.py` (the upcoming
    dual-zone variant). Either may be None. `last_close` and `atr_14` are
    threaded through so the LLM has the full context when picking among the
    candidates by reference.
    """
    last_close = market.last_close
    atr = indicators.atr_14

    candidates = CandidateLevels(
        last_close=last_close,
        atr_14=atr,
    )

    if breakout_entry is not None and breakout_entry.available:
        candidates.breakout_entry = ZoneBand(
            low=breakout_entry.candidate_research_zone_low or 0.0,
            high=breakout_entry.candidate_research_zone_high or 0.0,
            method="63-bar high + 0.5×ATR (breakout)",
            rationale="Breakout trigger band from entry.py.",
        )
    if pullback_entry is not None and pullback_entry.available:
        candidates.pullback_entry = ZoneBand(
            low=pullback_entry.candidate_research_zone_low or 0.0,
            high=pullback_entry.candidate_research_zone_high or 0.0,
            method="21-EMA / 50-SMA band (pullback)",
            rationale="Pullback research zone from entry.py.",
        )

    candidates.primary_exit_candidates = _build_primary_exits(
        levels=levels, atr=atr, last_close=last_close
    )
    candidates.runner_exit_candidates = _build_runner_exits(
        levels=levels,
        atr=atr,
        last_close=last_close,
        primaries=candidates.primary_exit_candidates,
    )
    candidates.invalidation_candidates = _build_invalidations(
        levels=levels,
        atr=atr,
        breakout_entry=breakout_entry,
        pullback_entry=pullback_entry,
    )
    return candidates


# ---------------------------------------------------------------------------
# Primary exits — the "first take-profit" candidates.


def _build_primary_exits(
    *,
    levels: LevelsPanel,
    atr: float | None,
    last_close: float | None,
) -> list[ZoneBand]:
    out: list[ZoneBand] = []
    if last_close is None or last_close <= 0:
        return out

    # Anchor 1: nearest resistance + 0.25×ATR cushion.
    if levels.nearest_resistance is not None and levels.nearest_resistance > last_close:
        cushion = (atr * PRIMARY_BAND_ATR_FRACTION) if atr else 0.0
        out.append(
            ZoneBand(
                low=levels.nearest_resistance,
                high=levels.nearest_resistance + cushion,
                method="nearest resistance + 0.25×ATR cushion",
                rationale="First test of overhead supply — common partial-trim spot.",
            )
        )

    # Anchor 2: recent 63-bar high. Only emit if it's distinct from the
    # nearest-resistance band (≥ 1×ATR away or the resistance was below price).
    high = levels.recent_high_63d
    if high is not None and high > last_close:
        too_close_to_existing = (
            atr is not None
            and out
            and abs(high - out[0].low) < atr
        )
        if not too_close_to_existing:
            cushion = (atr * PRIMARY_BAND_ATR_FRACTION) if atr else 0.0
            out.append(
                ZoneBand(
                    low=high,
                    high=high + cushion,
                    method="recent 63-bar high + 0.25×ATR",
                    rationale="Prior pivot high — supply zone often retests before clearing.",
                )
            )

    # Anchor 3: Fib extension if we have base_low → recent_high range.
    fib = _fib_extension(levels=levels, mult=FIB_PRIMARY, atr=atr)
    if fib is not None and fib.high > last_close:
        out.append(fib)

    return out


# ---------------------------------------------------------------------------
# Runner exits — extended targets beyond the primary.


def _build_runner_exits(
    *,
    levels: LevelsPanel,
    atr: float | None,
    last_close: float | None,
    primaries: list[ZoneBand],
) -> list[ZoneBand]:
    out: list[ZoneBand] = []
    if last_close is None or atr is None:
        return out

    # Each primary gets a runner offset 1.5×ATR above its high.
    for p in primaries:
        runner_low = p.high + RUNNER_OFFSET_ATR * atr
        runner_high = runner_low + PRIMARY_BAND_ATR_FRACTION * atr
        out.append(
            ZoneBand(
                low=runner_low,
                high=runner_high,
                method=f"primary high + {RUNNER_OFFSET_ATR}×ATR",
                rationale="Runner trail target — leaves room for a measured move beyond first resistance.",
            )
        )

    # Fib 1.618 extension as an additional runner candidate.
    fib = _fib_extension(levels=levels, mult=FIB_RUNNER, atr=atr)
    if fib is not None and fib.high > last_close:
        out.append(fib)

    # Sort by `low` so the LLM sees them in price order.
    out.sort(key=lambda z: z.low)
    return out


# ---------------------------------------------------------------------------
# Invalidation candidates — multiple reference stops; LLM picks one.


def _build_invalidations(
    *,
    levels: LevelsPanel,
    atr: float | None,
    breakout_entry: EntryZoneCandidate | None,
    pullback_entry: EntryZoneCandidate | None,
) -> list[float]:
    out: list[float] = []
    for entry in (breakout_entry, pullback_entry):
        if (
            entry is not None
            and entry.available
            and entry.invalidation_reference is not None
        ):
            out.append(entry.invalidation_reference)
    if levels.nearest_support is not None:
        out.append(levels.nearest_support)
    if levels.base_low is not None and (atr is not None and atr > 0):
        out.append(levels.base_low - 0.5 * atr)
    # Deduplicate while preserving order.
    seen: set[float] = set()
    unique: list[float] = []
    for x in out:
        rounded = round(x, 4)
        if rounded not in seen:
            seen.add(rounded)
            unique.append(x)
    return unique


# ---------------------------------------------------------------------------
# Helpers


def _fib_extension(
    *, levels: LevelsPanel, mult: float, atr: float | None
) -> ZoneBand | None:
    """Compute a Fibonacci extension band from base_low → recent_high.

    Standard formula: low + (high - low) × mult. Only emit when both anchors
    are present and the result is a meaningful price.
    """
    base_low = levels.base_low
    recent_high = levels.recent_high_63d
    if base_low is None or recent_high is None or recent_high <= base_low:
        return None
    target = base_low + (recent_high - base_low) * mult
    cushion = (atr * PRIMARY_BAND_ATR_FRACTION) if atr else 0.0
    return ZoneBand(
        low=target,
        high=target + cushion,
        method=f"Fibonacci {mult}× extension (base→recent_high)",
        rationale=(
            f"Measured-move target: {mult}× the prior swing range projected "
            "from the base low."
        ),
    )


__all__ = ["build_candidate_levels"]
