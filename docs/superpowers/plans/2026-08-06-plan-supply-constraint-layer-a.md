# Supply-constraint screener — Layer A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The deterministic financial screen — find companies with fixed costs and depressed margins, ranked by how much gross profit a return to their own historical peak would add relative to market cap. No constraint knowledge, no LLM, no price data.

**Architecture:** A new `src/app/supply/` package. SEC EDGAR XBRL `companyfacts` supplies point-in-time quarterly fundamentals; yfinance supplies market cap and analyst coverage. Metrics are computed per ticker and filtered against pre-registered thresholds.

**Scope: Phase 1 only.** The constraint registry, the Layer B cut, Layer C triggers and the UI are explicitly **not** in this plan. Per the brief: do not build phase 2 until phase 1 returns recognisable names.

**Tech Stack:** Python 3.11, `requests`, Pydantic v2, pytest.

## Global Constraints

- Tests: `.venv/bin/python -m pytest` (bare `python` is NOT on PATH). Baseline **605 passing**; every task leaves the suite green.
- Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Work on `main`. Never commit `data/`, `CLAUDE.md`, `.env`.
- **No test may hit the network.** EDGAR access is monkeypatched; fixture JSON lives in the test file, not on disk.
- **SEC requires a declared User-Agent** with contact details, and rate-limits to 10 requests/second. Both are non-negotiable; SEC blocks offenders by IP.
- **Point-in-time is non-negotiable.** When a period appears in several filings, keep the **earliest** `filed` value. A restated figure is not what was knowable at the time.
- Thresholds are pre-registered in a config file. Never tune them to produce nicer output; changing one requires an explicit logged reason.
- No price targets, no forecasts, no recommendation. Layer A answers "who has margin headroom", never "buy this".

## Findings from the feasibility probe (2026-08-06) — read before implementing

Verified live against EDGAR:

1. **Tags fragment across eras and must have fallback chains.** Lumentum: `GrossProfit` covers 2013-2026, but the cost line is `CostOfRevenue` (2013-2017) then `CostOfGoodsAndServicesSold` (2017-2019), and revenue is split across `SalesRevenueNet`, `Revenues` and `RevenueFromContractWithCustomerExcludingAssessedTax` with a real gap 2019-2022. A single-tag lookup truncates history silently and corrupts every percentile.
2. **The same period appears in many filings.** A 10-Q reports the current quarter and the prior-year comparative, so Micron returned 287 raw `GrossProfit` points for ~52 distinct quarters. Deduplicate by period end, keeping the earliest filing.
3. **Duration filtering identifies quarters.** Quarterly facts span 80-100 days; annual spans ~365. Filter on `end - start`.
4. **`PropertyPlantAndEquipmentNet` stops in 2020 for Micron** — same tag-migration problem on the balance sheet. Capital intensity needs its own chain.
5. **The brief's Sandisk sanity check is not testable.** SNDK (CIK 2023554) has 12 quarters from 2023-12 only; it was spun out of Western Digital in 2025 and has no standalone decade. Substitute check in Task 5.
6. Micron currently prints GM at the 98th percentile of its own history with zero headroom — correctly *excluded* by the screen, which is a good early signal that the percentile logic is meaningful.

---

### Task 1: EDGAR client

**Files:**
- Create: `src/app/supply/__init__.py`, `src/app/supply/edgar.py`
- Test: `tests/test_supply_edgar.py`

**Interfaces:**
- Produces: `SEC_USER_AGENT`, `ticker_to_cik() -> dict[str, int]`, `company_facts(cik) -> dict`, `quarterly_series(facts, concepts) -> list[FactPoint]`, `FactPoint` (dataclass: `end: date`, `filed: date`, `value: float`). Tasks 2-3 consume these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_supply_edgar.py`:

```python
"""EDGAR fact extraction: duration filtering, point-in-time dedup, tag chains."""

from __future__ import annotations

from datetime import date

import pytest

from app.supply.edgar import FactPoint, quarterly_series

def _fact(start, end, filed, val):
    return {"start": start, "end": end, "filed": filed, "val": val, "form": "10-Q"}


FACTS = {
    "facts": {
        "us-gaap": {
            "GrossProfit": {
                "units": {
                    "USD": [
                        # ~91 days: a quarter.
                        _fact("2024-01-01", "2024-03-31", "2024-05-01", 100.0),
                        # the same quarter restated in a later filing
                        _fact("2024-01-01", "2024-03-31", "2025-05-01", 111.0),
                        _fact("2024-04-01", "2024-06-30", "2024-08-01", 120.0),
                        # ~365 days: annual, must be excluded
                        _fact("2024-01-01", "2024-12-31", "2025-02-01", 500.0),
                        # no start: instant fact, must be excluded
                        {"end": "2024-06-30", "filed": "2024-08-01", "val": 9.0, "form": "10-Q"},
                    ]
                }
            },
            "CostOfRevenue": {
                "units": {"USD": [_fact("2024-01-01", "2024-03-31", "2024-05-01", 60.0)]}
            },
        }
    }
}


def test_only_quarter_length_durations_are_kept():
    pts = quarterly_series(FACTS, ["GrossProfit"])
    assert [p.end for p in pts] == [date(2024, 3, 31), date(2024, 6, 30)]


def test_earliest_filing_wins_so_the_series_is_point_in_time():
    pts = quarterly_series(FACTS, ["GrossProfit"])
    q1 = next(p for p in pts if p.end == date(2024, 3, 31))
    assert q1.value == 100.0  # not the 111.0 restatement
    assert q1.filed == date(2024, 5, 1)


def test_series_is_sorted_by_period_end():
    pts = quarterly_series(FACTS, ["GrossProfit"])
    assert [p.end for p in pts] == sorted(p.end for p in pts)


def test_tag_chain_falls_through_to_the_first_concept_present():
    pts = quarterly_series(FACTS, ["CostOfGoodsAndServicesSold", "CostOfRevenue"])
    assert len(pts) == 1
    assert pts[0].value == 60.0


def test_tag_chain_merges_across_concepts_without_double_counting():
    """Eras use different tags; a period covered by both takes the first listed."""
    facts = {
        "facts": {
            "us-gaap": {
                "A": {"units": {"USD": [_fact("2024-01-01", "2024-03-31", "2024-05-01", 1.0)]}},
                "B": {
                    "units": {
                        "USD": [
                            _fact("2024-01-01", "2024-03-31", "2024-05-01", 99.0),
                            _fact("2024-04-01", "2024-06-30", "2024-08-01", 2.0),
                        ]
                    }
                },
            }
        }
    }
    pts = quarterly_series(facts, ["A", "B"])
    assert [(p.end, p.value) for p in pts] == [
        (date(2024, 3, 31), 1.0),
        (date(2024, 6, 30), 2.0),
    ]


def test_missing_concepts_yield_an_empty_series():
    assert quarterly_series(FACTS, ["NoSuchConcept"]) == []


def test_user_agent_declares_contact_details():
    """SEC blocks anonymous clients by IP; the header is not optional."""
    from app.supply.edgar import SEC_USER_AGENT

    assert "@" in SEC_USER_AGENT
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_supply_edgar.py -q`
Expected: FAIL — no module `app.supply.edgar`.

- [ ] **Step 3: Write `src/app/supply/__init__.py`**

```python
"""Supply-constraint screener (Layer A).

Finds companies whose gross margins are depressed against their own history and
whose cost base is fixed enough that a margin recovery would land mostly in
profit. It knows nothing about supply shortages: the shortage side of the
pattern lives in a hand-curated registry and is joined in later.

Layer A is a coarse net. Expect several hundred hits and no opinion about any
of them.
"""
```

- [ ] **Step 4: Write `src/app/supply/edgar.py`**

```python
"""SEC EDGAR XBRL companyfacts access.

Three things about this data cost real correctness if ignored, all verified
against live filings on 2026-08-06.

**Tags migrate.** Lumentum's cost line is `CostOfRevenue` until 2017 and
`CostOfGoodsAndServicesSold` after; Micron's `PropertyPlantAndEquipmentNet`
stops in 2020. A single-concept lookup silently truncates history, and a
percentile computed over a truncated history is wrong without looking wrong.
Every metric therefore reads through an ordered chain of concepts.

**The same period is reported many times.** A 10-Q carries the current quarter
and the prior-year comparative, so Micron returns 287 raw GrossProfit points
for about 52 distinct quarters. We deduplicate on period end and keep the
EARLIEST filing, which is also what makes the series point-in-time: a restated
figure is not what was knowable at the time.

**Duration identifies the period type.** Quarterly facts span 80-100 days,
annual about 365, and balance-sheet facts have no start at all.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import requests

from app.logging import get_logger

log = get_logger(__name__)

# SEC requires a declared identity and rate-limits to 10 requests/second.
# Anonymous or aggressive clients are blocked by IP.
SEC_USER_AGENT = "trading-signal-research jaspermvalk@gmail.com"
_HEADERS = {"User-Agent": SEC_USER_AGENT}
_MIN_INTERVAL_S = 0.15  # ~6.7 req/s, comfortably inside the limit

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

QUARTER_MIN_DAYS = 80
QUARTER_MAX_DAYS = 100

_last_request_at = 0.0


@dataclass(frozen=True)
class FactPoint:
    """One reported figure for one period, as first filed."""

    end: date
    filed: date
    value: float


def _throttle() -> None:
    global _last_request_at
    wait = _MIN_INTERVAL_S - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _get_json(url: str) -> dict[str, Any]:
    _throttle()
    response = requests.get(url, headers=_HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def ticker_to_cik() -> dict[str, int]:
    """Map uppercase ticker to CIK. One request; callers should cache."""
    payload = _get_json(TICKER_MAP_URL)
    return {
        str(row["ticker"]).upper(): int(row["cik_str"])
        for row in payload.values()
        if row.get("ticker")
    }


def company_facts(cik: int) -> dict[str, Any]:
    """Full companyfacts document for one filer. Large; cache it."""
    return _get_json(FACTS_URL.format(cik=cik))


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def quarterly_series(facts: dict[str, Any], concepts: list[str]) -> list[FactPoint]:
    """Quarterly points for the first concept in `concepts` covering each period.

    The chain is ordered by preference: an earlier concept wins a period that
    several concepts report, which is how eras using different tags stitch into
    one continuous series without double counting.
    """
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    by_end: dict[date, FactPoint] = {}

    for concept in concepts:
        node = gaap.get(concept)
        if not node:
            continue
        for raw in (node.get("units") or {}).get("USD") or []:
            start = _parse_date(raw.get("start", ""))
            end = _parse_date(raw.get("end", ""))
            filed = _parse_date(raw.get("filed", ""))
            if start is None or end is None or filed is None:
                continue
            days = (end - start).days
            if days < QUARTER_MIN_DAYS or days > QUARTER_MAX_DAYS:
                continue
            value = raw.get("val")
            if value is None:
                continue

            existing = by_end.get(end)
            # An earlier concept in the chain owns the period outright. Within
            # one concept, the earliest filing wins.
            if existing is not None and existing.filed <= filed:
                continue
            by_end[end] = FactPoint(end=end, filed=filed, value=float(value))

        # Freeze what this concept covered before consulting the next one.
        covered = set(by_end)
        for later in concepts[concepts.index(concept) + 1 :]:
            _ = later  # readability: later concepts may only fill gaps
        del covered

    return sorted(by_end.values(), key=lambda p: p.end)


__all__ = [
    "FactPoint",
    "SEC_USER_AGENT",
    "company_facts",
    "quarterly_series",
    "ticker_to_cik",
]
```

**Implementer note:** the loop above must ensure a *later* concept never overwrites a period an *earlier* concept already supplied. Read the tests, particularly `test_tag_chain_merges_across_concepts_without_double_counting`, and simplify the implementation until it passes cleanly — the sketch above is deliberately explicit rather than clever, and the trailing `covered` block is dead code you should delete once you have the ownership rule right.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_supply_edgar.py -q`
Expected: 7 passed.

- [ ] **Step 6: Full suite and commit**

Run: `.venv/bin/python -m pytest -q` — expected 612 passed.

```bash
git add src/app/supply tests/test_supply_edgar.py
git commit -m "feat(supply): EDGAR companyfacts client with point-in-time fact extraction

Tags migrate between eras and the same period is reported in many filings, so
facts read through ordered concept chains and deduplicate on period end
keeping the earliest filing.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Concept chains and the margin history

**Files:**
- Create: `src/app/supply/fundamentals.py`
- Test: `tests/test_supply_fundamentals.py`

**Interfaces:**
- Consumes: Task 1's `quarterly_series`, `FactPoint`.
- Produces: `GROSS_PROFIT`, `COST_OF_REVENUE`, `REVENUE`, `PPE`, `CASH`, `OPERATING_CASH_FLOW` (concept chains), `MarginHistory` (dataclass: `quarters`, `gross_margins`, `revenues`, `first_end`, `last_end`), `build_margin_history(facts) -> MarginHistory`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_supply_fundamentals.py`:

```python
"""Gross-margin history assembled from EDGAR facts."""

from __future__ import annotations

from datetime import date

from app.supply.fundamentals import build_margin_history


def _q(start, end, filed, val):
    return {"start": start, "end": end, "filed": filed, "val": val, "form": "10-Q"}


def _facts(gp, cost=None, rev=None):
    gaap = {"GrossProfit": {"units": {"USD": gp}}}
    if cost:
        gaap["CostOfRevenue"] = {"units": {"USD": cost}}
    if rev:
        gaap["Revenues"] = {"units": {"USD": rev}}
    return {"facts": {"us-gaap": gaap}}


def test_margin_uses_revenue_when_the_revenue_tag_is_present():
    facts = _facts(
        gp=[_q("2024-01-01", "2024-03-31", "2024-05-01", 40.0)],
        rev=[_q("2024-01-01", "2024-03-31", "2024-05-01", 100.0)],
    )
    h = build_margin_history(facts)
    assert h.gross_margins == [0.40]
    assert h.revenues == [100.0]


def test_margin_falls_back_to_gross_profit_plus_cost():
    """The revenue tag migrated for many filers; profit and cost did not."""
    facts = _facts(
        gp=[_q("2024-01-01", "2024-03-31", "2024-05-01", 40.0)],
        cost=[_q("2024-01-01", "2024-03-31", "2024-05-01", 60.0)],
    )
    h = build_margin_history(facts)
    assert h.gross_margins == [0.40]
    assert h.revenues == [100.0]


def test_a_quarter_with_neither_revenue_nor_cost_is_dropped():
    facts = _facts(gp=[_q("2024-01-01", "2024-03-31", "2024-05-01", 40.0)])
    assert build_margin_history(facts).quarters == 0


def test_non_positive_revenue_is_dropped_rather_than_producing_a_wild_margin():
    facts = _facts(
        gp=[_q("2024-01-01", "2024-03-31", "2024-05-01", 40.0)],
        rev=[_q("2024-01-01", "2024-03-31", "2024-05-01", 0.0)],
    )
    assert build_margin_history(facts).quarters == 0


def test_history_reports_its_own_span_so_coverage_is_visible():
    facts = _facts(
        gp=[
            _q("2024-01-01", "2024-03-31", "2024-05-01", 40.0),
            _q("2024-04-01", "2024-06-30", "2024-08-01", 50.0),
        ],
        rev=[
            _q("2024-01-01", "2024-03-31", "2024-05-01", 100.0),
            _q("2024-04-01", "2024-06-30", "2024-08-01", 100.0),
        ],
    )
    h = build_margin_history(facts)
    assert h.quarters == 2
    assert h.first_end == date(2024, 3, 31)
    assert h.last_end == date(2024, 6, 30)


def test_empty_facts_produce_an_empty_history_not_an_error():
    h = build_margin_history({"facts": {"us-gaap": {}}})
    assert h.quarters == 0
    assert h.gross_margins == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_supply_fundamentals.py -q`
Expected: FAIL — no module.

- [ ] **Step 3: Write `src/app/supply/fundamentals.py`**

Define the concept chains as module constants, ordered most-preferred first:

```python
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
```

`build_margin_history` walks the gross-profit series and, for each period, resolves revenue by first consulting the revenue chain and falling back to `gross_profit + cost_of_revenue`. Periods where neither resolves, or where revenue is not positive, are dropped. The returned `MarginHistory` carries `quarters`, `gross_margins`, `revenues`, `first_end` and `last_end`, so a caller can see how much history actually backs a percentile.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_supply_fundamentals.py -q`
Expected: 6 passed.

- [ ] **Step 5: Full suite and commit**

Run: `.venv/bin/python -m pytest -q` — expected 618 passed.

```bash
git add src/app/supply/fundamentals.py tests/test_supply_fundamentals.py
git commit -m "feat(supply): gross-margin history with revenue fallback across tag eras

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Layer A metrics

**Files:**
- Create: `src/app/supply/metrics.py`, `configs/supply_screen.yaml`
- Test: `tests/test_supply_metrics.py`

**Interfaces:**
- Consumes: Task 2's `MarginHistory`.
- Produces: `SupplyMetrics` (Pydantic), `compute_metrics(...) -> SupplyMetrics`, `Thresholds`, `load_thresholds()`, `passes(metrics, thresholds) -> tuple[bool, list[str]]`.

Every formula is fixed by the brief. Implement exactly; do not tune.

| Metric | Formula |
|---|---|
| `gm_percentile` | share of historical quarterly GMs below the current TTM GM |
| `margin_headroom` | `max(historical GM) - current TTM GM`, in percentage points |
| `earnings_torque` | `margin_headroom_fraction * ttm_revenue / market_cap` |
| `gm_volatility` | population stdev of historical GMs, in percentage points |
| `capital_intensity` | `ppe / ttm_revenue` |
| `survivability_quarters` | `(cash + max(ttm_operating_cash_flow, 0)) / quarterly_burn`, where burn is the mean of negative quarterly operating cash flows; **infinite when the company is cash generative** |
| `analyst_count` | yfinance `numberOfAnalystOpinions` |

- [ ] **Step 1: Write the failing test**

Create `tests/test_supply_metrics.py` covering, at minimum:

```python
def test_earnings_torque_is_headroom_times_revenue_over_market_cap():
    """The brief's headline metric: 20pp of headroom on 1000 revenue against a
    2000 market cap returns 0.10 of market cap in annual gross profit."""
    # headroom 0.20 * 1000 / 2000 == 0.10

def test_percentile_places_current_margin_within_its_own_history()
def test_headroom_is_zero_at_the_historical_peak()
def test_survivability_is_infinite_for_a_cash_generative_company()
def test_survivability_counts_quarters_of_runway_when_burning()
def test_a_short_history_is_rejected_rather_than_percentiled()
    """Fewer than MIN_QUARTERS makes a percentile meaningless. SanDisk has 12
    quarters and would otherwise score against its own two-year window."""
def test_passes_returns_every_failing_reason_not_just_the_first()
def test_thresholds_come_from_config_not_from_code()
```

Add `MIN_QUARTERS = 24` (six years) as the floor below which metrics are refused, and make the failure explicit in `passes` rather than silent.

- [ ] **Step 2-4: Red, implement, green.**

`configs/supply_screen.yaml` holds the thresholds verbatim from the brief:

```yaml
# Pre-registered thresholds. Changing one requires an explicit reason in the
# commit message: tuning these against output is how a screen becomes an
# overfit story generator.
gm_percentile_max: 0.40
margin_headroom_min_pp: 10.0
earnings_torque_min: 0.25
gm_volatility_min_pp: 5.0
capital_intensity_min: 0.5
survivability_quarters_min: 8
analyst_count_max: 12
min_quarters_history: 24
```

- [ ] **Step 5: Full suite and commit.**

---

### Task 4: Screen runner with disk cache

**Files:**
- Create: `src/app/supply/screen.py`, `src/app/supply/cache.py`
- Test: `tests/test_supply_screen.py`

**Interfaces:**
- Produces: `run_supply_screen(tickers, *, market_caps, analyst_counts) -> list[SupplyMetrics]`.

Cache companyfacts on disk under `data/cache/edgar/CIK{cik}.json` with a 7-day TTL — filings do not change intraday, and the documents are large. Mirror the existing screener cache in `src/app/screener/cache.py` for consistency.

A ticker that fails to resolve, fails to fetch, or has too little history is **skipped with a recorded reason**, never silently dropped. The runner returns both passes and failures so Task 5 can report coverage honestly.

Tests monkeypatch the EDGAR client; no network.

- [ ] Red, implement, green, commit.

---

### Task 5: CLI and the phase-1 gate

**Files:**
- Modify: `src/app/cli.py`
- Test: `tests/test_cli_supply.py` (help text only, no DB or network)

**Interfaces:**
- Produces: `tsr supply-screen [--limit 50] [--universe PATH] [--json]`.

- [ ] **Step 1: Add the command.** Print a table of ticker, gm_percentile, margin_headroom, earnings_torque, capital_intensity, survivability_quarters, quarters_of_history, and the failing criteria for near-misses. Also print coverage: how many tickers resolved, how many had enough history, how many passed.

- [ ] **Step 2: Run it for real over `configs/screen_universe.csv`** and paste the top 50 into the task report.

- [ ] **Step 3: The gate.** The brief requires that phase 1 return recognisable, visibly cyclical, margin-depressed names before anything else is built.

  The brief's own Sandisk check is **not usable**: SNDK has 12 quarters of EDGAR history because it was spun out of Western Digital in 2025. Use these instead:
  - **Micron (MU) must NOT pass.** It currently prints GM at the 98th percentile of its own history with zero headroom. If it passes, the percentile or headroom maths is inverted.
  - **At least half the top 20 must be businesses a reader would recognise as cyclical and currently depressed** — semiconductors, materials, shipping, energy services, hardware. A top 20 full of software names means gross margin history is being read wrong.
  - **`quarters_of_history` must be reported per row.** Any row with fewer than 24 quarters should not be there at all.

  Report the result plainly. If the gate fails, stop and report rather than proceeding — the screen is the product.

- [ ] **Step 4: Commit.**

---

## Explicitly not in this plan

The constraint registry and `constraint_exposure` tables, the Layer B cut and scoring, Layer C inflection triggers, and the UI. All wait on the Task 5 gate.
