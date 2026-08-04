"""Tests for `app.market.peer_comparison`."""

from __future__ import annotations

from unittest.mock import patch

from app.market.peer_comparison import (
    PeerComparison,
    _resolve_peers,
    fetch_peer_comparison,
)


def test_resolve_peers_industry_match_excludes_self():
    peers = _resolve_peers("NVDA", "Technology", "Semiconductors")
    assert peers
    assert "NVDA" not in peers
    assert "AMD" in peers


def test_resolve_peers_sector_fallback():
    peers = _resolve_peers("MSFT", "Technology", "Unicorn Software For Llamas")
    # No industry match → falls back to sector list.
    assert peers
    assert "MSFT" not in peers
    # Sector fallback uses the Technology default set.
    assert any(p in {"AAPL", "GOOGL", "META", "AMZN", "NVDA"} for p in peers)


def test_resolve_peers_crypto_miner_override():
    # IREN/MARA classified by yfinance as Financial Services|Capital Markets,
    # but bitcoin miners are not bank peers.
    peers = _resolve_peers("IREN", "Financial Services", "Capital Markets")
    assert "IREN" not in peers
    assert {"MARA", "RIOT", "CLSK"}.issubset(set(peers))
    assert "GS" not in peers


def test_resolve_peers_empty_on_unknown_sector():
    assert _resolve_peers("UNKNOWN", None, None) == []
    # Truly unknown sector → empty.
    assert _resolve_peers("UNKNOWN", "Bingo", "Whatever") == []


def test_forward_pe_relative_classifications():
    p = PeerComparison(
        peer_set_available=True,
        target_forward_pe=10.0,
        median_forward_pe=30.0,
    )
    assert "cheap" in p.forward_pe_relative

    q = PeerComparison(
        peer_set_available=True,
        target_forward_pe=50.0,
        median_forward_pe=20.0,
    )
    assert "rich" in q.forward_pe_relative

    r = PeerComparison(
        peer_set_available=True,
        target_forward_pe=18.0,
        median_forward_pe=20.0,
    )
    assert "inline" in r.forward_pe_relative


def test_growth_relative_classifications():
    p = PeerComparison(target_revenue_growth_yoy=0.30, median_revenue_growth_yoy=0.10)
    assert "out-growing" in p.growth_relative

    q = PeerComparison(target_revenue_growth_yoy=0.02, median_revenue_growth_yoy=0.15)
    assert "under-growing" in q.growth_relative


def test_fetch_peer_comparison_unavailable_set_records_note():
    pc = fetch_peer_comparison(
        "WEIRDO",
        sector="Bingo",
        industry="Whatever",
        target_metadata={"forwardPE": 25, "revenueGrowth": 0.4, "profitMargins": 0.3},
    )
    assert pc.peer_set_available is False
    assert pc.note is not None
    assert pc.target_forward_pe == 25
    # Target survives even without peers.


def test_fetch_peer_comparison_with_fake_peer_metadata():
    """Patch _peer_metadata_pack so we don't hit yfinance for the peer set."""
    fake = {
        "AMD": [
            ("forward_pe", 22.0),
            ("trailing_pe", 30.0),
            ("peg", 1.2),
            ("price_to_sales_ttm", 7.5),
            ("revenue_growth_yoy", 0.10),
            ("profit_margins", 0.18),
            ("operating_margins", 0.20),
            ("market_cap", 250_000_000_000),
        ],
        "AVGO": [
            ("forward_pe", 28.0),
            ("trailing_pe", 40.0),
            ("peg", 1.6),
            ("price_to_sales_ttm", 12.0),
            ("revenue_growth_yoy", 0.08),
            ("profit_margins", 0.25),
            ("operating_margins", 0.30),
            ("market_cap", 800_000_000_000),
        ],
        "MU": [
            ("forward_pe", 15.0),
            ("trailing_pe", 18.0),
            ("peg", 0.9),
            ("price_to_sales_ttm", 4.0),
            ("revenue_growth_yoy", 0.20),
            ("profit_margins", 0.12),
            ("operating_margins", 0.15),
            ("market_cap", 100_000_000_000),
        ],
        "INTC": [
            ("forward_pe", 20.0),
            ("trailing_pe", 30.0),
            ("peg", 2.0),
            ("price_to_sales_ttm", 2.5),
            ("revenue_growth_yoy", -0.05),
            ("profit_margins", -0.05),
            ("operating_margins", -0.02),
            ("market_cap", 130_000_000_000),
        ],
        "QCOM": [
            ("forward_pe", 14.0),
            ("trailing_pe", 18.0),
            ("peg", 1.1),
            ("price_to_sales_ttm", 4.5),
            ("revenue_growth_yoy", 0.06),
            ("profit_margins", 0.22),
            ("operating_margins", 0.26),
            ("market_cap", 180_000_000_000),
        ],
    }

    with patch(
        "app.market.peer_comparison._peer_metadata_pack",
        side_effect=lambda peer: tuple(fake.get(peer, [])),
    ):
        pc = fetch_peer_comparison(
            "NVDA",
            sector="Technology",
            industry="Semiconductors",
            target_metadata={
                "forwardPE": 17.0,
                "pegRatio": 0.7,
                "priceToSalesTrailing12Months": 22.0,
                "revenueGrowth": 0.65,
                "profitMargins": 0.55,
            },
        )

    assert pc.peer_set_available
    assert set(pc.peer_tickers) == {"AMD", "AVGO", "MU", "INTC", "QCOM"}
    # Medians computed correctly across the 5-peer pack.
    assert pc.median_forward_pe == 20.0  # median of [22, 28, 15, 20, 14]
    # Target growth 65% vs peer median 8% → out-growing.
    assert "out-growing" in pc.growth_relative
    # Target margin 55% vs peer median 18% → premium margins.
    assert "premium" in pc.margin_relative
    # NVDA forward P/E 17 is below the median 20 → cheap-ish vs sector.
    assert "cheap" in pc.forward_pe_relative or "inline" in pc.forward_pe_relative
