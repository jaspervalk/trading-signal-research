"""Extended fundamentals fetcher — financials, cashflow, balance sheet.

The Fundamental analyst lens needs more than yfinance.info: it needs 3-year
revenue / margin / FCF trajectories, capital allocation, and balance sheet
ratios. These come from `yf.Ticker(t).financials / .cashflow / .balance_sheet`,
all annual columns (most recent first).

Everything is best-effort. yfinance ships inconsistent labels across tickers
and frequently NaN-pads the oldest year. Missing fields stay None; the agent
prompt acknowledges what's available.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import pandas as pd

from app.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RevenueTrajectory:
    """Annual revenue history, most recent fiscal year LAST in the tuple.

    `(FY-2, FY-1, FY-0)` so chronological reading is left-to-right.
    """

    fy_minus_2: float | None
    fy_minus_1: float | None
    fy_minus_0: float | None

    @property
    def yoy_recent(self) -> float | None:
        if self.fy_minus_0 is None or self.fy_minus_1 in (None, 0):
            return None
        return (self.fy_minus_0 - self.fy_minus_1) / self.fy_minus_1

    @property
    def yoy_prior(self) -> float | None:
        if self.fy_minus_1 is None or self.fy_minus_2 in (None, 0):
            return None
        return (self.fy_minus_1 - self.fy_minus_2) / self.fy_minus_2

    @property
    def quality(self) -> str:
        """Plain-language read: 'growing (accelerating)' / 'declining (decelerating)' / etc."""
        r = self.yoy_recent
        p = self.yoy_prior
        if r is None:
            return "unknown"
        if r > 0.05:
            direction = "growing"
        elif r > -0.02:
            direction = "stable"
        else:
            direction = "declining"
        if p is None:
            return direction
        delta = r - p
        if abs(delta) < 0.02:
            return f"{direction} (steady)"
        return f"{direction} ({'accelerating' if delta > 0 else 'decelerating'})"


@dataclass(frozen=True)
class FundamentalsExtended:
    """3-year trajectory + balance sheet + capital allocation read.

    Sits next to `ValuationPanel` in the Fundamental agent's context. Every
    field nullable — yfinance regularly drops or relabels rows on niche tickers.
    """

    fiscal_year_ends: list[str] = field(default_factory=list)  # ISO date strings, oldest → newest

    revenue: RevenueTrajectory | None = None
    operating_margin_fy_minus_2: float | None = None
    operating_margin_fy_minus_1: float | None = None
    operating_margin_fy_minus_0: float | None = None
    net_income_fy_minus_2: float | None = None
    net_income_fy_minus_1: float | None = None
    net_income_fy_minus_0: float | None = None

    fcf_ttm: float | None = None  # Most recent fiscal year (from yfinance.cashflow annual cols), not TTM
    fcf_yield: float | None = None
    capex_ttm: float | None = None  # Most recent fiscal year, not TTM; name is historical
    capex_pct_revenue: float | None = None

    current_ratio: float | None = None
    debt_to_equity: float | None = None
    total_cash: float | None = None
    total_debt: float | None = None
    cash_to_market_cap: float | None = None

    shares_outstanding_now: float | None = None
    shares_outstanding_yoy_pct: float | None = None  # positive = dilution
    buyback_yield_ttm: float | None = None  # Most recent fiscal year (not TTM); positive = buying back; share of market cap

    fetch_error: str | None = None
    sources_used: list[str] = field(default_factory=list)

    @property
    def operating_margin_trajectory(self) -> str:
        ms = [
            self.operating_margin_fy_minus_2,
            self.operating_margin_fy_minus_1,
            self.operating_margin_fy_minus_0,
        ]
        present = [m for m in ms if m is not None]
        if len(present) < 2:
            return "unknown"
        first, last = present[0], present[-1]
        if last > first + 0.01:
            return "expanding"
        if last < first - 0.01:
            return "compressing"
        return "stable"

    @property
    def balance_sheet_strength(self) -> str:
        """Coarse strength read: strong / adequate / stressed / unknown."""
        cr = self.current_ratio
        de = self.debt_to_equity
        if cr is None and de is None:
            return "unknown"
        score = 0
        n = 0
        if cr is not None:
            n += 1
            score += 1 if cr > 1.5 else (0 if cr > 1.0 else -1)
        if de is not None:
            n += 1
            score += 1 if de < 0.5 else (0 if de < 1.5 else -1)
        avg = score / n
        if avg > 0.5:
            return "strong"
        if avg < -0.5:
            return "stressed"
        return "adequate"

    @property
    def capital_allocation(self) -> str:
        """Returning cash, reinvesting, or diluting?"""
        by = self.buyback_yield_ttm
        dilu = self.shares_outstanding_yoy_pct
        if by is not None and by > 0.01:
            return "returning cash (buybacks)"
        if dilu is not None and dilu > 0.03:
            return "diluting"
        if self.capex_pct_revenue is not None and self.capex_pct_revenue > 0.15:
            return "reinvesting (heavy capex)"
        return "balanced"


_EMPTY = FundamentalsExtended()


@lru_cache(maxsize=128)
def fetch_fundamentals_extended(
    ticker: str, market_cap: float | None = None
) -> FundamentalsExtended:
    """Fetch + parse annual financials/cashflow/balance_sheet for `ticker`.

    `market_cap` is used to compute FCF yield + cash-to-market-cap ratios.
    Pass None if unknown — those fields stay None.
    """
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        fin = _to_frame(t.financials)
        cf = _to_frame(t.cashflow)
        bs = _to_frame(t.balance_sheet)
    except Exception as e:
        log.warning("fundamentals.fetch.error", ticker=ticker, error=str(e))
        return FundamentalsExtended(fetch_error=str(e))

    sources: list[str] = []
    if not fin.empty:
        sources.append("financials")
    if not cf.empty:
        sources.append("cashflow")
    if not bs.empty:
        sources.append("balance_sheet")
    if not sources:
        return FundamentalsExtended(fetch_error="all yfinance frames empty")

    fy_ends = _fiscal_year_ends(fin, cf, bs)

    revenue_traj = _parse_revenue(fin)
    op_m2, op_m1, op_m0 = _parse_operating_margins(fin)
    ni2, ni1, ni0 = _parse_net_income(fin)

    fcf_ttm = _row_latest(cf, _FCF_LABELS)
    capex_ttm_raw = _row_latest(cf, _CAPEX_LABELS)
    capex_ttm = abs(capex_ttm_raw) if capex_ttm_raw is not None else None
    revenue_latest = revenue_traj.fy_minus_0 if revenue_traj else None
    capex_pct = (capex_ttm / revenue_latest) if (capex_ttm and revenue_latest) else None

    fcf_yield = (fcf_ttm / market_cap) if (fcf_ttm and market_cap) else None

    current_ratio, debt_to_equity, total_cash, total_debt = _parse_balance_sheet(bs)
    cash_to_mc = (total_cash / market_cap) if (total_cash and market_cap) else None

    shares_now, shares_yoy = _parse_shares_trend(bs)
    buyback_yield = _parse_buyback_yield(cf, market_cap)

    return FundamentalsExtended(
        fiscal_year_ends=fy_ends,
        revenue=revenue_traj,
        operating_margin_fy_minus_2=op_m2,
        operating_margin_fy_minus_1=op_m1,
        operating_margin_fy_minus_0=op_m0,
        net_income_fy_minus_2=ni2,
        net_income_fy_minus_1=ni1,
        net_income_fy_minus_0=ni0,
        fcf_ttm=fcf_ttm,
        fcf_yield=fcf_yield,
        capex_ttm=capex_ttm,
        capex_pct_revenue=capex_pct,
        current_ratio=current_ratio,
        debt_to_equity=debt_to_equity,
        total_cash=total_cash,
        total_debt=total_debt,
        cash_to_market_cap=cash_to_mc,
        shares_outstanding_now=shares_now,
        shares_outstanding_yoy_pct=shares_yoy,
        buyback_yield_ttm=buyback_yield,
        sources_used=sources,
    )


_REVENUE_LABELS = ("Total Revenue", "Operating Revenue", "Revenue")
_OPERATING_INCOME_LABELS = (
    "Operating Income",
    "Operating Income As Reported",
    "Total Operating Income As Reported",
)
_NET_INCOME_LABELS = (
    "Net Income",
    "Net Income Common Stockholders",
    "Net Income From Continuing Operation Net Minority Interest",
)
_FCF_LABELS = ("Free Cash Flow", "Free Cashflow")
_CAPEX_LABELS = ("Capital Expenditure", "Capital Expenditures")
_CURRENT_ASSETS_LABELS = ("Current Assets", "Total Current Assets")
_CURRENT_LIAB_LABELS = ("Current Liabilities", "Total Current Liabilities")
_TOTAL_DEBT_LABELS = ("Total Debt", "Net Debt")
_EQUITY_LABELS = (
    "Stockholders Equity",
    "Common Stock Equity",
    "Total Equity Gross Minority Interest",
)
_CASH_LABELS = (
    "Cash Cash Equivalents And Short Term Investments",
    "Cash And Cash Equivalents",
    "Cash Financial",
)
_SHARES_LABELS = ("Ordinary Shares Number", "Share Issued")
_BUYBACK_LABELS = ("Repurchase Of Capital Stock", "Net Common Stock Issuance")


def _to_frame(obj: Any) -> pd.DataFrame:
    """Coerce yfinance accessors to DataFrame, treating None / empty as ()."""
    if obj is None:
        return pd.DataFrame()
    if isinstance(obj, pd.DataFrame):
        return obj
    return pd.DataFrame()


def _row_latest(df: pd.DataFrame, labels: tuple[str, ...]) -> float | None:
    """First non-NaN value in the first matching row (yfinance: cols are date-desc)."""
    if df.empty:
        return None
    for label in labels:
        if label in df.index:
            row = df.loc[label]
            for v in row.values:
                fv = _safe_float(v)
                if fv is not None:
                    return fv
    return None


def _row_three_years(
    df: pd.DataFrame, labels: tuple[str, ...]
) -> tuple[float | None, float | None, float | None]:
    """Return (FY-2, FY-1, FY-0) — oldest, prior, most-recent. yfinance cols
    are date-DESC, so this reverses the natural read order.
    """
    if df.empty:
        return (None, None, None)
    for label in labels:
        if label in df.index:
            row = df.loc[label]
            vals = [_safe_float(v) for v in row.values]
            recent = vals[0] if len(vals) > 0 else None
            prior = vals[1] if len(vals) > 1 else None
            two_prior = vals[2] if len(vals) > 2 else None
            return (two_prior, prior, recent)
    return (None, None, None)


def _parse_revenue(fin: pd.DataFrame) -> RevenueTrajectory | None:
    two, one, zero = _row_three_years(fin, _REVENUE_LABELS)
    if zero is None and one is None and two is None:
        return None
    return RevenueTrajectory(fy_minus_2=two, fy_minus_1=one, fy_minus_0=zero)


def _parse_operating_margins(
    fin: pd.DataFrame,
) -> tuple[float | None, float | None, float | None]:
    rev2, rev1, rev0 = _row_three_years(fin, _REVENUE_LABELS)
    op2, op1, op0 = _row_three_years(fin, _OPERATING_INCOME_LABELS)
    return (
        _safe_div(op2, rev2),
        _safe_div(op1, rev1),
        _safe_div(op0, rev0),
    )


def _parse_net_income(
    fin: pd.DataFrame,
) -> tuple[float | None, float | None, float | None]:
    return _row_three_years(fin, _NET_INCOME_LABELS)


def _parse_balance_sheet(
    bs: pd.DataFrame,
) -> tuple[float | None, float | None, float | None, float | None]:
    if bs.empty:
        return (None, None, None, None)
    current_assets = _row_latest(bs, _CURRENT_ASSETS_LABELS)
    current_liab = _row_latest(bs, _CURRENT_LIAB_LABELS)
    debt = _row_latest(bs, _TOTAL_DEBT_LABELS)
    equity = _row_latest(bs, _EQUITY_LABELS)
    cash = _row_latest(bs, _CASH_LABELS)
    current_ratio = _safe_div(current_assets, current_liab)
    debt_to_equity = _safe_div(debt, equity)
    return (current_ratio, debt_to_equity, cash, debt)


def _parse_shares_trend(
    bs: pd.DataFrame,
) -> tuple[float | None, float | None]:
    """Return (most-recent shares, YoY change %). Positive YoY = dilution."""
    if bs.empty:
        return (None, None)
    for label in _SHARES_LABELS:
        if label in bs.index:
            row = bs.loc[label]
            vals = [_safe_float(v) for v in row.values]
            recent = vals[0] if len(vals) > 0 else None
            prior = vals[1] if len(vals) > 1 else None
            yoy_pct = _safe_div(recent - prior, prior) if (recent and prior) else None
            return (recent, yoy_pct)
    return (None, None)


def _parse_buyback_yield(cf: pd.DataFrame, market_cap: float | None) -> float | None:
    """Most-recent fiscal-year cash spent on buybacks / market cap. Positive = buying back."""
    if cf.empty or not market_cap:
        return None
    raw = _row_latest(cf, _BUYBACK_LABELS)
    if raw is None:
        return None
    # yfinance reports repurchases as negative (cash outflow). Flip to positive = buyback yield.
    spent = -raw if raw < 0 else 0.0
    return spent / market_cap if market_cap else None


def _fiscal_year_ends(*frames: pd.DataFrame) -> list[str]:
    for df in frames:
        if not df.empty and len(df.columns) > 0:
            cols = list(df.columns)
            # Oldest → newest for prompt readability.
            try:
                ordered = sorted(cols)
                return [str(pd.Timestamp(c).date()) for c in ordered if c is not None]
            except Exception:
                return [str(c) for c in cols]
    return []


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


__all__ = [
    "FundamentalsExtended",
    "RevenueTrajectory",
    "fetch_fundamentals_extended",
]
