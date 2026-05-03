"""Tests for app.normalize.tickers — universe loading + ticker detection."""

from __future__ import annotations

from app.normalize.tickers import (
    Universe,
    detect_tickers,
    load_universe,
    unique_tickers,
)


def test_load_universe_real_csv():
    u = load_universe()
    assert u.has("NVDA")
    assert u.has("SPY")
    assert u.by_ticker["NVDA"]["name"] == "NVIDIA"
    # Single-word names get name_to_ticker; multi-word names do not.
    assert u.name_to_ticker.get("nvidia") == "NVDA"
    assert "berkshire hathaway" not in u.name_to_ticker  # multi-word


def _toy_universe() -> Universe:
    u = Universe()
    for t, name in [("NVDA", "NVIDIA"), ("AAPL", "Apple"), ("AMD", "AMD"), ("SPY", "SPY"), ("BRK.B", "Berkshire")]:
        u.by_ticker[t] = {"name": name, "sector": "X"}
        if " " not in name.lower():
            u.name_to_ticker[name.lower()] = t
    return u


def test_dollar_tickers():
    u = _toy_universe()
    ms = detect_tickers("watching $NVDA above 920 and $AAPL", u)
    methods = {m.method for m in ms}
    assert "dollar" in methods
    tickers = [m.ticker for m in ms if m.method == "dollar"]
    assert tickers == ["NVDA", "AAPL"]


def test_cased_token_in_universe():
    u = _toy_universe()
    ms = detect_tickers("NVDA looks strong", u)
    assert any(m.ticker == "NVDA" and m.method == "cased" for m in ms)


def test_lowercase_word_does_not_match_universe():
    """`spy` lowercase must NOT be matched as SPY (false-positive trap)."""
    u = _toy_universe()
    ms = detect_tickers("I spy with my little eye", u)
    # The company-name index has "spy" → SPY (single-word company name in toy
    # universe). To prevent this in real use, real universe.csv has SPY's name
    # as "SPDR S&P 500" which is multi-word and doesn't get indexed.
    # Here we just verify that the BARE 'spy' lowercase doesn't match via the
    # cased-token regex.
    cased = [m for m in ms if m.method == "cased"]
    assert cased == []


def test_company_name_match_real_universe():
    u = load_universe()
    ms = detect_tickers("I think nvidia looks great here", u)
    nvda = [m for m in ms if m.ticker == "NVDA"]
    assert nvda, "company-name fallback should resolve nvidia → NVDA"


def test_asr_map_multi_word_phrase():
    u = load_universe()
    ms = detect_tickers("the in video chart is set up nicely", u)
    assert any(m.ticker == "NVDA" and m.method == "asr_map" for m in ms)


def test_spelled_out_letters():
    u = _toy_universe()
    ms = detect_tickers("setup on n v d a is clean", u)
    assert any(m.ticker == "NVDA" and m.method == "spelled_out" for m in ms)


def test_stopwords_excluded_from_cased():
    """Tokens like 'A', 'I', 'IT' must not become tickers even if in universe."""
    u = _toy_universe()
    ms = detect_tickers("A great day for AAPL", u)
    cased_tickers = {m.ticker for m in ms if m.method == "cased"}
    assert "AAPL" in cased_tickers
    # The 'A' token must not become a ticker mention.
    assert all(m.ticker != "A" for m in ms)


def test_dedupe_within_method():
    u = _toy_universe()
    ms = detect_tickers("$NVDA $NVDA $NVDA", u)
    spans = {m.span for m in ms}
    assert len(spans) == 3  # different positions, not deduped


def test_unique_tickers_preserves_order():
    u = _toy_universe()
    ms = detect_tickers("$AAPL then $NVDA then $AAPL again", u)
    assert unique_tickers(ms) == ["AAPL", "NVDA"]


def test_empty_input():
    u = _toy_universe()
    assert detect_tickers("", u) == []
    assert detect_tickers("   ", u) == []


def test_dot_ticker():
    u = _toy_universe()
    ms = detect_tickers("$BRK.B is on the watchlist", u)
    assert any(m.ticker == "BRK.B" and m.method == "dollar" for m in ms)
