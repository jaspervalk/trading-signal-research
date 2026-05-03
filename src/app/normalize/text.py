"""Text cleaning + segment consolidation.

YouTube auto-transcripts arrive in tiny ~2-second chunks. The LLM extractor
needs longer context windows (~15-30s). We consolidate on the fly without
mutating the source `TranscriptSegment` rows — raw stays raw in the DB.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


class _SegmentLike(Protocol):
    """Anything with start/end seconds and text. ORM rows or dicts both work."""

    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class ConsolidatedWindow:
    """A merged transcript window with provenance back to source segment ids."""

    start_seconds: float
    end_seconds: float
    text: str
    source_segment_ids: tuple[int, ...]


_WHITESPACE_RUN = re.compile(r"\s+")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_text(text: str) -> str:
    """Basic cleanup: NBSP → space, drop control chars, collapse whitespace."""
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = _CONTROL_CHARS.sub("", text)
    text = _WHITESPACE_RUN.sub(" ", text)
    return text.strip()


def consolidate_segments(
    segments: list[_SegmentLike],
    *,
    window_seconds: float = 25.0,
    max_chars: int = 1500,
    min_gap_break_seconds: float = 3.0,
    segment_ids: list[int] | None = None,
) -> list[ConsolidatedWindow]:
    """Merge consecutive small segments into larger windows.

    Window boundary triggers (any one):
      - Window duration would exceed `window_seconds`.
      - Concatenated text would exceed `max_chars`.
      - Gap between consecutive segments exceeds `min_gap_break_seconds`
        (a natural pause, often signalling topic change).

    `segment_ids` is an optional parallel list of DB ids. If None, source ids
    are recorded as the segment index.
    """
    if not segments:
        return []
    if segment_ids is None:
        segment_ids = list(range(len(segments)))
    if len(segment_ids) != len(segments):
        raise ValueError("segment_ids length must match segments length")

    windows: list[ConsolidatedWindow] = []
    cur_text_parts: list[str] = []
    cur_ids: list[int] = []
    cur_start: float | None = None
    cur_end: float | None = None
    last_seg_end: float | None = None

    def _flush() -> None:
        if cur_start is None or cur_end is None or not cur_text_parts:
            return
        joined = clean_text(" ".join(cur_text_parts))
        if not joined:
            return
        windows.append(
            ConsolidatedWindow(
                start_seconds=cur_start,
                end_seconds=cur_end,
                text=joined,
                source_segment_ids=tuple(cur_ids),
            )
        )

    for seg, sid in zip(segments, segment_ids, strict=True):
        text = clean_text(seg.text)
        if not text:
            continue

        if cur_start is None:
            cur_start = seg.start_seconds
            cur_end = seg.end_seconds
            cur_text_parts = [text]
            cur_ids = [sid]
            last_seg_end = seg.end_seconds
            continue

        gap = seg.start_seconds - (last_seg_end or seg.start_seconds)
        prospective_dur = seg.end_seconds - cur_start
        prospective_chars = sum(len(p) + 1 for p in cur_text_parts) + len(text)

        should_flush = (
            prospective_dur > window_seconds
            or prospective_chars > max_chars
            or gap > min_gap_break_seconds
        )

        if should_flush:
            _flush()
            cur_start = seg.start_seconds
            cur_end = seg.end_seconds
            cur_text_parts = [text]
            cur_ids = [sid]
        else:
            cur_end = seg.end_seconds
            cur_text_parts.append(text)
            cur_ids.append(sid)

        last_seg_end = seg.end_seconds

    _flush()
    return windows


def context_window(
    windows: list[ConsolidatedWindow],
    *,
    target_index: int,
    seconds_before: float = 90.0,
    seconds_after: float = 90.0,
) -> ConsolidatedWindow:
    """Build a wider context window around a target window for LLM input.

    Returns a synthetic ConsolidatedWindow spanning [target.start - before,
    target.end + after] seconds, concatenating text from all windows that
    overlap that range.
    """
    if not windows:
        raise ValueError("no windows")
    target = windows[target_index]
    lo = target.start_seconds - seconds_before
    hi = target.end_seconds + seconds_after

    parts: list[str] = []
    ids: list[int] = []
    span_start: float | None = None
    span_end: float | None = None
    for w in windows:
        if w.end_seconds < lo or w.start_seconds > hi:
            continue
        parts.append(w.text)
        ids.extend(w.source_segment_ids)
        span_start = w.start_seconds if span_start is None else min(span_start, w.start_seconds)
        span_end = w.end_seconds if span_end is None else max(span_end, w.end_seconds)

    return ConsolidatedWindow(
        start_seconds=span_start if span_start is not None else target.start_seconds,
        end_seconds=span_end if span_end is not None else target.end_seconds,
        text=clean_text(" ".join(parts)),
        source_segment_ids=tuple(ids),
    )
