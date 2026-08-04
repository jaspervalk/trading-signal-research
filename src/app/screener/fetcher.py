"""Per-ticker metrics fetcher.

Wraps yfinance to pull the fields each screener filter needs. Every field
is allowed to be `None` — partial data is the common case and the pipeline
must keep going. The fetcher caches the *raw payload* to disk (24h TTL)
and renormalises into `TickerMetrics` on every call so a config tweak
(e.g. adding a new derived field) doesn't require a cache wipe.

Network calls are gated by an optional rate-limit `delay_seconds`; the
pipeline supplies a shared semaphore to keep yfinance happy.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Optional

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from app.logging import get_logger
from app.screener import cache
from app.screener.schema import TickerMetrics

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Field plucking helpers — every one must tolerate missing/NaN values.


def _f(x: Any) -> Optional[float]:
    """Coerce to float, returning None for NaN/missing/non-numeric."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _i(x: Any) -> Optional[int]:
    f = _f(x)
    return int(f) if f is not None else None


# ---------------------------------------------------------------------------
# Yahoo Finance fetch — kept thin and retryable.


@retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4), reraise=True)
def _fetch_raw(ticker: str) -> dict[str, Any]:
    """Single network round trip. Returns a dict of all pieces we'll need."""
    import yfinance as yf

    yt = yf.Ticker(ticker)
    info = dict(yt.info) if yt.info else {}

    # Daily history for SMA200 + relative strength. 1 year is enough for
    # both. We strip down to OHLC to keep the cache file small.
    hist = yt.history(period="1y", auto_adjust=True)
    history_rows: list[dict[str, Any]] = []
    if isinstance(hist, pd.DataFrame) and not hist.empty:
        h = hist[["Close"]].copy()
        h.index = pd.to_datetime(h.index, utc=True)
        history_rows = [
            {"ts": idx.isoformat(), "close": _f(row["Close"])}
            for idx, row in h.iterrows()
        ]

    # Earnings calendar (days-to-earnings)
    calendar_raw: dict[str, Any] = {}
    try:
        cal = yt.calendar
        if isinstance(cal, dict):
            for k, v in cal.items():
                if isinstance(v, list):
                    calendar_raw[k] = [str(x) for x in v]
                else:
                    calendar_raw[k] = str(v) if v is not None else None
    except Exception as e:  # pragma: no cover — yfinance flake surface
        log.debug("screener.fetch.calendar_skip", ticker=ticker, error=str(e))

    return {"info": info, "history": history_rows, "calendar": calendar_raw}


def _spy_history(now: float | None = None) -> list[dict[str, Any]]:
    """SPY benchmark history, cached separately so it isn't fetched per ticker."""
    payload = cache.get("__SPY__", now=now)
    if payload is not None and "history" in payload:
        return payload["history"]
    raw = _fetch_raw("SPY")
    cache.put("__SPY__", raw, now=now)
    return raw["history"]


# ---------------------------------------------------------------------------
# Metric assembly


def _compute_returns(
    history: list[dict[str, Any]],
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (last_close, sma_200, 63d_return)."""
    if not history:
        return None, None, None
    closes = [r["close"] for r in history if r.get("close") is not None]
    if len(closes) < 2:
        return None, None, None
    last = closes[-1]
    sma_200 = sum(closes[-200:]) / min(len(closes), 200) if len(closes) >= 50 else None
    # 3-month return ~ 63 trading days
    if len(closes) >= 63:
        ret_63 = (last / closes[-63]) - 1.0
    else:
        ret_63 = None
    return _f(last), _f(sma_200), _f(ret_63)


def _days_to_earnings(calendar_raw: dict[str, Any]) -> Optional[int]:
    """Pull next earnings date from yfinance.calendar, return days until."""
    if not calendar_raw:
        return None
    candidates: list[str] = []
    for key in ("Earnings Date", "earningsDate"):
        v = calendar_raw.get(key)
        if isinstance(v, list):
            candidates.extend(v)
        elif isinstance(v, str):
            candidates.append(v)
    for c in candidates:
        try:
            dt = pd.to_datetime(c, utc=True)
            now = pd.Timestamp.utcnow().tz_convert("UTC")
            delta_days = int((dt - now).total_seconds() // 86400)
            if delta_days >= -3:  # allow up to 3 days in the past (yfinance lag)
                return max(delta_days, 0)
        except (ValueError, TypeError):
            continue
    return None


def _quality_tags(metrics: TickerMetrics) -> list[str]:
    tags: list[str] = []
    if metrics.forward_pe is None and metrics.trailing_pe is None:
        tags.append("no_pe")
    if metrics.revenue_growth_yoy is None:
        tags.append("no_revenue_growth")
    if metrics.gross_margin is None:
        tags.append("no_gross_margin")
    if metrics.roe is None:
        tags.append("no_roe")
    if metrics.sma_200 is None:
        tags.append("no_sma_200")
    if metrics.rs_vs_spy_3mo is None:
        tags.append("no_relative_strength")
    return tags


def build_metrics(
    ticker: str,
    *,
    raw: dict[str, Any],
    spy_history: list[dict[str, Any]] | None = None,
) -> TickerMetrics:
    """Renormalise a raw cache payload into typed `TickerMetrics`."""
    info = raw.get("info") or {}
    history = raw.get("history") or []
    calendar_raw = raw.get("calendar") or {}

    last_close, sma_200, ret_63 = _compute_returns(history)
    pct_vs_200d = (
        (last_close - sma_200) / sma_200
        if (last_close is not None and sma_200 not in (None, 0))
        else None
    )

    spy_ret_63: Optional[float] = None
    if spy_history:
        _, _, spy_ret_63 = _compute_returns(spy_history)
    rs_vs_spy = (
        ret_63 - spy_ret_63
        if (ret_63 is not None and spy_ret_63 is not None)
        else None
    )

    metrics = TickerMetrics(
        ticker=ticker.upper(),
        sector=info.get("sector") or None,
        industry=info.get("industry") or None,
        market_cap=_f(info.get("marketCap")),
        forward_pe=_f(info.get("forwardPE")),
        trailing_pe=_f(info.get("trailingPE")),
        peg_ratio=_f(info.get("pegRatio") or info.get("trailingPegRatio")),
        ev_to_ebitda=_f(info.get("enterpriseToEbitda")),
        revenue_growth_yoy=_f(info.get("revenueGrowth")),
        earnings_growth_yoy=_f(info.get("earningsGrowth")),
        revenue_growth_qoq=_f(info.get("revenueQuarterlyGrowth")),
        gross_margin=_f(info.get("grossMargins")),
        roe=_f(info.get("returnOnEquity")),
        debt_to_equity=(
            _f(info.get("debtToEquity")) / 100.0
            if _f(info.get("debtToEquity")) is not None
            else None
        ),
        last_close=last_close,
        sma_200=sma_200,
        pct_vs_200d=pct_vs_200d,
        rs_vs_spy_3mo=rs_vs_spy,
        days_to_earnings=_days_to_earnings(calendar_raw),
    )
    metrics.data_quality = _quality_tags(metrics)
    return metrics


def fetch_metrics(
    ticker: str,
    *,
    use_cache: bool = True,
    ttl_hours: float = cache.DEFAULT_TTL_HOURS,
    rate_limit_delay: float = 0.0,
    fetcher: Callable[[str], dict[str, Any]] | None = None,
    spy_fetcher: Callable[[], list[dict[str, Any]]] | None = None,
    now: float | None = None,
) -> TickerMetrics:
    """Fetch (cached) metrics for one ticker.

    `fetcher` and `spy_fetcher` are injectable for tests — production
    callers leave them as None and the real yfinance code runs.
    """
    raw = None
    if use_cache:
        raw = cache.get(ticker, ttl_hours=ttl_hours, now=now)
    if raw is None:
        fn = fetcher or _fetch_raw
        if rate_limit_delay > 0:
            time.sleep(rate_limit_delay)
        raw = fn(ticker)
        if use_cache:
            cache.put(ticker, raw, now=now)
    spy_hist = (spy_fetcher or _spy_history)()
    return build_metrics(ticker, raw=raw, spy_history=spy_hist)


__all__ = ["build_metrics", "fetch_metrics"]
