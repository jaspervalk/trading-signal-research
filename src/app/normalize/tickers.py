"""Ticker recovery from noisy ASR transcripts.

Strategy:
  - $-prefixed tickers: highest confidence.
  - Cased ticker tokens that match the universe: high confidence.
  - Lowercase company names → ticker via name map: medium confidence.
  - ASR confusion map (a small, conservative dictionary): medium confidence.
  - Spelled-out tickers ("n v d a"): medium confidence.

We deliberately do NOT match bare lowercase tokens against the universe
(e.g. "and" → AMD, "spy" → SPY) because the false-positive rate is brutal.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from app.config import REPO_ROOT


@dataclass(frozen=True)
class TickerMention:
    ticker: str
    span: tuple[int, int]  # (start, end) char offsets in source text
    matched_text: str
    method: str  # 'dollar' | 'cased' | 'company_name' | 'asr_map' | 'spelled_out'
    confidence: float


@dataclass
class Universe:
    by_ticker: dict[str, dict[str, str]] = field(default_factory=dict)
    # lowercase company-name token → canonical ticker
    name_to_ticker: dict[str, str] = field(default_factory=dict)

    @property
    def tickers(self) -> set[str]:
        return set(self.by_ticker.keys())

    def has(self, ticker: str) -> bool:
        return ticker.upper() in self.by_ticker


# Conservative ASR-confusion map. Add entries only after seeing real false-negatives.
# Keys are lowercase phrases observed in transcripts. Values are canonical tickers.
ASR_CONFUSIONS: dict[str, str] = {
    # Common multi-letter spellouts that ASR runs together or splits weirdly.
    "in video": "NVDA",
    "in vidia": "NVDA",
    "n video": "NVDA",
    "tesla": "TSLA",
    "apple": "AAPL",
    "microsoft": "MSFT",
    "amazon": "AMZN",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "netflix": "NFLX",
    "broadcom": "AVGO",
    "advanced micro devices": "AMD",
    "intel": "INTC",
    "qualcomm": "QCOM",
    "salesforce": "CRM",
    "oracle": "ORCL",
    "adobe": "ADBE",
    "palantir": "PLTR",
    "snowflake": "SNOW",
    "crowdstrike": "CRWD",
    "shopify": "SHOP",
    "block inc": "SQ",
    "coinbase": "COIN",
    "robinhood": "HOOD",
    "jpmorgan": "JPM",
    "goldman sachs": "GS",
    "morgan stanley": "MS",
    "berkshire": "BRK.B",
    "united health": "UNH",
    "unitedhealth": "UNH",
    "eli lilly": "LLY",
    "exxon": "XOM",
    "chevron": "CVX",
    "walmart": "WMT",
    "costco": "COST",
    "procter and gamble": "PG",
    "coca cola": "KO",
    "coca-cola": "KO",
    "pepsi": "PEP",
    "home depot": "HD",
    "starbucks": "SBUX",
    "mcdonalds": "MCD",
    "mcdonald's": "MCD",
    "disney": "DIS",
    "boeing": "BA",
    "caterpillar": "CAT",
    "deere": "DE",
    "alibaba": "BABA",
    "uber": "UBER",
    "airbnb": "ABNB",
    "spotify": "SPOT",
    "roku": "ROKU",
    "snapchat": "SNAP",
    "roblox": "RBLX",
    "super micro": "SMCI",
    "supermicro": "SMCI",
    "arm holdings": "ARM",
}


_DOLLAR_TICKER = re.compile(r"\$([A-Z]{1,5}(?:\.[A-Z])?)\b")
_CASED_TOKEN = re.compile(r"\b[A-Z]{2,5}(?:\.[A-Z])?\b")
_SPELLED_OUT = re.compile(r"\b([a-z](?:\s+[a-z]){2,4})\b")


def load_universe(path: Path | None = None) -> Universe:
    """Load tickers from configs/universe.csv. Adds company-name shortcuts."""
    if path is None:
        path = REPO_ROOT / "configs" / "universe.csv"
    u = Universe()
    if not path.exists():
        return u

    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = (row.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            u.by_ticker[ticker] = {
                "name": (row.get("name") or "").strip(),
                "sector": (row.get("sector") or "").strip(),
            }
            # Index by first significant word of the company name (lowercased).
            # E.g. "Bank of America" → "bank" — too lossy, skip those. Only
            # index single-word company names; multi-word names rely on ASR_CONFUSIONS.
            name_lower = (row.get("name") or "").strip().lower()
            if name_lower and " " not in name_lower and name_lower not in u.name_to_ticker:
                u.name_to_ticker[name_lower] = ticker
    return u


def detect_tickers(text: str, universe: Universe) -> list[TickerMention]:
    """Find ticker mentions in text. Returns mentions in source-order."""
    if not text or not universe.by_ticker:
        return []

    mentions: list[TickerMention] = []
    seen: set[tuple[str, int]] = set()  # (ticker, start) dedupe

    # 1. $-prefixed
    for m in _DOLLAR_TICKER.finditer(text):
        ticker = m.group(1).upper()
        if not universe.has(ticker):
            continue
        key = (ticker, m.start())
        if key in seen:
            continue
        seen.add(key)
        mentions.append(
            TickerMention(
                ticker=ticker,
                span=(m.start(), m.end()),
                matched_text=m.group(0),
                method="dollar",
                confidence=0.95,
            )
        )

    # 2. Cased uppercase tokens that match the universe.
    # NB: ASR usually doesn't capitalize, so this hits user-typed
    # descriptions / titles / chat messages more than transcripts.
    for m in _CASED_TOKEN.finditer(text):
        token = m.group(0).upper()
        if not universe.has(token):
            continue
        # Skip if this is the inside of a $-prefix already captured.
        if m.start() > 0 and text[m.start() - 1] == "$":
            continue
        # Skip super-common English words even if they're in the universe.
        if token in {"A", "I", "AT", "ON", "BE", "AS", "BY", "DO", "GO", "IF", "IS", "IT", "OR", "SO", "TO", "UP", "WE"}:
            continue
        key = (token, m.start())
        if key in seen:
            continue
        seen.add(key)
        mentions.append(
            TickerMention(
                ticker=token,
                span=(m.start(), m.end()),
                matched_text=m.group(0),
                method="cased",
                confidence=0.85,
            )
        )

    # 3. Company name (lowercase, single-word) → ticker.
    lower = text.lower()
    for name, ticker in universe.name_to_ticker.items():
        # Word-boundary match.
        for m in re.finditer(rf"\b{re.escape(name)}\b", lower):
            key = (ticker, m.start())
            if key in seen:
                continue
            seen.add(key)
            mentions.append(
                TickerMention(
                    ticker=ticker,
                    span=(m.start(), m.end()),
                    matched_text=text[m.start() : m.end()],
                    method="company_name",
                    confidence=0.7,
                )
            )

    # 4. ASR confusion map (multi-word phrases + known mishearings).
    for phrase, ticker in ASR_CONFUSIONS.items():
        for m in re.finditer(rf"\b{re.escape(phrase)}\b", lower):
            key = (ticker, m.start())
            if key in seen:
                continue
            seen.add(key)
            mentions.append(
                TickerMention(
                    ticker=ticker,
                    span=(m.start(), m.end()),
                    matched_text=text[m.start() : m.end()],
                    method="asr_map",
                    confidence=0.65,
                )
            )

    # 5. Spelled-out tickers, e.g. "n v d a" → NVDA.
    for m in _SPELLED_OUT.finditer(lower):
        letters = re.sub(r"\s+", "", m.group(1)).upper()
        if 2 <= len(letters) <= 5 and universe.has(letters):
            key = (letters, m.start())
            if key in seen:
                continue
            seen.add(key)
            mentions.append(
                TickerMention(
                    ticker=letters,
                    span=(m.start(), m.end()),
                    matched_text=text[m.start() : m.end()],
                    method="spelled_out",
                    confidence=0.6,
                )
            )

    mentions.sort(key=lambda x: x.span[0])
    return mentions


def unique_tickers(mentions: Iterable[TickerMention]) -> list[str]:
    """Distinct tickers preserving first-occurrence order."""
    seen: set[str] = set()
    out: list[str] = []
    for m in mentions:
        if m.ticker not in seen:
            seen.add(m.ticker)
            out.append(m.ticker)
    return out
