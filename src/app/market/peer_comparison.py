"""Sector / industry peer comparison.

The Fundamental lens improves dramatically when a multiple is contextualised
("forward P/E 35 vs semiconductor median 22") rather than emitted naked. This
module produces a `PeerComparison` containing sector/industry-median ratios
computed from a small curated peer set per industry.

We do NOT use yfinance's `recommendations` or peer endpoints — they're either
deprecated or rate-limited. Instead we maintain a hand-picked map from
`(sector, industry)` → up to ~6 peers, then pull each peer's `info` and median
the headline ratios.

Cached aggressively: `(ticker, sector, industry)` → result, lru-cached. If the
industry has no curated mapping, the comparison returns an empty result with
`peer_set_available=False` and a clear note for the prompt.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class PeerComparison:
    peer_tickers: list[str] = field(default_factory=list)
    peer_set_available: bool = False
    note: str | None = None

    median_forward_pe: float | None = None
    median_trailing_pe: float | None = None
    median_peg: float | None = None
    median_price_to_sales: float | None = None
    median_revenue_growth_yoy: float | None = None
    median_profit_margins: float | None = None
    median_operating_margins: float | None = None
    median_market_cap: float | None = None

    target_forward_pe: float | None = None
    target_peg: float | None = None
    target_revenue_growth_yoy: float | None = None
    target_profit_margins: float | None = None

    @property
    def forward_pe_relative(self) -> str:
        """Plain-language read: 'rich vs sector', 'cheap vs sector', or 'inline'."""
        t = self.target_forward_pe
        m = self.median_forward_pe
        if t is None or m is None or m == 0:
            return "—"
        ratio = t / m
        if ratio > 1.30:
            return f"rich (+{(ratio - 1) * 100:.0f}% vs sector)"
        if ratio < 0.75:
            return f"cheap ({(ratio - 1) * 100:.0f}% vs sector)"
        return f"inline ({(ratio - 1) * 100:+.0f}% vs sector)"

    @property
    def growth_relative(self) -> str:
        t = self.target_revenue_growth_yoy
        m = self.median_revenue_growth_yoy
        if t is None or m is None:
            return "—"
        delta = t - m
        if delta > 0.05:
            return f"out-growing peers (+{delta * 100:.1f}pp)"
        if delta < -0.05:
            return f"under-growing peers ({delta * 100:.1f}pp)"
        return "in-line growth"

    @property
    def margin_relative(self) -> str:
        t = self.target_profit_margins
        m = self.median_profit_margins
        if t is None or m is None:
            return "—"
        delta = t - m
        if delta > 0.05:
            return f"premium margins (+{delta * 100:.1f}pp)"
        if delta < -0.05:
            return f"sub-par margins ({delta * 100:.1f}pp)"
        return "in-line margins"


# Curated `(sector, industry-key)` → peers. `industry-key` is a substring match
# (case-insensitive, applied to yfinance.info["industry"]). Falls back to
# `sector` only if no industry match. Ticker LISTS exclude the target itself
# at lookup time.
_INDUSTRY_PEERS: dict[tuple[str, str], list[str]] = {
    # Technology
    ("Technology", "semiconductor"): ["NVDA", "AMD", "AVGO", "MU", "INTC", "QCOM"],
    ("Technology", "semiconductor equipment"): ["AMAT", "LRCX", "KLAC", "ASML"],
    ("Technology", "consumer electronics"): ["AAPL", "HPQ", "DELL", "SONY"],
    ("Technology", "software—application"): ["MSFT", "CRM", "ADBE", "NOW", "INTU"],
    ("Technology", "software application"): ["MSFT", "CRM", "ADBE", "NOW", "INTU"],
    ("Technology", "software—infrastructure"): ["MSFT", "ORCL", "SNOW", "NET", "CRWD"],
    ("Technology", "software infrastructure"): ["MSFT", "ORCL", "SNOW", "NET", "CRWD"],
    ("Technology", "information technology services"): ["IBM", "ACN", "INFY", "WIT"],
    ("Technology", "computer hardware"): ["AAPL", "HPQ", "DELL", "STX", "WDC"],
    ("Technology", "electronic components"): ["TEL", "APH", "GLW", "FLEX"],
    # Communication Services
    ("Communication Services", "internet content"): ["GOOGL", "META", "NFLX", "DIS", "PINS"],
    ("Communication Services", "entertainment"): ["DIS", "NFLX", "WBD", "PARA"],
    ("Communication Services", "telecom services"): ["T", "VZ", "TMUS"],
    # Consumer Cyclical
    ("Consumer Cyclical", "auto manufacturers"): ["TSLA", "F", "GM", "RIVN", "LCID"],
    ("Consumer Cyclical", "internet retail"): ["AMZN", "SHOP", "MELI", "ETSY"],
    ("Consumer Cyclical", "specialty retail"): ["BBY", "ULTA", "TSCO", "FIVE"],
    ("Consumer Cyclical", "restaurants"): ["MCD", "SBUX", "CMG", "QSR"],
    ("Consumer Cyclical", "lodging"): ["MAR", "HLT", "H", "ABNB"],
    # Consumer Defensive
    ("Consumer Defensive", "discount stores"): ["WMT", "COST", "TGT", "DG"],
    ("Consumer Defensive", "beverages—non-alcoholic"): ["KO", "PEP", "MNST", "KDP"],
    ("Consumer Defensive", "packaged foods"): ["KHC", "GIS", "K", "MDLZ"],
    # Energy
    ("Energy", "oil & gas e&p"): ["XOM", "CVX", "COP", "EOG", "FANG"],
    ("Energy", "oil & gas integrated"): ["XOM", "CVX", "BP", "SHEL"],
    ("Energy", "oil & gas refining"): ["MPC", "VLO", "PSX", "DK"],
    # Healthcare
    ("Healthcare", "drug manufacturers—general"): ["JNJ", "PFE", "MRK", "LLY", "NVO"],
    ("Healthcare", "drug manufacturers general"): ["JNJ", "PFE", "MRK", "LLY", "NVO"],
    ("Healthcare", "biotechnology"): ["AMGN", "GILD", "REGN", "VRTX", "BIIB"],
    ("Healthcare", "medical devices"): ["MDT", "ABT", "BSX", "SYK", "EW"],
    # Financial Services
    ("Financial Services", "banks—diversified"): ["JPM", "BAC", "C", "WFC"],
    ("Financial Services", "capital markets"): ["GS", "MS", "BLK", "SCHW", "HOOD", "COIN"],
    ("Financial Services", "insurance"): ["BRK-B", "PGR", "ALL", "TRV"],
    # Industrials
    ("Industrials", "aerospace & defense"): ["BA", "LMT", "RTX", "NOC", "GD"],
    ("Industrials", "railroads"): ["UNP", "CSX", "NSC", "CP"],
    ("Industrials", "specialty industrial machinery"): ["HON", "ETN", "ITW", "PH"],
    ("Industrials", "electrical equipment"): ["GE", "ETN", "EMR", "ROK", "PLUG", "FCEL", "BE"],
    # Real Estate
    ("Real Estate", "reit"): ["O", "AMT", "PLD", "EQIX", "SPG"],
    # Utilities
    ("Utilities", "utilities—regulated electric"): ["NEE", "DUK", "SO", "AEP"],
    # Materials / Basic Materials
    ("Basic Materials", "specialty chemicals"): ["LIN", "APD", "SHW", "PPG"],
    ("Basic Materials", "gold"): ["NEM", "GOLD", "AEM", "KGC"],
    # Crypto / miners (yfinance puts these under Financial Services|Capital Markets
    # but they trade more like commodity producers; carry a specialised set).
    ("CRYPTO_MINERS", "crypto"): ["MARA", "RIOT", "CLSK", "WULF", "BTBT", "HUT", "IREN"],
}

# Sector-level fallback if industry doesn't match.
_SECTOR_FALLBACK_PEERS: dict[str, list[str]] = {
    "Technology": ["MSFT", "AAPL", "GOOGL", "META", "AMZN", "NVDA"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS"],
    "Consumer Cyclical": ["AMZN", "TSLA", "HD", "NKE", "MCD"],
    "Consumer Defensive": ["WMT", "PG", "KO", "PEP", "COST"],
    "Energy": ["XOM", "CVX", "COP", "EOG"],
    "Healthcare": ["JNJ", "PFE", "MRK", "LLY", "ABBV"],
    "Financial Services": ["JPM", "BAC", "BRK-B", "V", "MA"],
    "Industrials": ["GE", "HON", "CAT", "UPS", "BA"],
    "Real Estate": ["O", "AMT", "PLD", "EQIX"],
    "Utilities": ["NEE", "DUK", "SO", "AEP"],
    "Basic Materials": ["LIN", "APD", "SHW", "NEM"],
}


def _resolve_peers(ticker: str, sector: str | None, industry: str | None) -> list[str]:
    if not sector and not industry:
        return []
    industry_l = (industry or "").lower()

    # Crypto-miner override: yfinance classifies these under Capital Markets,
    # but compared against banks the multiples are nonsense.
    if "crypto" in industry_l or ticker.upper() in {
        "MARA", "RIOT", "CLSK", "WULF", "BTBT", "HUT", "IREN", "BITF", "CIFR",
    }:
        peers = list(_INDUSTRY_PEERS[("CRYPTO_MINERS", "crypto")])
        return [p for p in peers if p.upper() != ticker.upper()]

    if sector and industry_l:
        for (s, i_key), peers in _INDUSTRY_PEERS.items():
            if s != sector:
                continue
            if i_key.lower() in industry_l:
                return [p for p in peers if p.upper() != ticker.upper()]

    if sector and sector in _SECTOR_FALLBACK_PEERS:
        peers = _SECTOR_FALLBACK_PEERS[sector]
        return [p for p in peers if p.upper() != ticker.upper()]

    return []


def fetch_peer_comparison(
    ticker: str,
    *,
    sector: str | None,
    industry: str | None,
    target_metadata: dict[str, Any] | None = None,
) -> PeerComparison:
    """Compute sector/industry-median ratios across a curated peer set.

    `target_metadata` should be the already-fetched yfinance.info dict for
    `ticker` (we use it for the target's own ratios so we don't re-fetch).
    """
    peers = _resolve_peers(ticker, sector, industry)
    target_meta = target_metadata or {}
    target_pack = _extract_metrics_pack(target_meta)

    if not peers:
        return PeerComparison(
            peer_tickers=[],
            peer_set_available=False,
            note=(
                f"no curated peer set for {sector or '?'} / {industry or '?'} — "
                "comparison unavailable"
            ),
            target_forward_pe=target_pack.get("forward_pe"),
            target_peg=target_pack.get("peg"),
            target_revenue_growth_yoy=target_pack.get("revenue_growth_yoy"),
            target_profit_margins=target_pack.get("profit_margins"),
        )

    return _build_peer_comparison(peers, target_pack)


@lru_cache(maxsize=64)
def _peer_metadata_pack(peer: str) -> tuple[tuple[str, float | None], ...]:
    """Cacheable yfinance.info fetch reduced to the metric tuple we need."""
    try:
        import yfinance as yf

        info = yf.Ticker(peer).get_info() or {}
    except Exception as e:  # pragma: no cover — yfinance flakiness
        log.warning("peer.metadata.error", peer=peer, error=str(e))
        return tuple()
    pack = _extract_metrics_pack(info)
    return tuple(pack.items())


def _build_peer_comparison(peers: list[str], target_pack: dict[str, float | None]) -> PeerComparison:
    forward_pe: list[float] = []
    trailing_pe: list[float] = []
    peg: list[float] = []
    ps: list[float] = []
    rev_growth: list[float] = []
    profit_m: list[float] = []
    operating_m: list[float] = []
    mc: list[float] = []
    success_peers: list[str] = []
    for p in peers:
        pack = dict(_peer_metadata_pack(p))
        if not pack:
            continue
        # Skip the peer entirely if we got nothing useful; otherwise contribute
        # whatever fields it has.
        added = False
        for source, dest in (
            ("forward_pe", forward_pe),
            ("trailing_pe", trailing_pe),
            ("peg", peg),
            ("price_to_sales_ttm", ps),
            ("revenue_growth_yoy", rev_growth),
            ("profit_margins", profit_m),
            ("operating_margins", operating_m),
            ("market_cap", mc),
        ):
            v = pack.get(source)
            if v is not None and _is_sane(source, v):
                dest.append(v)
                added = True
        if added:
            success_peers.append(p)

    def _median(xs: list[float]) -> float | None:
        return statistics.median(xs) if xs else None

    return PeerComparison(
        peer_tickers=success_peers,
        peer_set_available=bool(success_peers),
        note=None if success_peers else "peers fetched but no usable metrics",
        median_forward_pe=_median(forward_pe),
        median_trailing_pe=_median(trailing_pe),
        median_peg=_median(peg),
        median_price_to_sales=_median(ps),
        median_revenue_growth_yoy=_median(rev_growth),
        median_profit_margins=_median(profit_m),
        median_operating_margins=_median(operating_m),
        median_market_cap=_median(mc),
        target_forward_pe=target_pack.get("forward_pe"),
        target_peg=target_pack.get("peg"),
        target_revenue_growth_yoy=target_pack.get("revenue_growth_yoy"),
        target_profit_margins=target_pack.get("profit_margins"),
    )


def _extract_metrics_pack(info: dict[str, Any]) -> dict[str, float | None]:
    return {
        "forward_pe": _safe_float(info.get("forwardPE")),
        "trailing_pe": _safe_float(info.get("trailingPE")),
        "peg": _safe_float(info.get("pegRatio") or info.get("trailingPegRatio")),
        "price_to_sales_ttm": _safe_float(info.get("priceToSalesTrailing12Months")),
        "revenue_growth_yoy": _safe_float(info.get("revenueGrowth")),
        "profit_margins": _safe_float(info.get("profitMargins")),
        "operating_margins": _safe_float(info.get("operatingMargins")),
        "market_cap": _safe_float(info.get("marketCap")),
    }


def _is_sane(metric: str, v: float) -> bool:
    """Drop obvious garbage so medians aren't poisoned (e.g. forward P/E of 9999)."""
    if metric in ("forward_pe", "trailing_pe"):
        return -200 < v < 500
    if metric == "peg":
        return -50 < v < 50
    if metric == "price_to_sales_ttm":
        return 0 < v < 200
    if metric in ("revenue_growth_yoy", "profit_margins", "operating_margins"):
        return -2 < v < 5
    return True


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


__all__ = ["PeerComparison", "fetch_peer_comparison"]
