"""Quarterly gross-margin history assembled from EDGAR XBRL facts.

Revenue tags migrate between eras just like the cost-of-revenue tags
`quarterly_series` already stitches (see `app.supply.edgar`). Verified live
on 2026-08-06: Lumentum's cost line is `CostOfRevenue` until 2017 then
`CostOfGoodsAndServicesSold`, and its revenue is spread across
`SalesRevenueNet`, `Revenues` and
`RevenueFromContractWithCustomerExcludingAssessedTax` with a genuine gap
between eras. So each quarter resolves revenue two ways: the revenue concept
chain first, then `gross_profit + cost_of_revenue` as a fallback. Neither
route alone gives continuous coverage.

`MarginHistory` always reports `quarters`, `first_end` and `last_end` because
a percentile computed over a truncated history is wrong WITHOUT LOOKING
WRONG — callers (see Task 3) need the coverage visible, not just the numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.supply.edgar import quarterly_series

GROSS_PROFIT = ["GrossProfit"]

COST_OF_REVENUE = [
    "CostOfGoodsAndServicesSold",
    "CostOfRevenue",
    "CostOfGoodsSold",
    "CostOfServices",
]

REVENUE = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueGoodsNet",
]

PPE = [
    "PropertyPlantAndEquipmentNet",
    "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
]

CASH = [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
]

OPERATING_CASH_FLOW = [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
]


@dataclass(frozen=True)
class MarginHistory:
    """Quarterly gross-margin series, with its own coverage span attached.

    `quarters`, `first_end` and `last_end` let a caller see how much history
    actually backs a percentile before trusting it (Task 3 refuses to
    compute below 24 quarters using exactly these fields).
    """

    quarters: int
    gross_margins: list[float]
    revenues: list[float]
    first_end: date | None
    last_end: date | None
    dropped_implausible: int = 0


def build_margin_history(facts: dict[str, Any]) -> MarginHistory:
    """Assemble a quarterly gross-margin history from raw EDGAR companyfacts.

    Walks the gross-profit series and, for each period, resolves revenue by
    first consulting the revenue concept chain and falling back to
    `gross_profit + cost_of_revenue`. Periods where neither resolves, or
    where the resolved revenue is not positive, are dropped rather than
    producing a wild or undefined margin.

    A gross margin outside `(-1.0, 1.0]` is arithmetically impossible: profit
    cannot exceed revenue, and cost cannot exceed revenue by more than
    revenue itself without that being a tagging artefact in this dataset (see
    Q4 10-K note below). Those quarters are dropped and counted in
    `dropped_implausible` rather than silently corrupting a percentile — this
    is a hard mathematical constraint, not a tunable threshold.

    The most common source of an implausible margin: in Q4 a company files a
    10-K rather than a 10-Q, so no consolidated quarterly revenue fact exists
    for that period. The 80-100 day duration filter in `quarterly_series`
    then may pick up a smaller-scoped fact (a segment or corporate line)
    whose value is an order of magnitude too small, while gross profit
    resolves correctly — producing a wildly overstated margin.
    """
    gross_profit_points = quarterly_series(facts, GROSS_PROFIT)
    revenue_by_end = {p.end: p.value for p in quarterly_series(facts, REVENUE)}
    cost_by_end = {p.end: p.value for p in quarterly_series(facts, COST_OF_REVENUE)}

    margins: list[float] = []
    revenues: list[float] = []
    ends: list[date] = []
    dropped_implausible = 0

    for point in gross_profit_points:
        revenue = revenue_by_end.get(point.end)
        if revenue is None:
            cost = cost_by_end.get(point.end)
            if cost is None:
                continue
            revenue = point.value + cost

        if revenue <= 0:
            continue

        margin = point.value / revenue
        if not (-1.0 < margin <= 1.0):
            dropped_implausible += 1
            continue

        margins.append(margin)
        revenues.append(revenue)
        ends.append(point.end)

    return MarginHistory(
        quarters=len(ends),
        gross_margins=margins,
        revenues=revenues,
        first_end=ends[0] if ends else None,
        last_end=ends[-1] if ends else None,
        dropped_implausible=dropped_implausible,
    )


__all__ = [
    "GROSS_PROFIT",
    "COST_OF_REVENUE",
    "REVENUE",
    "PPE",
    "CASH",
    "OPERATING_CASH_FLOW",
    "MarginHistory",
    "build_margin_history",
]
