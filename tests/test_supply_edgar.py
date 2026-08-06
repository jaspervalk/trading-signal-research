"""EDGAR fact extraction: duration filtering, point-in-time dedup, tag chains."""

from __future__ import annotations

from datetime import date

import pytest

from app.supply.edgar import FactPoint, quarterly_series

def _fact(start, end, filed, val):
    return {"start": start, "end": end, "filed": filed, "val": val, "form": "10-Q"}


FACTS = {
    "facts": {
        "us-gaap": {
            "GrossProfit": {
                "units": {
                    "USD": [
                        # ~91 days: a quarter.
                        _fact("2024-01-01", "2024-03-31", "2024-05-01", 100.0),
                        # the same quarter restated in a later filing
                        _fact("2024-01-01", "2024-03-31", "2025-05-01", 111.0),
                        _fact("2024-04-01", "2024-06-30", "2024-08-01", 120.0),
                        # ~365 days: annual, must be excluded
                        _fact("2024-01-01", "2024-12-31", "2025-02-01", 500.0),
                        # no start: instant fact, must be excluded
                        {"end": "2024-06-30", "filed": "2024-08-01", "val": 9.0, "form": "10-Q"},
                    ]
                }
            },
            "CostOfRevenue": {
                "units": {"USD": [_fact("2024-01-01", "2024-03-31", "2024-05-01", 60.0)]}
            },
        }
    }
}


def test_only_quarter_length_durations_are_kept():
    pts = quarterly_series(FACTS, ["GrossProfit"])
    assert [p.end for p in pts] == [date(2024, 3, 31), date(2024, 6, 30)]


def test_earliest_filing_wins_so_the_series_is_point_in_time():
    pts = quarterly_series(FACTS, ["GrossProfit"])
    q1 = next(p for p in pts if p.end == date(2024, 3, 31))
    assert q1.value == 100.0  # not the 111.0 restatement
    assert q1.filed == date(2024, 5, 1)


def test_series_is_sorted_by_period_end():
    pts = quarterly_series(FACTS, ["GrossProfit"])
    assert [p.end for p in pts] == sorted(p.end for p in pts)


def test_tag_chain_falls_through_to_the_first_concept_present():
    pts = quarterly_series(FACTS, ["CostOfGoodsAndServicesSold", "CostOfRevenue"])
    assert len(pts) == 1
    assert pts[0].value == 60.0


def test_tag_chain_merges_across_concepts_without_double_counting():
    """Eras use different tags; a period covered by both takes the first listed."""
    facts = {
        "facts": {
            "us-gaap": {
                "A": {"units": {"USD": [_fact("2024-01-01", "2024-03-31", "2024-05-01", 1.0)]}},
                "B": {
                    "units": {
                        "USD": [
                            _fact("2024-01-01", "2024-03-31", "2024-05-01", 99.0),
                            _fact("2024-04-01", "2024-06-30", "2024-08-01", 2.0),
                        ]
                    }
                },
            }
        }
    }
    pts = quarterly_series(facts, ["A", "B"])
    assert [(p.end, p.value) for p in pts] == [
        (date(2024, 3, 31), 1.0),
        (date(2024, 6, 30), 2.0),
    ]


def test_missing_concepts_yield_an_empty_series():
    assert quarterly_series(FACTS, ["NoSuchConcept"]) == []


def test_user_agent_declares_contact_details():
    """SEC blocks anonymous clients by IP; the header is not optional."""
    from app.supply.edgar import SEC_USER_AGENT

    assert "@" in SEC_USER_AGENT
