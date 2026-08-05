"""Flatten the ticker panels into self-describing, citable measures.

Three unit conventions coexist in this codebase and none of them are marked
on the models: most rates are fractions (0.25 = 25%), `dividend_yield` is
*usually* pre-multiplied by yfinance (0.38 = 0.38%) but not reliably so, and
prices, market caps and share counts are absolutes. `rsi_14` is a 0-100
index. Anything serialising these to a consumer that cannot see the source —
an LLM, an export, another service — will misread them by 100x sooner or
later. `dividend_yield` is normalised (see `_normalize_dividend_yield`)
before it is tagged, so its "percent" unit is true by construction rather
than an assertion resting on an unreliable upstream convention.

A `PanelDigest` is a flat list of `Measure` rows. Each carries its dotted
field path (which doubles as a citation key), its unit, and the age of the
data it came from. Fields that are None are listed in `missing` rather than
dropped, so absence is visible rather than inferred.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Literal

from pydantic import BaseModel

from app.analysis.schema import TickerResearchView

Unit = Literal[
    "ratio",         # a pure multiple, e.g. forward P/E
    "fraction",      # 0.25 == 25%
    "percent",       # 0.38 == 0.38% (already multiplied upstream)
    "usd",           # absolute currency
    "shares",        # absolute share count
    "days",          # whole days, may be negative
    "index_0_100",   # bounded oscillator, e.g. RSI
    "price",         # a price level in the instrument's currency
    "categorical",   # a string label
]

# (dotted path, unit). Order here is the order in the digest.
_VALUATION_FIELDS: tuple[tuple[str, Unit], ...] = (
    ("market_cap", "usd"),
    ("forward_pe", "ratio"),
    ("trailing_pe", "ratio"),
    ("peg_ratio", "ratio"),
    ("price_to_sales_ttm", "ratio"),
    ("price_to_book", "ratio"),
    ("enterprise_to_ebitda", "ratio"),
    ("earnings_growth_forward", "fraction"),
    ("revenue_growth_yoy", "fraction"),
    ("profit_margins", "fraction"),
    ("float_shares", "shares"),
    ("shares_outstanding", "shares"),
    ("short_pct_of_float", "fraction"),
    ("held_pct_institutions", "fraction"),
    ("beta", "ratio"),
    # yfinance's dividendYield convention is unreliable: usually pre-multiplied
    # (0.38 == 0.38%), occasionally a bare fraction. `_normalize_dividend_yield`
    # (consulted via `_NORMALISERS` in `_collect`) coerces it to percent before
    # this tag is applied, so "percent" is true by construction rather than
    # merely asserted — see its docstring for the heuristic.
    ("dividend_yield", "percent"),
    ("days_to_next_earnings", "days"),
    ("sector", "categorical"),
    ("industry", "categorical"),
)

_MARKET_FIELDS: tuple[tuple[str, Unit], ...] = (
    ("last_close", "price"),
    ("return_5d", "fraction"),
    ("return_21d", "fraction"),
    ("return_63d", "fraction"),
    ("return_252d", "fraction"),
    ("excess_return_21d", "fraction"),
    ("pct_off_52w_high", "fraction"),
    ("pct_off_52w_low", "fraction"),
    ("dollar_volume", "usd"),
)

_INDICATOR_FIELDS: tuple[tuple[str, Unit], ...] = (
    ("rsi_14", "index_0_100"),
    ("atr_14", "price"),
    ("atr_14_pct", "fraction"),
    ("ma_alignment", "categorical"),
    ("sma_50_slope_21d_pct", "fraction"),
    ("sma_200_slope_63d_pct", "fraction"),
    ("dist_to_sma_50_pct", "fraction"),
    ("dist_to_sma_200_pct", "fraction"),
    ("realized_vol_21d_annualized", "fraction"),
    ("relative_strength_vs_spy_63d", "fraction"),
)

_LEVELS_FIELDS: tuple[tuple[str, Unit], ...] = (
    ("nearest_support", "price"),
    ("nearest_resistance", "price"),
    ("recent_high_63d", "price"),
    ("base_low", "price"),
    ("pullback_pct_from_recent_high", "fraction"),
    ("breakout_distance_pct", "fraction"),
)

# Materialised @property reads off FundamentalsExtended / PeerComparison.
# These are frozen dataclasses whose most useful fields are properties, so
# they vanish under dataclasses.asdict() and must be named explicitly.
_FUNDAMENTALS_PROPERTIES: tuple[tuple[str, Unit], ...] = (
    ("operating_margin_trajectory", "categorical"),
    ("balance_sheet_strength", "categorical"),
    ("capital_allocation", "categorical"),
)
_FUNDAMENTALS_FIELDS: tuple[tuple[str, Unit], ...] = (
    ("fcf_yield", "fraction"),
    ("debt_to_equity", "ratio"),
    ("current_ratio", "ratio"),
    ("cash_to_market_cap", "fraction"),
    ("shares_outstanding_yoy_pct", "fraction"),
)
_PEER_PROPERTIES: tuple[tuple[str, Unit], ...] = (
    ("forward_pe_relative", "categorical"),
    ("growth_relative", "categorical"),
    ("margin_relative", "categorical"),
)
_PEER_FIELDS: tuple[tuple[str, Unit], ...] = (
    ("median_forward_pe", "ratio"),
    ("median_peg", "ratio"),
    ("median_revenue_growth_yoy", "fraction"),
    ("median_profit_margins", "fraction"),
)


def _normalize_dividend_yield(value: float) -> float:
    """Coerce yfinance's inconsistent dividendYield to a true percent.

    yfinance returns this field pre-multiplied for most tickers (0.38 == 0.38%)
    but as a fraction for some. The web layer applies the same heuristic in
    ValuationPanel.tsx. Normalising here — rather than tagging and hoping —
    is what makes the "percent" unit true rather than merely asserted.

    A real yield below 0.05% is not plausible for a dividend payer, so a value
    under that threshold is read as a fraction and scaled.
    """
    return value * 100 if 0 < value < 0.05 else value


# Per-field normalisers, keyed by the dotted `path` (`prefix.name`), applied
# inside `_collect` before a Measure is built. Only fields whose upstream
# convention is unreliable belong here — see `_normalize_dividend_yield`.
_NORMALISERS: dict[str, Callable[[float], float]] = {
    "valuation.dividend_yield": _normalize_dividend_yield,
}


class Measure(BaseModel):
    """One panel field, self-describing.

    `field` is the dotted path and doubles as a citation key: a consumer
    quoting a number is expected to name the field it came from, and that
    name can be checked against the digest.
    """

    field: str
    value: float | int | str | None
    unit: Unit
    as_of: datetime


def render_measure(m: Measure) -> str:
    """One canonical rendering for a measure line, shared by every prompt
    surface so the same number never appears in two formats.

    Floats render via `.4g` (so e.g. 0.38 stays "0.38", 1234.5 stays "1.23e+03"
    -> avoided below since valuation numbers are rarely that large, but the
    guard matters for whole numbers: `.4g` strips trailing zeros (30.0 ->
    "30"), which reads as an int and loses the "this is a measured float"
    signal, so whole-valued floats are re-rendered with one decimal place.
    """
    if isinstance(m.value, float):
        rendered = f"{m.value:,.4g}"
        if "." not in rendered and "e" not in rendered and "E" not in rendered:
            rendered = f"{m.value:,.1f}"
    else:
        rendered = str(m.value)
    name = m.field.split(".", 1)[1] if "." in m.field else m.field
    return f"- {name}: {rendered} ({m.unit})"


class PanelDigest(BaseModel):
    """Every panel number for one ticker, flat and unit-tagged."""

    ticker: str
    as_of: datetime
    measures: list[Measure] = []
    missing: list[str] = []

    def by_field(self) -> dict[str, Measure]:
        return {m.field: m for m in self.measures}

    def has(self, field: str) -> bool:
        return any(m.field == field for m in self.measures)

    def valuation_block(self) -> str:
        """Render the valuation measures as prompt-ready text.

        Only present fields are rendered; each line states the unit so the
        reader cannot mistake a fraction for a percent. Absent fields are
        not named here — they are already visible structurally via
        `PanelDigest.missing` — so a reader skimming the block cannot
        mistake "field was omitted from this rendering" for "field is
        unavailable" when a caller filters `missing` down before render.
        """
        rows = [m for m in self.measures if m.field.startswith("valuation.")]
        if not rows:
            return "(no valuation data available for this ticker)"

        lines: list[str] = [render_measure(m) for m in rows]

        stamp = max(m.as_of for m in rows)
        lines.append(f"(valuation as of {stamp.isoformat()})")
        return "\n".join(lines)


def _collect(
    source: object | None,
    prefix: str,
    spec: tuple[tuple[str, Unit], ...],
    as_of: datetime,
    measures: list[Measure],
    missing: list[str],
) -> None:
    for name, unit in spec:
        path = f"{prefix}.{name}"
        value = getattr(source, name, None) if source is not None else None
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(path)
            continue
        normalize = _NORMALISERS.get(path)
        if normalize is not None and isinstance(value, float):
            value = normalize(value)
        measures.append(Measure(field=path, value=value, unit=unit, as_of=as_of))


def build_panel_digest(
    view: TickerResearchView,
    *,
    fundamentals: object | None = None,
    peers: object | None = None,
) -> PanelDigest:
    """Flatten `view` (plus optional Deep-mode extras) into a `PanelDigest`.

    `fundamentals` and `peers` are `FundamentalsExtended` / `PeerComparison`
    when available — typed loosely to avoid importing market modules into the
    analysis layer.
    """
    measures: list[Measure] = []
    missing: list[str] = []
    as_of = view.as_of

    # Valuation measures carry the metadata's own age when it is known.
    valuation_as_of = getattr(view.valuation, "fetched_at", None) or as_of

    _collect(view.valuation, "valuation", _VALUATION_FIELDS, valuation_as_of, measures, missing)
    _collect(view.market, "market", _MARKET_FIELDS, as_of, measures, missing)
    _collect(view.indicators, "indicators", _INDICATOR_FIELDS, as_of, measures, missing)
    _collect(view.levels, "levels", _LEVELS_FIELDS, as_of, measures, missing)
    _collect(fundamentals, "fundamentals", _FUNDAMENTALS_FIELDS, as_of, measures, missing)
    _collect(fundamentals, "fundamentals", _FUNDAMENTALS_PROPERTIES, as_of, measures, missing)
    _collect(peers, "peers", _PEER_FIELDS, as_of, measures, missing)
    _collect(peers, "peers", _PEER_PROPERTIES, as_of, measures, missing)

    return PanelDigest(
        ticker=view.ticker,
        as_of=as_of,
        measures=measures,
        missing=missing,
    )


__all__ = ["Measure", "PanelDigest", "Unit", "build_panel_digest", "render_measure"]
