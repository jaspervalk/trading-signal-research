"""PortfolioTrade ORM row: persistence and defaults."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import PortfolioTrade


def test_portfolio_trade_roundtrip(session):
    t = PortfolioTrade(
        ticker="NVDA",
        side="buy",
        quantity=10.0,
        price_per_share=145.20,
        currency="USD",
        fees=1.50,
        traded_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        eur_amount=1320.50,
        note="first entry",
    )
    session.add(t)
    session.commit()
    session.refresh(t)

    assert t.id is not None
    assert t.ticker == "NVDA"
    assert t.quantity == 10.0
    assert t.eur_amount == 1320.50
    assert t.created_at is not None


def test_portfolio_trade_optional_fields_default(session):
    t = PortfolioTrade(
        ticker="ASML",
        side="buy",
        quantity=2.5,
        price_per_share=880.0,
        traded_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    session.add(t)
    session.commit()
    session.refresh(t)

    assert t.currency == "USD"
    assert t.fees == 0.0
    assert t.eur_amount is None
    assert t.note is None
