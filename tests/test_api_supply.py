"""Supply-constraint screener API: shape + wiring tests.

No network: `_fetch_supply_market_data`, `run_supply_screen`, `load_universe`,
`ticker_to_cik`, `company_facts` and the EDGAR disk cache are all
monkeypatched at their import site in `apps.api.app.routes.supply`. No test
writes into the real `data/cache/edgar/` — `edgar_cache.get`/`put` are
monkeypatched to no-ops wherever `get_margin_history` would otherwise touch
disk.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

import apps.api.app.routes.supply as supply_routes
from app.screener.universe import UniverseEntry
from app.supply.constraints import Constraint, ConstraintExposure
from app.supply.metrics import SupplyMetrics
from app.supply.screen import ScreenResult
from app.supply.triggers import evaluate_gross_margin_inflection


@pytest.fixture
def client():
    from apps.api.app.main import app

    return TestClient(app)


def _universe():
    return [
        UniverseEntry(
            ticker="WDC", name="Western Digital", sector="Technology", end_market="datacenter"
        ),
        UniverseEntry(
            ticker="MU", name="Micron", sector="Technology", end_market="datacenter"
        ),
        UniverseEntry(
            ticker="ZZZ", name="Nothing Found", sector="Industrials", end_market=""
        ),
    ]


def _metrics(**overrides) -> SupplyMetrics:
    base = dict(
        quarters_of_history=24,
        sufficient_history=True,
        gm_percentile=0.1,
        margin_headroom_pp=22.6,
        earnings_torque=0.4,
        gm_volatility_pp=6.0,
        capital_intensity=0.6,
        survivability_quarters=25.0,
        analyst_count=8,
        coverage_multiplier=1.5,
        caveats=[],
    )
    base.update(overrides)
    return SupplyMetrics(**base)


def _screen_results():
    # A clean inflection: 4-quarter decline (.30 -> .20) then 2 expanding
    # quarters -- exercises the "firing" path end to end through the API.
    wdc_trigger = evaluate_gross_margin_inflection([0.30, 0.28, 0.26, 0.20, 0.22, 0.25])
    return [
        ScreenResult(
            ticker="WDC",
            cik=106040,
            metrics=_metrics(earnings_torque=0.4),
            passed=True,
            reasons=[],
            dropped_implausible=1,
            trigger=wdc_trigger,
        ),
        ScreenResult(
            ticker="MU",
            cik=723125,
            metrics=_metrics(
                sufficient_history=False,
                gm_percentile=None,
                margin_headroom_pp=None,
                earnings_torque=None,
                gm_volatility_pp=None,
                quarters_of_history=10,
            ),
            passed=False,
            reasons=["insufficient_history: 10 quarters (need >= 24)"],
            dropped_implausible=0,
            trigger=evaluate_gross_margin_inflection([0.30, 0.28, 0.26]),
        ),
        ScreenResult(
            ticker="ZZZ",
            cik=None,
            metrics=None,
            passed=False,
            reasons=["no CIK found for ticker 'ZZZ'"],
            dropped_implausible=0,
        ),
    ]


@pytest.fixture(autouse=True)
def _patch_screen_sourcing(monkeypatch):
    monkeypatch.setattr(supply_routes, "load_universe", lambda path: _universe())
    monkeypatch.setattr(
        supply_routes,
        "_fetch_supply_market_data",
        lambda tickers: ({"WDC": 2.0e10, "MU": 9.0e10}, {"WDC": 8, "MU": 20}),
    )
    monkeypatch.setattr(
        supply_routes,
        "run_supply_screen",
        lambda tickers, **kw: _screen_results(),
    )


# --- GET /supply/screen -----------------------------------------------------


def test_get_screen_returns_expected_shape(client):
    r = client.get("/supply/screen?limit=50")
    assert r.status_code == 200
    body = r.json()

    assert "as_of" in body
    assert body["coverage"] == {
        "universe": 3,
        "resolved": 2,
        "sufficient_history": 1,
        "passed": 1,
    }
    assert len(body["rows"]) == 3

    wdc = next(row for row in body["rows"] if row["ticker"] == "WDC")
    assert wdc["passed"] is True
    assert wdc["sector"] == "Technology"
    assert wdc["end_market"] == "datacenter"
    assert wdc["dropped_implausible"] == 1
    assert wdc["trigger"]["status"] == "firing"
    assert wdc["trigger"]["draws_attention"] is True
    for key in (
        "quarters_of_history",
        "analyst_count",
        "coverage_multiplier",
        "gm_percentile",
        "margin_headroom_pp",
        "earnings_torque",
        "capital_intensity",
        "survivability_quarters",
    ):
        assert key in wdc["metrics"]


def test_get_screen_row_without_metrics_is_still_reported(client):
    r = client.get("/supply/screen")
    body = r.json()
    zzz = next(row for row in body["rows"] if row["ticker"] == "ZZZ")
    assert zzz["metrics"] is None
    assert zzz["passed"] is False
    assert "no CIK found" in zzz["reasons"][0]


def test_get_screen_ranked_by_earnings_torque_descending(client):
    r = client.get("/supply/screen")
    tickers = [row["ticker"] for row in r.json()["rows"]]
    # WDC has torque=0.4 (highest, computable); MU/ZZZ have none — WDC first.
    assert tickers[0] == "WDC"


def test_get_screen_limit_truncates_rows_not_coverage(client):
    r = client.get("/supply/screen?limit=1")
    body = r.json()
    assert len(body["rows"]) == 1
    assert body["coverage"]["universe"] == 3


# --- end-market concentration (Change 2, reporting only) ---------------------


def test_get_screen_includes_concentration_block(client):
    r = client.get("/supply/screen")
    body = r.json()
    assert "concentration" in body
    c = body["concentration"]
    assert c["top_n"] == 5
    # WDC + MU both classified "datacenter"; ZZZ unclassified (blank).
    assert c["rows_classified"] == 2
    assert c["dominant_end_market"] == "datacenter"
    assert c["dominant_count"] == 2
    assert "datacenter" in c["summary"]


def test_concentration_never_changes_row_order(client):
    """Reporting only: the concentration block must not affect which row
    is ranked first (still earnings_torque within the passing tier)."""
    r = client.get("/supply/screen")
    body = r.json()
    assert body["rows"][0]["ticker"] == "WDC"


# --- trigger status (Change 4) ------------------------------------------------


def test_get_screen_row_without_a_trigger_is_null(client):
    r = client.get("/supply/screen")
    zzz = next(row for row in r.json()["rows"] if row["ticker"] == "ZZZ")
    assert zzz["trigger"] is None


def test_get_screen_quiet_trigger_does_not_draw_attention(client):
    r = client.get("/supply/screen")
    mu = next(row for row in r.json()["rows"] if row["ticker"] == "MU")
    assert mu["trigger"] is not None
    assert mu["trigger"]["draws_attention"] is False


# --- POST /supply/screen/refresh --------------------------------------------


def test_refresh_screen_clears_cache_and_recomputes(client, monkeypatch):
    calls = {"n": 0}

    def _fake_clear():
        calls["n"] += 1
        return 7

    monkeypatch.setattr(supply_routes.edgar_cache, "clear", _fake_clear)

    r = client.post("/supply/screen/refresh")
    assert r.status_code == 200
    assert calls["n"] == 1
    assert r.json()["coverage"]["universe"] == 3


def test_refresh_screen_guards_concurrent_runs(client, monkeypatch):
    monkeypatch.setattr(supply_routes.edgar_cache, "clear", lambda: 0)
    assert supply_routes._refresh_lock.acquire(blocking=False)
    try:
        r = client.post("/supply/screen/refresh")
        assert r.status_code == 409
    finally:
        supply_routes._refresh_lock.release()


# --- GET /supply/constraints -------------------------------------------------


def _constraint(**overrides) -> Constraint:
    base = dict(
        id="nand_2024_2025_deficit",
        market="NAND flash memory",
        deficit_pct=0.045,
        deficit_source="Some source (2026)",
        deficit_horizon="2024-2025",
        expansion_lead_months=18,
        demand_driver="AI/datacenter demand",
        capacity_history="not verified",
        confidence="high",
        last_reviewed=date(2026, 8, 6),
        exposures=[
            ConstraintExposure(
                ticker="WDC",
                revenue_exposure_pct=0.5,
                exposure_source="unverified estimate",
                is_pure_play=False,
            )
        ],
    )
    base.update(overrides)
    c = Constraint(**base)
    c.is_stale = overrides.get("is_stale", False)
    return c


def test_get_constraints_returns_registry_with_exposures(client, monkeypatch):
    monkeypatch.setattr(supply_routes, "load_constraints", lambda: [_constraint()])
    r = client.get("/supply/constraints")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["id"] == "nand_2024_2025_deficit"
    assert body[0]["is_stale"] is False
    assert body[0]["exposures"] == [
        {
            "ticker": "WDC",
            "revenue_exposure_pct": 0.5,
            "exposure_source": "unverified estimate",
            "is_pure_play": False,
        }
    ]


def test_get_constraints_serializes_null_deficit_pct_and_counter_evidence(client, monkeypatch):
    tio2 = _constraint(
        id="tio2_test",
        deficit_pct=None,
        expansion_lead_months=None,
        confidence="low",
        counter_evidence="Chinese exports are filling the gap.",
    )
    monkeypatch.setattr(supply_routes, "load_constraints", lambda: [tio2])
    r = client.get("/supply/constraints")
    body = r.json()[0]
    assert body["deficit_pct"] is None
    assert body["expansion_lead_months"] is None
    assert body["confidence"] == "low"
    assert body["counter_evidence"] == "Chinese exports are filling the gap."


def test_get_constraints_surfaces_stale_flag(client, monkeypatch):
    stale = _constraint(last_reviewed=date(2020, 1, 1))
    stale.is_stale = True
    monkeypatch.setattr(supply_routes, "load_constraints", lambda: [stale])
    r = client.get("/supply/constraints")
    assert r.json()[0]["is_stale"] is True


# --- GET /supply/margin-history/{ticker} ------------------------------------


def _dur(start: str, end: str, filed: str, val: float) -> dict:
    return {"start": start, "end": end, "filed": filed, "val": val, "form": "10-Q"}


def _facts_for_margin_history() -> dict:
    quarters = [
        ("2024-01-01", "2024-03-31"),
        ("2024-04-01", "2024-06-30"),
        ("2024-07-01", "2024-09-30"),
        ("2024-10-01", "2024-12-31"),
    ]
    gp = [40.0, 30.0, 45.0, 20.0]
    rev = [100.0, 100.0, 100.0, 100.0]
    return {
        "facts": {
            "us-gaap": {
                "GrossProfit": {
                    "units": {
                        "USD": [
                            _dur(s, e, "2025-01-15", v)
                            for (s, e), v in zip(quarters, gp)
                        ]
                    }
                },
                "Revenues": {
                    "units": {
                        "USD": [
                            _dur(s, e, "2025-01-15", v)
                            for (s, e), v in zip(quarters, rev)
                        ]
                    }
                },
            }
        }
    }


@pytest.fixture(autouse=True)
def _no_disk_cache_writes(monkeypatch):
    """Belt-and-braces: even if a test forgets to patch it, `get`/`put` must
    never touch the real `data/cache/edgar/` directory."""
    monkeypatch.setattr(supply_routes.edgar_cache, "get", lambda cik, **kw: None)
    monkeypatch.setattr(supply_routes.edgar_cache, "put", lambda cik, payload, **kw: None)


def test_get_margin_history_returns_series_with_peak_and_headroom(client, monkeypatch):
    monkeypatch.setattr(supply_routes, "ticker_to_cik", lambda: {"WDC": 106040})
    monkeypatch.setattr(
        supply_routes, "company_facts", lambda cik: _facts_for_margin_history()
    )

    r = client.get("/supply/margin-history/WDC")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "WDC"
    assert body["cik"] == 106040
    assert body["quarters"] == 4
    assert len(body["points"]) == 4
    # peak margin is 0.45 (Q3), current (last) is 0.20 -> headroom 25pp.
    assert body["historical_peak_gross_margin"] == pytest.approx(0.45)
    assert body["current_gross_margin"] == pytest.approx(0.20)
    assert body["headroom_pp"] == pytest.approx(25.0)


def test_get_margin_history_unknown_ticker_returns_404(client, monkeypatch):
    monkeypatch.setattr(supply_routes, "ticker_to_cik", lambda: {"WDC": 106040})
    r = client.get("/supply/margin-history/NOPE")
    assert r.status_code == 404
