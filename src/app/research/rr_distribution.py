"""Compute the full R/R distribution across candidate-level combinations.

Used by both Quick and Deep modes after the LLM has emitted its categorical
picks. The point: surface the R/R *range* the deterministic grid supports,
so the headline R/R is one point in a distribution rather than 'the answer'.
"""

from __future__ import annotations

from app.research.schema import (
    CandidateLevels,
    Picks,
    RRCombo,
    RRDistribution,
    ZoneBand,
    blended_risk_reward,
)


def compute_rr_distribution(
    candidates: CandidateLevels, picks: Picks
) -> RRDistribution:
    """Enumerate every (entry × primary × runner × invalidation) combo.

    For each combo, compute `rr_primary`, `rr_runner` (None if no runner),
    and `rr_blended` via the same blended formula as the headline plan.
    Skip combos where `entry.low - invalidation <= 0` (R/R undefined).
    """
    entries = _entries(candidates)
    primaries = candidates.primary_exit_candidates
    runners: list[ZoneBand] = list(candidates.runner_exit_candidates) or []
    runners_with_none: list[tuple[int | None, ZoneBand | None]] = [
        (i, r) for i, r in enumerate(runners)
    ]
    runners_with_none.append((None, None))
    invalidations = candidates.invalidation_candidates

    combos: list[RRCombo] = []
    for entry_kind, entry in entries:
        for p_idx, primary in enumerate(primaries):
            for r_idx, runner in runners_with_none:
                for inv_idx, inv in enumerate(invalidations):
                    risk = entry.low - inv
                    if risk <= 0:
                        continue
                    rr_p = risk_reward(entry, primary, inv)
                    if rr_p <= 0:
                        # Degenerate: primary exit is at or below entry — no
                        # valid trade. Skip so it doesn't pollute the
                        # distribution with rr_blended=0.0 and bias min/median.
                        continue
                    rr_r = risk_reward(entry, runner, inv) if runner is not None else None
                    rr_b = blended_risk_reward(rr_primary=rr_p, rr_runner=rr_r)
                    if rr_b is None:
                        continue
                    combos.append(
                        RRCombo(
                            entry_kind=entry_kind,
                            primary_index=p_idx,
                            runner_index=r_idx,
                            invalidation_index=inv_idx,
                            entry_label=entry.method,
                            primary_label=primary.method,
                            runner_label=runner.method if runner else None,
                            invalidation_label=f"{inv:.2f}",
                            rr_primary=rr_p,
                            rr_runner=rr_r,
                            rr_blended=rr_b,
                            is_chosen=(
                                entry_kind == picks.entry_kind
                                and p_idx == picks.primary_index
                                and r_idx == picks.runner_index
                                and inv_idx == picks.invalidation_index
                            ),
                        )
                    )

    if not combos:
        return RRDistribution(min_rr=0.0, median_rr=0.0, max_rr=0.0, n_combos=0, combos=[])

    rrs = sorted(c.rr_blended for c in combos)
    return RRDistribution(
        min_rr=rrs[0],
        median_rr=_median(rrs),
        max_rr=rrs[-1],
        n_combos=len(combos),
        combos=combos,
    )


def _entries(candidates: CandidateLevels) -> list[tuple[str, ZoneBand]]:
    out: list[tuple[str, ZoneBand]] = []
    if candidates.breakout_entry is not None:
        out.append(("breakout", candidates.breakout_entry))
    if candidates.pullback_entry is not None:
        out.append(("pullback", candidates.pullback_entry))
    return out


def risk_reward(entry: ZoneBand, exit_: ZoneBand, invalidation: float) -> float:
    """R/R for a single (entry, exit, invalidation) triple.

    Uses the band edges that make this a *conservative* read: risk =
    entry.low − invalidation (the deepest you'd pay before entry); reward
    = exit_.low − entry.high (the nearest take-profit minus the worst
    fill). Returns 0.0 when the trade is structurally undefined (risk or
    reward non-positive). Rounded to 2 decimals to match the headline
    R/R fields on `EntryExitPlan`.

    Shared by `compute_rr_distribution` and the headline R/R math in
    `quick.py` / `judge.py` so all three reads can never drift.
    """
    risk = entry.low - invalidation
    if risk <= 0:
        return 0.0
    reward = exit_.low - entry.high
    if reward <= 0:
        return 0.0
    return round(reward / risk, 2)


def _median(values: list[float]) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return round(values[n // 2], 2)
    return round((values[n // 2 - 1] + values[n // 2]) / 2, 2)


__all__ = ["Picks", "compute_rr_distribution", "risk_reward"]
