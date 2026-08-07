"""End-market concentration reporting for the supply-constraint screen.

**REPORTING ONLY.** This module counts how many of the top-N ranked rows
share a single `end_market` — it never weights, filters, or reorders
anything. That's an explicit constraint from the project owner: the Layer A
screen has no sector awareness, so a run can quietly repeat the exact same
macro bet under the cover of "different companies" (TROX/KRO/CC all sell
TiO2 pigment, HUN sells MDI, WLK sells PVC — five different products, one
end market: construction & housing) — the same failure mode a portfolio
factor module was already built to catch. This module makes that visible;
it does not fix it by touching the ranking.

`end_market` (`configs/supply_universe.csv`, `UniverseEntry.end_market`) is
deliberately coarser than `sector` — TiO2 and PVC are different GICS
sub-industries but the same end market. That's the whole point: `sector`
alone cannot see this concentration, `end_market` can.

Rows with a blank/unknown `end_market` are EXCLUDED from the tally, not
folded into a phantom "" group — an unclassified ticker must never inflate,
or itself constitute, any market's concentration count.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Optional

# Example from the owner's own review: "4 of top 5 share end market
# 'construction'." Used whenever a caller doesn't override top_n.
DEFAULT_TOP_N = 5


@dataclass(frozen=True)
class EndMarketConcentration:
    """Concentration of a single end market over the top `top_n` ranked rows."""

    top_n: int
    rows_considered: int  # rows actually available, <= top_n
    rows_classified: int  # of those, how many carried a non-blank end_market
    dominant_end_market: Optional[str]
    dominant_count: int
    # end_market -> count, blanks excluded, sorted by count descending.
    breakdown: dict[str, int]

    @property
    def summary_line(self) -> str:
        """Human-readable line, e.g. "4 of top 5 share end market 'construction'"."""
        if self.dominant_end_market is None or self.dominant_count < 2:
            return f"no repeated end market in the top {self.top_n}"
        return (
            f"{self.dominant_count} of top {self.top_n} share end market "
            f"{self.dominant_end_market!r}"
        )


def compute_end_market_concentration(
    tickers_in_rank_order: list[str],
    end_market_by_ticker: dict[str, Optional[str]],
    *,
    top_n: int = DEFAULT_TOP_N,
) -> EndMarketConcentration:
    """Tally end-market repetition over the first `top_n` of `tickers_in_rank_order`.

    `tickers_in_rank_order` is expected to already be in the screen's final
    ranked order (post Change-1 tiering) — this function does not sort.
    `end_market_by_ticker` should be keyed uppercase (same convention as the
    rest of `app.supply`); a missing or blank value for a ticker excludes it
    from the tally rather than forming its own group.
    """
    top = tickers_in_rank_order[:top_n]
    counts: Counter[str] = Counter()
    classified = 0
    for ticker in top:
        end_market = (end_market_by_ticker.get(ticker.upper()) or "").strip()
        if not end_market:
            continue
        classified += 1
        counts[end_market] += 1

    if counts:
        dominant_end_market, dominant_count = counts.most_common(1)[0]
    else:
        dominant_end_market, dominant_count = None, 0

    return EndMarketConcentration(
        top_n=top_n,
        rows_considered=len(top),
        rows_classified=classified,
        dominant_end_market=dominant_end_market,
        dominant_count=dominant_count,
        breakdown=dict(counts.most_common()),
    )


__all__ = ["DEFAULT_TOP_N", "EndMarketConcentration", "compute_end_market_concentration"]
