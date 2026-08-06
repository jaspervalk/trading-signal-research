"""Gross-margin history assembled from EDGAR facts."""

from __future__ import annotations

from datetime import date

from app.supply.fundamentals import build_margin_history


def _q(start, end, filed, val):
    return {"start": start, "end": end, "filed": filed, "val": val, "form": "10-Q"}


def _facts(gp, cost=None, rev=None):
    gaap = {"GrossProfit": {"units": {"USD": gp}}}
    if cost:
        gaap["CostOfRevenue"] = {"units": {"USD": cost}}
    if rev:
        gaap["Revenues"] = {"units": {"USD": rev}}
    return {"facts": {"us-gaap": gaap}}


def test_margin_uses_revenue_when_the_revenue_tag_is_present():
    facts = _facts(
        gp=[_q("2024-01-01", "2024-03-31", "2024-05-01", 40.0)],
        rev=[_q("2024-01-01", "2024-03-31", "2024-05-01", 100.0)],
    )
    h = build_margin_history(facts)
    assert h.gross_margins == [0.40]
    assert h.revenues == [100.0]


def test_margin_falls_back_to_gross_profit_plus_cost():
    """The revenue tag migrated for many filers; profit and cost did not."""
    facts = _facts(
        gp=[_q("2024-01-01", "2024-03-31", "2024-05-01", 40.0)],
        cost=[_q("2024-01-01", "2024-03-31", "2024-05-01", 60.0)],
    )
    h = build_margin_history(facts)
    assert h.gross_margins == [0.40]
    assert h.revenues == [100.0]


def test_a_quarter_with_neither_revenue_nor_cost_is_dropped():
    facts = _facts(gp=[_q("2024-01-01", "2024-03-31", "2024-05-01", 40.0)])
    assert build_margin_history(facts).quarters == 0


def test_non_positive_revenue_is_dropped_rather_than_producing_a_wild_margin():
    facts = _facts(
        gp=[_q("2024-01-01", "2024-03-31", "2024-05-01", 40.0)],
        rev=[_q("2024-01-01", "2024-03-31", "2024-05-01", 0.0)],
    )
    assert build_margin_history(facts).quarters == 0


def test_history_reports_its_own_span_so_coverage_is_visible():
    facts = _facts(
        gp=[
            _q("2024-01-01", "2024-03-31", "2024-05-01", 40.0),
            _q("2024-04-01", "2024-06-30", "2024-08-01", 50.0),
        ],
        rev=[
            _q("2024-01-01", "2024-03-31", "2024-05-01", 100.0),
            _q("2024-04-01", "2024-06-30", "2024-08-01", 100.0),
        ],
    )
    h = build_margin_history(facts)
    assert h.quarters == 2
    assert h.first_end == date(2024, 3, 31)
    assert h.last_end == date(2024, 6, 30)


def test_empty_facts_produce_an_empty_history_not_an_error():
    h = build_margin_history({"facts": {"us-gaap": {}}})
    assert h.quarters == 0
    assert h.gross_margins == []


def test_gross_margin_above_one_is_dropped_and_counted():
    """A Q4 10-K quarter where the duration filter picked up a segment-scoped
    revenue fact an order of magnitude too small (see GE, ADR/bug writeup):
    gross profit resolves fine but revenue is far too small, producing an
    impossible >100% margin. This must be dropped, not reported.
    """
    facts = _facts(
        gp=[_q("2024-10-01", "2024-12-31", "2025-02-01", 7.56)],
        rev=[_q("2024-10-01", "2024-12-31", "2025-02-01", 2.58)],
    )
    h = build_margin_history(facts)
    assert h.quarters == 0
    assert h.gross_margins == []
    assert h.dropped_implausible == 1


def test_gross_margin_of_exactly_one_is_kept():
    """A zero-cost quarter is implausible but not mathematically impossible —
    the boundary is inclusive at 1.0."""
    facts = _facts(
        gp=[_q("2024-01-01", "2024-03-31", "2024-05-01", 100.0)],
        rev=[_q("2024-01-01", "2024-03-31", "2024-05-01", 100.0)],
    )
    h = build_margin_history(facts)
    assert h.gross_margins == [1.0]
    assert h.dropped_implausible == 0


def test_gross_margin_below_negative_one_is_dropped_and_counted():
    facts = _facts(
        gp=[_q("2024-01-01", "2024-03-31", "2024-05-01", -250.0)],
        rev=[_q("2024-01-01", "2024-03-31", "2024-05-01", 100.0)],
    )
    h = build_margin_history(facts)
    assert h.quarters == 0
    assert h.dropped_implausible == 1


def test_a_normal_quarter_is_unaffected_by_the_plausibility_check():
    facts = _facts(
        gp=[_q("2024-01-01", "2024-03-31", "2024-05-01", 40.0)],
        rev=[_q("2024-01-01", "2024-03-31", "2024-05-01", 100.0)],
    )
    h = build_margin_history(facts)
    assert h.gross_margins == [0.40]
    assert h.dropped_implausible == 0
