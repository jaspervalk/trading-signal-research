"""Portfolio API: CRUD round-trip and validation surfacing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.portfolio import pricing
from app.portfolio.policy import Policy, Trigger

BASE = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _use_policy(monkeypatch, policy: Policy) -> None:
    """Route around the yaml-backed, lru_cache'd `load_policy` for a test."""
    import apps.api.app.routes.portfolio as portfolio_routes

    monkeypatch.setattr(portfolio_routes, "load_policy", lambda: policy)


@pytest.fixture
def client(engine, session):
    """Use FastAPI's dependency_overrides to inject the test session."""
    from fastapi.testclient import TestClient

    from apps.api.app.deps import db_session
    from apps.api.app.main import app

    def _override():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[db_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(pricing, "_fetch_one", lambda t: pricing.Quote(
        ticker=t, last_price=150.0, previous_close=140.0,
        currency="USD", as_of=pricing._now(),
    ))
    pricing.clear_cache()
    yield
    pricing.clear_cache()


def _payload(**kw):
    body = {
        "ticker": "NVDA",
        "side": "buy",
        "quantity": 10,
        "price_per_share": 100.0,
        "traded_at": BASE.isoformat(),
    }
    body.update(kw)
    return body


def test_create_and_list_trade(client):
    r = client.post("/portfolio/trades", json=_payload())
    assert r.status_code == 201
    assert r.json()["ticker"] == "NVDA"

    r = client.get("/portfolio/trades")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_get_portfolio_returns_priced_position(client):
    client.post("/portfolio/trades", json=_payload())
    r = client.get("/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["open_positions"][0]["ticker"] == "NVDA"
    assert body["open_positions"][0]["market_value"] == 1500.0


def test_oversell_returns_422(client):
    client.post("/portfolio/trades", json=_payload(quantity=5))
    r = client.post(
        "/portfolio/trades",
        json=_payload(side="sell", quantity=9, traded_at=(BASE + timedelta(days=1)).isoformat()),
    )
    assert r.status_code == 422
    assert "oversell" in r.json()["detail"]


def test_future_date_returns_422(client):
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    r = client.post("/portfolio/trades", json=_payload(traded_at=future))
    assert r.status_code == 422
    assert "future" in r.json()["detail"]


def test_negative_quantity_returns_422(client):
    r = client.post("/portfolio/trades", json=_payload(quantity=-5))
    assert r.status_code == 422


def test_malformed_currency_returns_422(client):
    r = client.post("/portfolio/trades", json=_payload(currency="12A"))
    assert r.status_code == 422


def test_patch_trade(client):
    trade_id = client.post("/portfolio/trades", json=_payload()).json()["id"]
    r = client.patch(f"/portfolio/trades/{trade_id}", json={"quantity": 20})
    assert r.status_code == 200
    assert r.json()["quantity"] == 20


def test_patch_missing_trade_returns_404(client):
    assert client.patch("/portfolio/trades/999", json={"quantity": 1}).status_code == 404


def test_delete_trade(client):
    trade_id = client.post("/portfolio/trades", json=_payload()).json()["id"]
    assert client.delete(f"/portfolio/trades/{trade_id}").status_code == 204
    assert client.get("/portfolio/trades").json() == []


def test_delete_missing_trade_returns_404(client):
    assert client.delete("/portfolio/trades/999").status_code == 404


def test_delete_buy_that_a_later_sell_depends_on_returns_422(client):
    buy_id = client.post("/portfolio/trades", json=_payload(quantity=10)).json()["id"]
    client.post(
        "/portfolio/trades",
        json=_payload(side="sell", quantity=5, traded_at=(BASE + timedelta(days=1)).isoformat()),
    )

    r = client.delete(f"/portfolio/trades/{buy_id}")
    assert r.status_code == 422
    assert "cannot delete" in r.json()["detail"]

    # The refused delete must not have removed the trade.
    assert len(client.get("/portfolio/trades").json()) == 2


# --- GET /portfolio/policy --------------------------------------------------


def test_policy_available_with_priced_positions(client, monkeypatch):
    _use_policy(
        monkeypatch,
        Policy(
            base_currency="EUR",
            target_weights={"NVDA": 0.5},
            factors={"GROWTH": ["NVDA"]},
            ai_factors=["GROWTH"],
            ai_target_max=0.6,
        ),
    )
    client.post("/portfolio/trades", json=_payload())

    r = client.get("/portfolio/policy")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["reason"] is None
    assert len(body["positions"]) == 1
    assert body["positions"][0]["ticker"] == "NVDA"


def test_policy_response_includes_ai_weight_buy_order_and_triggers(client, monkeypatch):
    _use_policy(
        monkeypatch,
        Policy(
            base_currency="EUR",
            target_weights={"NVDA": 0.5},
            factors={"GROWTH": ["NVDA"]},
            ai_factors=["GROWTH"],
            ai_target_max=0.6,
            triggers=[
                Trigger(
                    ticker="NVDA",
                    status="watch",
                    condition="data-center capex guidance cut",
                    next_report="2026-09-01",
                )
            ],
            monthly_trade_budget=3,
        ),
    )
    client.post("/portfolio/trades", json=_payload())

    r = client.get("/portfolio/policy")
    assert r.status_code == 200
    body = r.json()
    assert "ai_weight" in body
    assert body["buy_order"] == ["NVDA"]
    assert body["triggers"] == [
        {
            "ticker": "NVDA",
            "status": "watch",
            "condition": "data-center capex guidance cut",
            "next_report": "2026-09-01",
        }
    ]
    assert body["monthly_trade_budget"] == 3


def test_policy_position_over_target_reports_trim(client, monkeypatch):
    _use_policy(
        monkeypatch,
        Policy(
            base_currency="EUR",
            target_weights={"NVDA": 0.05},
            factors={"GROWTH": ["NVDA"]},
            ai_factors=["GROWTH"],
        ),
    )
    client.post("/portfolio/trades", json=_payload())

    r = client.get("/portfolio/policy")
    assert r.status_code == 200
    nvda = next(p for p in r.json()["positions"] if p["ticker"] == "NVDA")
    assert nvda["band_status"] == "trim"


def test_policy_unavailable_with_no_positions(client, monkeypatch):
    _use_policy(monkeypatch, Policy(base_currency="EUR"))

    r = client.get("/portfolio/policy")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["reason"]
