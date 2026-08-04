"""Scan universe loader.

Reads `configs/screen_universe.csv` (versionable, hand-editable) and
optionally refreshes it from Wikipedia's S&P 500 + Nasdaq 100 tables.

CSV schema:  ticker,name,sector,source
- `source` is "sp500", "ndx", or "both" — informational only.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from app.config import REPO_ROOT
from app.logging import get_logger

log = get_logger(__name__)

DEFAULT_UNIVERSE_PATH = REPO_ROOT / "configs" / "screen_universe.csv"

SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX_WIKI_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"


@dataclass(frozen=True)
class UniverseEntry:
    ticker: str
    name: str = ""
    sector: str = ""
    source: str = ""  # "sp500" | "ndx" | "both"


def load_universe(
    path: Path = DEFAULT_UNIVERSE_PATH,
    *,
    sector: str | None = None,
) -> list[UniverseEntry]:
    """Load universe from CSV. Optionally filter by sector (case-insensitive)."""
    if not path.exists():
        raise FileNotFoundError(
            f"Screen universe not found at {path}. "
            f"Run `tsr screen-refresh-universe` to populate it."
        )
    out: list[UniverseEntry] = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = (row.get("ticker") or "").strip().upper()
            if not t:
                continue
            entry = UniverseEntry(
                ticker=t,
                name=(row.get("name") or "").strip(),
                sector=(row.get("sector") or "").strip(),
                source=(row.get("source") or "").strip(),
            )
            if sector and entry.sector.lower() != sector.lower():
                continue
            out.append(entry)
    log.info("screener.universe.loaded", path=str(path), n=len(out), sector=sector)
    return out


def fetch_from_wikipedia() -> list[UniverseEntry]:
    """Fetch current S&P 500 + Nasdaq 100 constituents from Wikipedia.

    Uses `pandas.read_html` — no API key required. Dedupes by ticker.
    Network-dependent. The schema here matches Wikipedia's table headers
    as of 2026-05; if Wikipedia restructures, this function needs an update.
    """
    import pandas as pd  # local import — heavy dep, screener users shouldn't pay unless refreshing

    log.info("screener.universe.fetch_start", urls=[SP500_WIKI_URL, NDX_WIKI_URL])

    sp_tables = pd.read_html(SP500_WIKI_URL)
    sp = sp_tables[0]  # first table = constituents
    sp_entries = {
        str(row["Symbol"]).strip().upper().replace(".", "-"): UniverseEntry(
            ticker=str(row["Symbol"]).strip().upper().replace(".", "-"),
            name=str(row.get("Security", "")).strip(),
            sector=str(row.get("GICS Sector", "")).strip(),
            source="sp500",
        )
        for _, row in sp.iterrows()
        if str(row.get("Symbol", "")).strip()
    }

    ndx_tables = pd.read_html(NDX_WIKI_URL)
    # Nasdaq-100 page has several tables; find one with "Ticker" or "Symbol" column.
    ndx_df = None
    for t in ndx_tables:
        cols = {c.lower() for c in t.columns.astype(str)}
        if {"ticker"} <= cols or {"symbol"} <= cols:
            ndx_df = t
            break
    if ndx_df is None:
        raise RuntimeError("Could not locate Nasdaq-100 constituents table on Wikipedia.")
    ticker_col = "Ticker" if "Ticker" in ndx_df.columns else "Symbol"
    sector_col = next(
        (c for c in ndx_df.columns if "sector" in str(c).lower() or "gics" in str(c).lower()),
        None,
    )
    name_col = next(
        (c for c in ndx_df.columns if "company" in str(c).lower() or "security" in str(c).lower()),
        None,
    )

    merged: dict[str, UniverseEntry] = dict(sp_entries)
    for _, row in ndx_df.iterrows():
        t = str(row[ticker_col]).strip().upper().replace(".", "-")
        if not t:
            continue
        if t in merged:
            existing = merged[t]
            merged[t] = UniverseEntry(
                ticker=t,
                name=existing.name,
                sector=existing.sector,
                source="both",
            )
        else:
            merged[t] = UniverseEntry(
                ticker=t,
                name=str(row.get(name_col, "")).strip() if name_col else "",
                sector=str(row.get(sector_col, "")).strip() if sector_col else "",
                source="ndx",
            )

    out = sorted(merged.values(), key=lambda e: e.ticker)
    log.info(
        "screener.universe.fetch_done",
        n_sp500=len(sp_entries),
        n_ndx=sum(1 for e in out if e.source in ("ndx", "both")),
        n_total=len(out),
    )
    return out


def write_universe(entries: list[UniverseEntry], path: Path = DEFAULT_UNIVERSE_PATH) -> None:
    """Persist entries to CSV. Overwrites."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "name", "sector", "source"])
        writer.writeheader()
        for e in entries:
            writer.writerow(
                {"ticker": e.ticker, "name": e.name, "sector": e.sector, "source": e.source}
            )
    log.info("screener.universe.written", path=str(path), n=len(entries))


__all__ = [
    "DEFAULT_UNIVERSE_PATH",
    "UniverseEntry",
    "fetch_from_wikipedia",
    "load_universe",
    "write_universe",
]
