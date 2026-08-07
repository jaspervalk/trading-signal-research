"""Layer C: inflection triggers over a ticker's own gross-margin history.

Layer A finds margin-compression candidates against their own historical
distribution; Layer B joins that to a named supply constraint. Layer C asks
the timing question: has the compression actually started reversing?

**This module implements exactly ONE of the four triggers in the brief:
gross-margin inflection.** It is pure arithmetic over `MarginHistory.gross_margins`
(`app.supply.fundamentals`), which the screen already computes for Layer A —
no new data source.

**Definition (verbatim, so a reader can check any output against it):** two
consecutive quarters of sequential (quarter-over-quarter) gross-margin
expansion, immediately following a declining trend — where "declining
trend" means the 4 quarters immediately before the expansion streak began
show a NET decline (first quarter of that 4-quarter window > its last
quarter; not required to be monotonic every single step in between).

**Trigger 2 (price vs. volume split) is deliberately NOT implemented here.**
It requires unit sales volume, and EDGAR's XBRL companyfacts do not
reliably carry it — most filers never tag a volume concept at all (unlike
revenue, cost, or margin, which are near-universal). Faking it with a proxy
(e.g. revenue / an assumed average selling price) would silently launder an
assumption into a metric that reads as data. Better to state the gap
plainly and leave the trigger unimplemented than to fake it — same
discipline `app.supply.metrics` uses for `survivability_quarters=None`
rather than guessing a number.

**Triggers 3 and 4 are out of scope** for this module entirely (not stubbed,
not planned here).

**Status lifecycle**, matching the brief's `armed` / `firing` / `confirmed`
vocabulary plus one addition (`insufficient_history`) for a state the brief
doesn't name but that a caller still needs to distinguish from "evaluated
and found nothing" — see `evaluate_gross_margin_inflection`'s docstring for
what puts a ticker in each bucket. Only `firing` and `confirmed` are meant
to draw a reader's attention; `armed` and `not_armed` stay quiet, and
`insufficient_history` is a "cannot evaluate," never a false negative.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

# 4 quarters to establish the prior decline + 2 quarters minimum to observe
# the expansion streak. Below this, the trigger cannot be evaluated at all —
# see `evaluate_gross_margin_inflection`'s `insufficient_history` branch.
MIN_QUARTERS = 6

TriggerStatus = Literal[
    "insufficient_history", "not_armed", "armed", "firing", "confirmed"
]


@dataclass(frozen=True)
class InflectionTrigger:
    """Result of evaluating the gross-margin inflection trigger for one
    ticker at one point in time.

    `expansion_streak` is the length of the current run of consecutive
    quarter-over-quarter GM increases ending at the most recent quarter —
    visible on its own, not folded into `status`, so a reader can tell a
    3-quarter confirmation from a 5-quarter one. `prior_decline_pp` is the
    net decline (in percentage points) over the 4 quarters immediately
    before that streak began; `None` whenever it wasn't evaluated (streak
    too short, or not enough history before it to check).
    """

    status: TriggerStatus
    quarters_of_history: int
    expansion_streak: int
    prior_decline_pp: Optional[float]
    detail: str

    @property
    def draws_attention(self) -> bool:
        """Only `firing` / `confirmed` should surface in a UI badge or
        headline — `armed` stays quiet per the brief, and the remaining
        statuses carry no signal at all."""
        return self.status in ("firing", "confirmed")


def evaluate_gross_margin_inflection(
    quarterly_gross_margins: list[float],
    *,
    min_quarters: int = MIN_QUARTERS,
) -> InflectionTrigger:
    """Evaluate the gross-margin inflection trigger over a chronological
    (oldest -> newest) series of quarterly gross margins.

    `quarterly_gross_margins` must already be in the same chronological
    order as `MarginHistory.gross_margins` (oldest first) — this function
    does not sort.

    Returns one of:

    - `insufficient_history` — fewer than `min_quarters` quarters available.
      A "cannot evaluate," deliberately distinct from `not_armed`: this
      function refuses to report "no trigger" on a series too short to
      rule it out, the same discipline `app.supply.metrics.compute_metrics`
      uses for `sufficient_history`.
    - `not_armed` — evaluated, and the defined pattern isn't present: either
      the most recent quarter didn't expand at all, the expansion streak
      doesn't clear 2 quarters, the 4 quarters before the streak began
      didn't net-decline, or the streak is so long it eats into all
      available history (no room left to check for a prior decline at all).
    - `armed` — exactly 1 quarter of expansion so far. Loaded, watching for
      a second confirming quarter; not yet evaluated against the
      prior-decline condition (that only matters once the streak clears 2).
    - `firing` — exactly 2 consecutive expanding quarters, immediately
      preceded by a validated 4-quarter net decline. The pattern has just
      completed.
    - `confirmed` — 3+ consecutive expanding quarters, immediately preceded
      by a validated 4-quarter net decline. The inflection has held up
      beyond the minimum bar.
    """
    n = len(quarterly_gross_margins)
    if n < min_quarters:
        return InflectionTrigger(
            status="insufficient_history",
            quarters_of_history=n,
            expansion_streak=0,
            prior_decline_pp=None,
            detail=(
                f"only {n} quarter(s) of gross-margin history available "
                f"(need >= {min_quarters}: 4 to establish the prior decline, "
                f"2 more for the minimum expansion streak)"
            ),
        )

    margins = quarterly_gross_margins

    # Longest run of consecutive sequential (QoQ) increases ending at the
    # most recent quarter.
    streak = 0
    i = n - 1
    while i > 0 and margins[i] > margins[i - 1]:
        streak += 1
        i -= 1
    # `i` now indexes the last quarter that did NOT extend the streak — the
    # quarter the streak's first increase was measured from. The 4 quarters
    # immediately before, AND INCLUDING, that quarter are the "prior
    # decline" window the definition requires.
    base_index = i

    if streak == 0:
        return InflectionTrigger(
            status="not_armed",
            quarters_of_history=n,
            expansion_streak=0,
            prior_decline_pp=None,
            detail="no gross-margin expansion in the most recent quarter",
        )

    if streak == 1:
        return InflectionTrigger(
            status="armed",
            quarters_of_history=n,
            expansion_streak=1,
            prior_decline_pp=None,
            detail="1 consecutive quarter of GM expansion — needs 2 to fire",
        )

    decline_start = base_index - 3
    if decline_start < 0:
        return InflectionTrigger(
            status="not_armed",
            quarters_of_history=n,
            expansion_streak=streak,
            prior_decline_pp=None,
            detail=(
                f"{streak} consecutive expanding quarters, but not enough "
                "history before them to check for a prior decline"
            ),
        )

    decline_window = margins[decline_start : base_index + 1]
    prior_decline_pp = (decline_window[0] - decline_window[-1]) * 100
    declined = decline_window[0] > decline_window[-1]

    if not declined:
        return InflectionTrigger(
            status="not_armed",
            quarters_of_history=n,
            expansion_streak=streak,
            prior_decline_pp=prior_decline_pp,
            detail=(
                f"{streak} consecutive expanding quarters, but the 4 "
                "quarters before them did not net-decline — not a margin "
                "inflection"
            ),
        )

    status: TriggerStatus = "firing" if streak == 2 else "confirmed"
    return InflectionTrigger(
        status=status,
        quarters_of_history=n,
        expansion_streak=streak,
        prior_decline_pp=prior_decline_pp,
        detail=(
            f"{streak} consecutive quarters of GM expansion following a "
            f"{prior_decline_pp:.1f}pp decline over the prior 4 quarters"
        ),
    )


__all__ = [
    "MIN_QUARTERS",
    "InflectionTrigger",
    "TriggerStatus",
    "evaluate_gross_margin_inflection",
]
