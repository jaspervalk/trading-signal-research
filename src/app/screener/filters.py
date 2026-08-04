"""Pluggable filter classes.

Each filter is independent, configurable, and tolerant of `None` inputs.
A filter that lacks the data it needs returns `passed=False` with an
"insufficient_data" reason — that's strictly more conservative than
admitting tickers we can't evaluate, but it's never used to *rank*
tickers (only the union of passes does that).

Filter contract:

    class SomeFilter:
        name: str
        def evaluate(self, m: TickerMetrics) -> FilterResult: ...

The "cheap for a reason" / earnings-proximity flags are *not* filters;
they live on `ScreenRow.flags` and the pipeline populates them.
"""

from __future__ import annotations

from typing import Protocol

from app.screener.schema import FilterResult, ScreenConfig, TickerMetrics


class Filter(Protocol):
    name: str

    def evaluate(self, m: TickerMetrics) -> FilterResult:  # pragma: no cover
        ...


# ---------------------------------------------------------------------------
# 1. Valuation


class ValuationFilter:
    name = "value"

    def __init__(self, config: ScreenConfig):
        self.cfg = config

    def evaluate(self, m: TickerMetrics) -> FilterResult:
        reasons: list[str] = []
        checks: list[tuple[str, bool]] = []
        if m.forward_pe is not None:
            ok = m.forward_pe < self.cfg.max_forward_pe
            checks.append((f"fwd_pe={m.forward_pe:.1f}<{self.cfg.max_forward_pe}", ok))
        if m.peg_ratio is not None and m.peg_ratio > 0:
            ok = m.peg_ratio < self.cfg.max_peg
            checks.append((f"peg={m.peg_ratio:.2f}<{self.cfg.max_peg}", ok))
        if m.ev_to_ebitda is not None and m.ev_to_ebitda > 0:
            ok = m.ev_to_ebitda < self.cfg.max_ev_ebitda
            checks.append((f"ev/ebitda={m.ev_to_ebitda:.1f}<{self.cfg.max_ev_ebitda}", ok))

        if not checks:
            return FilterResult(name=self.name, passed=False, reasons=["insufficient_data"])

        # "Pass if ANY of forward P/E / PEG / EV/EBITDA threshold."
        passed = any(ok for _, ok in checks)
        reasons = [tag for tag, ok in checks if ok] if passed else [
            tag for tag, _ in checks
        ]
        return FilterResult(name=self.name, passed=passed, reasons=reasons)


# ---------------------------------------------------------------------------
# 2. Growth


class GrowthFilter:
    name = "growth"

    def __init__(self, config: ScreenConfig):
        self.cfg = config

    def evaluate(self, m: TickerMetrics) -> FilterResult:
        if m.revenue_growth_yoy is None:
            return FilterResult(
                name=self.name, passed=False, reasons=["insufficient_data"]
            )
        rev_ok = m.revenue_growth_yoy > self.cfg.min_revenue_growth

        # Earnings: positive OR inflecting (qoq > yoy => acceleration).
        if self.cfg.require_earnings_positive:
            if m.earnings_growth_yoy is None:
                earn_ok = False
                earn_tag = "earnings_unknown"
            else:
                positive = m.earnings_growth_yoy > 0
                inflecting = (
                    m.revenue_growth_qoq is not None
                    and m.revenue_growth_qoq > m.revenue_growth_yoy
                )
                earn_ok = positive or inflecting
                earn_tag = (
                    f"earn_yoy={m.earnings_growth_yoy:.2f}"
                    + (" inflecting" if inflecting else "")
                )
        else:
            earn_ok = True
            earn_tag = "earnings_check_skipped"

        passed = rev_ok and earn_ok
        reasons = [
            f"rev_yoy={m.revenue_growth_yoy:.2f}>{self.cfg.min_revenue_growth}",
            earn_tag,
        ]
        return FilterResult(name=self.name, passed=passed, reasons=reasons)


# ---------------------------------------------------------------------------
# 3. Quality / moat proxies


class QualityFilter:
    name = "quality"

    def __init__(self, config: ScreenConfig):
        self.cfg = config

    def evaluate(self, m: TickerMetrics) -> FilterResult:
        if m.gross_margin is None or m.roe is None or m.debt_to_equity is None:
            return FilterResult(
                name=self.name, passed=False, reasons=["insufficient_data"]
            )
        gm_ok = m.gross_margin > self.cfg.min_gross_margin
        roe_ok = m.roe > self.cfg.min_roe
        de_ok = m.debt_to_equity < self.cfg.max_debt_to_equity
        passed = gm_ok and roe_ok and de_ok
        reasons = [
            f"gm={m.gross_margin:.2f}>{self.cfg.min_gross_margin}",
            f"roe={m.roe:.2f}>{self.cfg.min_roe}",
            f"d/e={m.debt_to_equity:.2f}<{self.cfg.max_debt_to_equity}",
        ]
        return FilterResult(name=self.name, passed=passed, reasons=reasons)


# ---------------------------------------------------------------------------
# 4. Technical momentum


class TechnicalFilter:
    name = "technical"

    def __init__(self, config: ScreenConfig):
        self.cfg = config

    def evaluate(self, m: TickerMetrics) -> FilterResult:
        if m.pct_vs_200d is None or m.rs_vs_spy_3mo is None:
            return FilterResult(
                name=self.name, passed=False, reasons=["insufficient_data"]
            )
        above_200d = m.pct_vs_200d > 0
        rs_ok = m.rs_vs_spy_3mo > self.cfg.min_rs_vs_spy_3mo
        not_extended = m.pct_vs_200d <= self.cfg.max_extension_above_200d
        passed = above_200d and rs_ok and not_extended
        reasons = [
            f"vs_200d={m.pct_vs_200d:+.2f}",
            f"rs_3mo={m.rs_vs_spy_3mo:+.2f}",
            ("not_extended" if not_extended else "extended"),
        ]
        return FilterResult(name=self.name, passed=passed, reasons=reasons)


# ---------------------------------------------------------------------------
# Registry + flag helpers


ALL_FILTERS: dict[str, type] = {
    ValuationFilter.name: ValuationFilter,
    GrowthFilter.name: GrowthFilter,
    QualityFilter.name: QualityFilter,
    TechnicalFilter.name: TechnicalFilter,
}


def build_filters(config: ScreenConfig) -> list[Filter]:
    """Instantiate the filters named in `config.enabled_filters` (or all)."""
    names = config.enabled_filters or list(ALL_FILTERS.keys())
    return [ALL_FILTERS[n](config) for n in names if n in ALL_FILTERS]


def compute_flags(m: TickerMetrics, config: ScreenConfig) -> list[str]:
    """Informational tags that sit next to filters but never gate the screen."""
    flags: list[str] = []
    # "cheap for a reason": valuation looks low but earnings growth is negative
    is_cheap = (
        (m.forward_pe is not None and m.forward_pe < config.max_forward_pe)
        or (m.peg_ratio is not None and 0 < m.peg_ratio < config.max_peg)
        or (m.ev_to_ebitda is not None and 0 < m.ev_to_ebitda < config.max_ev_ebitda)
    )
    if is_cheap and m.earnings_growth_yoy is not None and m.earnings_growth_yoy < 0:
        flags.append("cheap_for_a_reason")
    # Catalyst proximity — informational, never filters
    if (
        m.days_to_earnings is not None
        and 0 <= m.days_to_earnings <= config.earnings_proximity_days
    ):
        flags.append(f"earnings_in_{m.days_to_earnings}d")
    if m.pct_vs_200d is not None and m.pct_vs_200d > config.max_extension_above_200d:
        flags.append("extended_above_200d")
    return flags


__all__ = [
    "ALL_FILTERS",
    "Filter",
    "GrowthFilter",
    "QualityFilter",
    "TechnicalFilter",
    "ValuationFilter",
    "build_filters",
    "compute_flags",
]
