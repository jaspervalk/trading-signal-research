"""Tests for app.normalize.text — segment consolidation + cleaning."""

from __future__ import annotations

from dataclasses import dataclass

from app.normalize.text import (
    clean_text,
    consolidate_segments,
    context_window,
)


@dataclass
class Seg:
    start_seconds: float
    end_seconds: float
    text: str


def test_clean_text_basic():
    assert clean_text("  hello\n\tworld  ") == "hello world"
    assert clean_text("foo\xa0bar") == "foo bar"
    assert clean_text("") == ""


def test_consolidate_simple():
    segs = [
        Seg(0.0, 2.0, "watching"),
        Seg(2.1, 4.0, "NVDA above"),
        Seg(4.0, 6.0, "920"),
    ]
    out = consolidate_segments(segs, window_seconds=25.0)
    assert len(out) == 1
    assert "NVDA above" in out[0].text
    assert out[0].start_seconds == 0.0
    assert out[0].end_seconds == 6.0


def test_consolidate_breaks_on_window_seconds():
    segs = [Seg(i * 5.0, i * 5.0 + 4.0, f"chunk {i}") for i in range(10)]
    out = consolidate_segments(segs, window_seconds=15.0)
    # Each window has at most 4 segments (4 * 5s = 20s; first new segment that
    # would push past 15s starts a new window).
    assert len(out) >= 3
    for w in out:
        assert (w.end_seconds - w.start_seconds) <= 25.0


def test_consolidate_breaks_on_long_gap():
    segs = [
        Seg(0.0, 2.0, "first"),
        Seg(2.5, 4.5, "still first"),
        Seg(20.0, 22.0, "new topic"),  # gap > 3s default
    ]
    out = consolidate_segments(segs, window_seconds=60.0, min_gap_break_seconds=3.0)
    assert len(out) == 2
    assert out[0].text.startswith("first")
    assert out[1].text == "new topic"


def test_consolidate_empty():
    assert consolidate_segments([]) == []


def test_consolidate_skips_blank_text():
    segs = [Seg(0.0, 1.0, "   "), Seg(1.0, 2.0, "real")]
    out = consolidate_segments(segs)
    assert len(out) == 1
    assert out[0].text == "real"


def test_consolidate_records_source_ids():
    segs = [Seg(0.0, 1.0, "a"), Seg(1.0, 2.0, "b")]
    out = consolidate_segments(segs, segment_ids=[42, 43])
    assert out[0].source_segment_ids == (42, 43)


def test_context_window_includes_neighbors():
    segs = [
        Seg(0.0, 5.0, "one"),
        Seg(5.0, 10.0, "two"),
        Seg(10.0, 15.0, "three"),
        Seg(15.0, 20.0, "four"),
    ]
    windows = consolidate_segments(segs, window_seconds=4.5)
    # 4 windows of one segment each.
    assert len(windows) == 4
    cw = context_window(windows, target_index=2, seconds_before=10.0, seconds_after=10.0)
    assert "two" in cw.text
    assert "three" in cw.text
    assert "four" in cw.text
