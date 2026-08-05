"""Output limits are enforced in Python, not merely requested in prose."""

from __future__ import annotations

from datetime import UTC, datetime

from app.research.schema import (
    MAX_CASE_ITEMS,
    MAX_POINT_CHARS,
    MAX_SUMMARY_CHARS,
    EntryExitPlan,
    LensView,
    ZoneBand,
)

AS_OF = datetime(2026, 8, 5, tzinfo=UTC)


def _plan(**overrides):
    kwargs = dict(
        ticker="NVDA",
        as_of=AS_OF,
        entry_zone=ZoneBand(low=100.0, high=102.0, method="m"),
        exit_zone_primary=ZoneBand(low=110.0, high=112.0, method="m"),
        invalidation=95.0,
        risk_reward_primary=2.0,
        confidence="medium",
        timeframe="5-15d",
        mode="deep",
        cost_usd=0.05,
        duration_ms=1000,
    )
    kwargs.update(overrides)
    return EntryExitPlan(**kwargs)


def test_case_lists_are_clamped_to_max_items():
    plan = _plan(bull_case=[f"point {i}" for i in range(12)])
    assert len(plan.bull_case) == MAX_CASE_ITEMS


def test_bear_case_and_key_risks_are_clamped_too():
    plan = _plan(
        bear_case=[f"b{i}" for i in range(12)],
        key_risks=[f"r{i}" for i in range(12)],
    )
    assert len(plan.bear_case) == MAX_CASE_ITEMS
    assert len(plan.key_risks) == MAX_CASE_ITEMS


def test_overlong_case_entry_is_truncated_not_rejected():
    plan = _plan(bull_case=["x" * (MAX_POINT_CHARS + 500)])
    assert len(plan.bull_case[0]) <= MAX_POINT_CHARS
    assert plan.bull_case[0].endswith("…")


def test_short_entries_are_left_untouched():
    plan = _plan(bull_case=["a tidy point"])
    assert plan.bull_case == ["a tidy point"]


def test_lens_summary_is_truncated():
    lens = LensView(name="quantitative", conviction="high", summary="y" * 1000)
    assert len(lens.summary) <= MAX_SUMMARY_CHARS


def test_lens_points_are_clamped_and_truncated():
    lens = LensView(
        name="quantitative",
        conviction="high",
        points=[f"p{i}" for i in range(20)] + ["z" * 900],
    )
    assert len(lens.points) <= MAX_CASE_ITEMS
    assert all(len(p) <= MAX_POINT_CHARS for p in lens.points)


def test_bare_string_wrapping_still_works():
    """Regression guard for the 2026-05-28 char-splitting incident."""
    plan = _plan(bull_case="a single bare string")
    assert plan.bull_case == ["a single bare string"]


def test_judge_tool_schema_matches_the_prompt_and_bounds_strings():
    from app.research.agents.judge import judge_tool_input_schema

    props = judge_tool_input_schema()["properties"]
    assert props["bull_case"]["maxItems"] == MAX_CASE_ITEMS
    assert props["bear_case"]["maxItems"] == MAX_CASE_ITEMS
    assert props["key_risks"]["maxItems"] == MAX_CASE_ITEMS
    assert props["entry_rationale"]["maxLength"] == MAX_POINT_CHARS


def test_lens_tool_schema_bounds_summary_and_points():
    from app.research.agents.base import _lens_tool_input_schema

    props = _lens_tool_input_schema()["properties"]
    assert props["summary"]["maxLength"] == MAX_SUMMARY_CHARS
    assert props["points"]["items"]["maxLength"] == MAX_POINT_CHARS
