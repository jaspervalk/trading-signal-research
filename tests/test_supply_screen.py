"""`run_supply_screen`: batch runner wiring CIK resolution, cached fetch,
fundamentals assembly and metric scoring together.

No test hits the network — `ticker_to_cik` / `company_facts` are
monkeypatched at their `app.supply.screen` import site — and every test
passes `cache_dir=tmp_path` so nothing touches the real
`data/cache/edgar/` disk cache.
"""

from __future__ import annotations

from app.supply.metrics import Thresholds
from app.supply.screen import run_supply_screen

# --- fixtures -----------------------------------------------------------


def _dur(start: str, end: str, filed: str, val: float) -> dict:
    return {"start": start, "end": end, "filed": filed, "val": val, "form": "10-Q"}


def _instant(end: str, filed: str, val: float) -> dict:
    return {"end": end, "filed": filed, "val": val, "form": "10-Q"}


QUARTERS = [
    ("2024-01-01", "2024-03-31"),
    ("2024-04-01", "2024-06-30"),
    ("2024-07-01", "2024-09-30"),
    ("2024-10-01", "2024-12-31"),
]
GP_VALUES = [40.0, 50.0, 45.0, 55.0]
REVENUE_VALUES = [100.0, 100.0, 100.0, 100.0]
OCF_VALUES = [-10.0, 20.0, -5.0, 30.0]


def _full_facts() -> dict:
    """Four quarters of GP/revenue/OCF plus point-in-time PP&E and cash --
    enough for a TTM figure and, under lenient thresholds, a pass."""
    gp = [_dur(s, e, "2025-01-15", v) for (s, e), v in zip(QUARTERS, GP_VALUES)]
    rev = [_dur(s, e, "2025-01-15", v) for (s, e), v in zip(QUARTERS, REVENUE_VALUES)]
    ocf = [_dur(s, e, "2025-01-15", v) for (s, e), v in zip(QUARTERS, OCF_VALUES)]
    return {
        "facts": {
            "us-gaap": {
                "GrossProfit": {"units": {"USD": gp}},
                "Revenues": {"units": {"USD": rev}},
                # Balance-sheet facts: instant, no "start" -- deliberately
                # shaped like real EDGAR data, which `quarterly_series`
                # would exclude by its duration filter.
                "PropertyPlantAndEquipmentNet": {
                    "units": {"USD": [_instant("2024-12-31", "2025-01-15", 300.0)]}
                },
                "CashAndCashEquivalentsAtCarryingValue": {
                    "units": {"USD": [_instant("2024-12-31", "2025-01-15", 50.0)]}
                },
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf}},
            }
        }
    }


def _sparse_facts() -> dict:
    """Only 2 quarters -- too little even for a TTM figure (need 4)."""
    gp = [_dur(s, e, "2025-01-15", v) for (s, e), v in zip(QUARTERS[:2], GP_VALUES[:2])]
    rev = [_dur(s, e, "2025-01-15", v) for (s, e), v in zip(QUARTERS[:2], REVENUE_VALUES[:2])]
    return {
        "facts": {
            "us-gaap": {
                "GrossProfit": {"units": {"USD": gp}},
                "Revenues": {"units": {"USD": rev}},
            }
        }
    }


def _facts_missing_ppe() -> dict:
    facts = _full_facts()
    del facts["facts"]["us-gaap"]["PropertyPlantAndEquipmentNet"]
    return facts


# Thresholds tuned so any full 4-quarter fixture clears every criterion --
# isolates the runner's wiring/error-handling from metrics.passes() logic,
# which already has its own dedicated test suite.
LENIENT_THRESHOLDS = Thresholds(
    gm_percentile_max=1.0,
    margin_headroom_min_pp=-1000.0,
    earnings_torque_min=-1000.0,
    gm_volatility_min_pp=-1000.0,
    capital_intensity_min=-1000.0,
    survivability_quarters_min=-1000.0,
    analyst_count_max=999_999,
    min_quarters_history=4,
)


# --- tests ----------------------------------------------------------------


def test_passes_and_failures_are_both_returned(monkeypatch, tmp_path):
    """A batch with one clean pass and one wiring failure returns both --
    neither is dropped from the result list."""
    monkeypatch.setattr("app.supply.screen.ticker_to_cik", lambda: {"AAA": 1, "EEE": 2})
    monkeypatch.setattr("app.supply.screen.company_facts", lambda cik: _full_facts())

    results = run_supply_screen(
        ["AAA", "EEE"],
        market_caps={"AAA": 1000.0},  # EEE deliberately missing
        analyst_counts={},
        thresholds=LENIENT_THRESHOLDS,
        cache_dir=tmp_path,
    )

    assert len(results) == 2
    aaa = next(r for r in results if r.ticker == "AAA")
    eee = next(r for r in results if r.ticker == "EEE")

    assert aaa.passed is True
    assert aaa.metrics is not None
    assert aaa.reasons == []

    assert eee.passed is False
    assert eee.metrics is None
    assert "market cap" in eee.reasons[0]


def test_unresolvable_ticker_is_recorded_with_a_reason(monkeypatch, tmp_path):
    monkeypatch.setattr("app.supply.screen.ticker_to_cik", lambda: {})
    fetch_calls: list[int] = []
    monkeypatch.setattr(
        "app.supply.screen.company_facts",
        lambda cik: fetch_calls.append(cik) or _full_facts(),
    )

    results = run_supply_screen(
        ["ZZZ"], market_caps={}, analyst_counts={}, cache_dir=tmp_path
    )

    assert len(results) == 1
    r = results[0]
    assert r.cik is None
    assert r.metrics is None
    assert r.passed is False
    assert "no CIK found" in r.reasons[0]
    assert fetch_calls == []  # never attempted a fetch for an unresolved ticker


def test_fetch_exception_is_isolated_to_one_ticker(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.supply.screen.ticker_to_cik", lambda: {"GOOD": 1, "BAD": 2}
    )

    def fake_company_facts(cik: int) -> dict:
        if cik == 2:
            raise RuntimeError("503 Service Unavailable")
        return _full_facts()

    monkeypatch.setattr("app.supply.screen.company_facts", fake_company_facts)

    results = run_supply_screen(
        ["GOOD", "BAD"],
        market_caps={"GOOD": 1000.0, "BAD": 1000.0},
        analyst_counts={},
        thresholds=LENIENT_THRESHOLDS,
        cache_dir=tmp_path,
    )

    good = next(r for r in results if r.ticker == "GOOD")
    bad = next(r for r in results if r.ticker == "BAD")

    assert good.passed is True
    assert good.metrics is not None

    assert bad.metrics is None
    assert bad.passed is False
    assert "fetch failed" in bad.reasons[0]
    assert "503" in bad.reasons[0]


def test_cache_hit_avoids_a_second_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr("app.supply.screen.ticker_to_cik", lambda: {"AAA": 1})
    calls: list[int] = []

    def fake_company_facts(cik: int) -> dict:
        calls.append(cik)
        return _full_facts()

    monkeypatch.setattr("app.supply.screen.company_facts", fake_company_facts)

    kwargs = dict(
        market_caps={"AAA": 1000.0},
        analyst_counts={},
        thresholds=LENIENT_THRESHOLDS,
        cache_dir=tmp_path,
    )
    first = run_supply_screen(["AAA"], **kwargs)
    second = run_supply_screen(["AAA"], **kwargs)

    assert calls == [1]  # second run served entirely from disk cache
    assert first[0].passed is True
    assert second[0].passed is True


def test_cache_miss_after_ttl_expiry(monkeypatch, tmp_path):
    monkeypatch.setattr("app.supply.screen.ticker_to_cik", lambda: {"AAA": 1})
    calls: list[int] = []

    def fake_company_facts(cik: int) -> dict:
        calls.append(cik)
        return _full_facts()

    monkeypatch.setattr("app.supply.screen.company_facts", fake_company_facts)

    t0 = 1_700_000_000.0
    kwargs = dict(
        market_caps={"AAA": 1000.0},
        analyst_counts={},
        thresholds=LENIENT_THRESHOLDS,
        cache_dir=tmp_path,
    )
    run_supply_screen(["AAA"], now=t0, **kwargs)
    assert calls == [1]

    # Still within the 7-day TTL -- must stay a cache hit.
    run_supply_screen(["AAA"], now=t0 + 3 * 24 * 3600, **kwargs)
    assert calls == [1]

    # 8 days later -- past the 7-day TTL, must re-fetch.
    run_supply_screen(["AAA"], now=t0 + 8 * 24 * 3600, **kwargs)
    assert calls == [1, 1]


def test_too_little_history_is_recorded_with_a_reason(monkeypatch, tmp_path):
    monkeypatch.setattr("app.supply.screen.ticker_to_cik", lambda: {"THIN": 3})
    monkeypatch.setattr("app.supply.screen.company_facts", lambda cik: _sparse_facts())

    results = run_supply_screen(
        ["THIN"],
        market_caps={"THIN": 1000.0},
        analyst_counts={},
        thresholds=LENIENT_THRESHOLDS,
        cache_dir=tmp_path,
    )

    r = results[0]
    assert r.metrics is None
    assert r.passed is False
    assert "too little history" in r.reasons[0]


def test_cik_map_outage_records_every_ticker_with_a_reason(monkeypatch, tmp_path):
    """The one shared ticker->CIK lookup failing must not crash the whole
    batch -- every ticker still gets a `ScreenResult` with a reason."""

    def raise_outage() -> dict:
        raise RuntimeError("SEC ticker map unavailable")

    monkeypatch.setattr("app.supply.screen.ticker_to_cik", raise_outage)

    results = run_supply_screen(
        ["A", "B", "C"], market_caps={}, analyst_counts={}, cache_dir=tmp_path
    )

    assert len(results) == 3
    assert all(r.metrics is None and not r.passed for r in results)
    assert all("CIK map unavailable" in r.reasons[0] for r in results)


def test_missing_ppe_is_recorded_with_a_reason(monkeypatch, tmp_path):
    monkeypatch.setattr("app.supply.screen.ticker_to_cik", lambda: {"NOPPE": 4})
    monkeypatch.setattr(
        "app.supply.screen.company_facts", lambda cik: _facts_missing_ppe()
    )

    results = run_supply_screen(
        ["NOPPE"],
        market_caps={"NOPPE": 1000.0},
        analyst_counts={},
        thresholds=LENIENT_THRESHOLDS,
        cache_dir=tmp_path,
    )

    r = results[0]
    assert r.metrics is None
    assert r.passed is False
    assert "PP&E" in r.reasons[0]


def test_ticker_case_is_not_significant_for_lookups(monkeypatch, tmp_path):
    """`market_caps`/`analyst_counts` dicts are keyed however the caller
    likes; a lowercase ticker must still find an uppercase-keyed cap."""
    monkeypatch.setattr("app.supply.screen.ticker_to_cik", lambda: {"AAA": 1})
    monkeypatch.setattr("app.supply.screen.company_facts", lambda cik: _full_facts())

    results = run_supply_screen(
        ["aaa"],
        market_caps={"AAA": 1000.0},
        analyst_counts={},
        thresholds=LENIENT_THRESHOLDS,
        cache_dir=tmp_path,
    )

    assert results[0].passed is True
