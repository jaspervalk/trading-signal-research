"""Tests for `app.market.fundamentals` — parsing + plain-language reads.

Tests use fabricated pandas DataFrames in the yfinance row-label shape so we
don't depend on a live network call.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from app.market.fundamentals import (
    FundamentalsExtended,
    RevenueTrajectory,
    fetch_fundamentals_extended,
)


def test_revenue_trajectory_growing_accelerating():
    r = RevenueTrajectory(fy_minus_2=100, fy_minus_1=110, fy_minus_0=140)
    assert r.yoy_prior == 0.10
    assert abs(r.yoy_recent - (30 / 110)) < 1e-9
    assert "growing" in r.quality
    assert "accelerating" in r.quality


def test_revenue_trajectory_declining_decelerating():
    r = RevenueTrajectory(fy_minus_2=200, fy_minus_1=180, fy_minus_0=140)
    assert "declining" in r.quality
    assert "decelerating" in r.quality  # yoy_recent < yoy_prior (both negative)


def test_revenue_trajectory_handles_missing():
    r = RevenueTrajectory(fy_minus_2=None, fy_minus_1=None, fy_minus_0=100)
    assert r.yoy_recent is None
    assert r.quality == "unknown"


def test_operating_margin_trajectory_read():
    f = FundamentalsExtended(
        operating_margin_fy_minus_2=0.20,
        operating_margin_fy_minus_1=0.25,
        operating_margin_fy_minus_0=0.30,
    )
    assert f.operating_margin_trajectory == "expanding"

    g = FundamentalsExtended(
        operating_margin_fy_minus_2=0.30,
        operating_margin_fy_minus_1=0.25,
        operating_margin_fy_minus_0=0.18,
    )
    assert g.operating_margin_trajectory == "compressing"

    h = FundamentalsExtended(
        operating_margin_fy_minus_2=0.20,
        operating_margin_fy_minus_1=0.205,
        operating_margin_fy_minus_0=0.198,
    )
    assert h.operating_margin_trajectory == "stable"


def test_balance_sheet_strength():
    strong = FundamentalsExtended(current_ratio=3.5, debt_to_equity=0.10)
    assert strong.balance_sheet_strength == "strong"

    stressed = FundamentalsExtended(current_ratio=0.6, debt_to_equity=3.0)
    assert stressed.balance_sheet_strength == "stressed"

    adequate = FundamentalsExtended(current_ratio=1.3, debt_to_equity=1.0)
    assert adequate.balance_sheet_strength == "adequate"

    unk = FundamentalsExtended()
    assert unk.balance_sheet_strength == "unknown"


def test_capital_allocation_classifications():
    buybacks = FundamentalsExtended(buyback_yield_ttm=0.025)
    assert buybacks.capital_allocation == "returning cash (buybacks)"

    diluting = FundamentalsExtended(shares_outstanding_yoy_pct=0.07)
    assert diluting.capital_allocation == "diluting"

    capex_heavy = FundamentalsExtended(capex_pct_revenue=0.22)
    assert capex_heavy.capital_allocation == "reinvesting (heavy capex)"

    balanced = FundamentalsExtended(buyback_yield_ttm=0.003)
    assert balanced.capital_allocation == "balanced"


def _fab_yfinance_frames():
    """Build fake yfinance DataFrames in the shape it actually returns.

    yfinance: columns are dates DESC (most recent first), rows are line items.
    """
    cols = [
        pd.Timestamp("2026-01-31"),
        pd.Timestamp("2025-01-31"),
        pd.Timestamp("2024-01-31"),
        pd.Timestamp("2023-01-31"),
    ]
    financials = pd.DataFrame(
        {
            cols[0]: [216_000_000_000, 130_000_000_000, 73_000_000_000],
            cols[1]: [130_000_000_000, 81_000_000_000, 32_000_000_000],
            cols[2]: [60_000_000_000, 32_000_000_000, 16_000_000_000],
            cols[3]: [26_000_000_000, 5_000_000_000, 4_000_000_000],
        },
        index=["Total Revenue", "Operating Income", "Net Income"],
    )
    cashflow = pd.DataFrame(
        {
            cols[0]: [96_000_000_000, -6_000_000_000, -40_000_000_000],
            cols[1]: [60_000_000_000, -3_000_000_000, -33_000_000_000],
            cols[2]: [27_000_000_000, -1_000_000_000, -9_000_000_000],
            cols[3]: [3_800_000_000, -1_800_000_000, -10_000_000_000],
        },
        index=["Free Cash Flow", "Capital Expenditure", "Repurchase Of Capital Stock"],
    )
    balance_sheet = pd.DataFrame(
        {
            cols[0]: [125_000_000_000, 32_000_000_000, 11_000_000_000, 62_000_000_000, 157_000_000_000, 24_304_000_000],
            cols[1]: [80_000_000_000, 18_000_000_000, 9_900_000_000, 43_000_000_000, 79_000_000_000, 24_477_000_000],
            cols[2]: [44_000_000_000, 10_000_000_000, 11_000_000_000, 26_000_000_000, 43_000_000_000, 24_640_000_000],
            cols[3]: [23_000_000_000, 6_500_000_000, 12_000_000_000, 13_000_000_000, 22_000_000_000, 24_661_000_000],
        },
        index=[
            "Current Assets",
            "Current Liabilities",
            "Total Debt",
            "Cash Cash Equivalents And Short Term Investments",
            "Stockholders Equity",
            "Ordinary Shares Number",
        ],
    )
    return financials, cashflow, balance_sheet


def test_fetch_fundamentals_extended_parses_yfinance_shape():
    fetch_fundamentals_extended.cache_clear()
    fin, cf, bs = _fab_yfinance_frames()

    class _FakeTicker:
        def __init__(self, t):
            self.financials = fin
            self.cashflow = cf
            self.balance_sheet = bs

    with patch("yfinance.Ticker", _FakeTicker):
        f = fetch_fundamentals_extended("FAKE", market_cap=4_000_000_000_000)

    assert f.fetch_error is None
    assert set(f.sources_used) == {"financials", "cashflow", "balance_sheet"}

    # Revenue 3y
    assert f.revenue is not None
    assert f.revenue.fy_minus_0 == 216_000_000_000
    assert f.revenue.fy_minus_1 == 130_000_000_000
    assert f.revenue.fy_minus_2 == 60_000_000_000
    assert "growing" in f.revenue.quality

    # Operating margins computed from operating income / total revenue.
    assert f.operating_margin_fy_minus_0 == 130_000_000_000 / 216_000_000_000

    # FCF + capex + ratios.
    assert f.fcf_ttm == 96_000_000_000
    assert f.fcf_yield == 96_000_000_000 / 4_000_000_000_000
    assert f.capex_ttm == 6_000_000_000  # abs of -6B
    assert f.capex_pct_revenue == 6_000_000_000 / 216_000_000_000

    # Balance sheet.
    assert f.current_ratio == 125_000_000_000 / 32_000_000_000
    assert f.debt_to_equity == 11_000_000_000 / 157_000_000_000
    assert f.total_cash == 62_000_000_000
    assert f.cash_to_market_cap == 62_000_000_000 / 4_000_000_000_000

    # Buyback yield: $40B / $4T market cap.
    assert f.buyback_yield_ttm == 40_000_000_000 / 4_000_000_000_000

    # Shares outstanding YoY change is negative (24,304M vs 24,477M).
    assert f.shares_outstanding_yoy_pct < 0


def test_fetch_fundamentals_extended_returns_error_on_yfinance_failure():
    fetch_fundamentals_extended.cache_clear()

    def _boom(*a, **kw):
        raise RuntimeError("network down")

    with patch("yfinance.Ticker", side_effect=_boom):
        f = fetch_fundamentals_extended("BOOM", market_cap=1_000_000_000)
    assert f.fetch_error is not None
    assert "network" in f.fetch_error.lower()
    assert f.revenue is None
