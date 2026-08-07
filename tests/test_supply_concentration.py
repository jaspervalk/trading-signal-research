"""Tests for end-market concentration reporting (Change 2).

REPORTING ONLY: this module never reorders or filters anything — every test
here works over an already-decided rank order and just tallies it.
"""

from __future__ import annotations

from app.supply.concentration import (
    DEFAULT_TOP_N,
    compute_end_market_concentration,
)


def test_dominant_end_market_over_top_n():
    ranking = ["TROX", "KRO", "CC", "HUN", "WLK"]
    end_markets = {
        "TROX": "construction",
        "KRO": "construction",
        "CC": "construction",
        "HUN": "construction",
        "WLK": "construction",
    }
    result = compute_end_market_concentration(ranking, end_markets, top_n=5)
    assert result.dominant_end_market == "construction"
    assert result.dominant_count == 5
    assert result.rows_considered == 5
    assert result.rows_classified == 5
    assert "5 of top 5" in result.summary_line
    assert "construction" in result.summary_line


def test_four_of_top_five_matches_the_owners_example():
    ranking = ["TROX", "KRO", "CC", "HUN", "AAPL"]
    end_markets = {
        "TROX": "construction",
        "KRO": "construction",
        "CC": "construction",
        "HUN": "construction",
        "AAPL": "consumer",
    }
    result = compute_end_market_concentration(ranking, end_markets, top_n=5)
    assert result.dominant_end_market == "construction"
    assert result.dominant_count == 4
    assert result.summary_line == "4 of top 5 share end market 'construction'"


def test_blank_end_markets_are_excluded_not_a_phantom_group():
    ranking = ["A", "B", "C", "D", "E"]
    end_markets = {
        "A": "construction",
        "B": "",  # blank -- must not form its own group or count anywhere
        "C": None,  # missing entirely -- same treatment
        "D": "construction",
        "E": "   ",  # whitespace-only -- also blank
    }
    result = compute_end_market_concentration(ranking, end_markets, top_n=5)
    assert result.rows_considered == 5
    assert result.rows_classified == 2  # only A and D carried a real end_market
    assert result.dominant_end_market == "construction"
    assert result.dominant_count == 2
    assert "" not in result.breakdown
    assert result.breakdown == {"construction": 2}


def test_missing_ticker_in_lookup_is_treated_as_blank():
    ranking = ["A", "UNKNOWN"]
    end_markets = {"A": "energy"}
    result = compute_end_market_concentration(ranking, end_markets, top_n=2)
    assert result.rows_classified == 1
    assert result.dominant_end_market == "energy"
    assert result.dominant_count == 1
    # A single classified row is not "concentration" (need >= 2 to say so).
    assert result.summary_line == "no repeated end market in the top 2"


def test_no_repeated_end_market_reports_plainly():
    ranking = ["A", "B", "C"]
    end_markets = {"A": "energy", "B": "autos", "C": "healthcare"}
    result = compute_end_market_concentration(ranking, end_markets, top_n=3)
    assert result.dominant_count == 1
    assert result.summary_line == "no repeated end market in the top 3"


def test_all_blank_reports_no_dominant_and_no_breakdown():
    ranking = ["A", "B"]
    end_markets = {"A": "", "B": ""}
    result = compute_end_market_concentration(ranking, end_markets, top_n=2)
    assert result.dominant_end_market is None
    assert result.dominant_count == 0
    assert result.breakdown == {}
    assert result.summary_line == "no repeated end market in the top 2"


def test_top_n_truncates_the_ranking_before_tallying():
    ranking = ["A", "B", "C", "D"]
    end_markets = {"A": "x", "B": "x", "C": "x", "D": "x"}
    result = compute_end_market_concentration(ranking, end_markets, top_n=2)
    assert result.rows_considered == 2
    assert result.dominant_count == 2  # only A, B counted -- C, D excluded


def test_top_n_larger_than_ranking_uses_all_available_rows():
    ranking = ["A", "B"]
    end_markets = {"A": "x", "B": "x"}
    result = compute_end_market_concentration(ranking, end_markets, top_n=10)
    assert result.rows_considered == 2
    assert result.top_n == 10


def test_ticker_lookup_is_case_insensitive():
    ranking = ["trox", "KRO"]
    end_markets = {"TROX": "construction", "KRO": "construction"}
    result = compute_end_market_concentration(ranking, end_markets, top_n=2)
    assert result.dominant_count == 2


def test_default_top_n_is_five():
    assert DEFAULT_TOP_N == 5
    ranking = ["A", "B", "C", "D", "E", "F"]
    end_markets = {t: "x" for t in ranking}
    result = compute_end_market_concentration(ranking, end_markets)
    assert result.top_n == 5
    assert result.rows_considered == 5  # F excluded by the default top_n


def test_ordering_is_deterministic_given_a_fixed_ranking():
    ranking = ["A", "B", "C", "D", "E"]
    end_markets = {"A": "x", "B": "x", "C": "y", "D": "y", "E": "y"}
    r1 = compute_end_market_concentration(ranking, end_markets, top_n=5)
    r2 = compute_end_market_concentration(ranking, end_markets, top_n=5)
    assert r1 == r2
    assert r1.dominant_end_market == "y"
    assert r1.dominant_count == 3
