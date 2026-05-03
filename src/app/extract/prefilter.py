"""Rule-based prefilter: produce candidate windows worth sending to the LLM.

Strategy:
  1. Consolidate a Document's segments into ~25s windows.
  2. For each window, score it for "call-likelihood":
       +1.5 per ticker mention (any method)
       +1.0 if a call-language verb is present
       +0.5 if a price-like number is present
       +0.5 if a directional qualifier is present near a ticker
  3. Keep windows above a minimum score, expanded to ±N seconds of context.

Throws away ~90% of segments before LLM tokens are spent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.normalize.text import (
    ConsolidatedWindow,
    consolidate_segments,
    context_window,
)
from app.normalize.tickers import TickerMention, Universe, detect_tickers


# Verbs / phrases that strongly suggest a trade call is being discussed.
CALL_VERBS_PATTERN = re.compile(
    r"\b("
    r"watching|watch list|watchlist|"
    r"buying|bought|adding to|"
    r"selling|sold|trimming|"
    r"long(?:ing)?|short(?:ing)?|"
    r"calls? on|puts? on|"
    r"breakout|breaking out|breaks out|breaks above|breaks below|"
    r"reclaim(?:s|ing)?|reject(?:s|ing|ed)?|"
    r"bullish|bearish|"
    r"set ?up(?:ping)?|"
    r"target(?:ing)?|stop(?:ped)?|stop ?loss|"
    r"above|below|over|under|"
    r"if it (?:holds|breaks|reclaims|fails)|"
    r"entry|trigger"
    r")\b",
    re.IGNORECASE,
)


# Price-like numbers: integer or decimal, typically $-prefixed or near a ticker.
# Skip very small (<1) and very large (>100000) numbers — usually not prices.
PRICE_PATTERN = re.compile(r"\$?\d{1,5}(?:\.\d{1,2})?")


@dataclass
class CandidateWindow:
    """A consolidated window + its enriched context that's worth LLM extraction."""

    window: ConsolidatedWindow
    context: ConsolidatedWindow
    ticker_mentions: list[TickerMention] = field(default_factory=list)
    call_verb_hits: int = 0
    price_hits: int = 0
    score: float = 0.0


def score_window(
    text: str,
    universe: Universe,
) -> tuple[float, list[TickerMention], int, int]:
    """Score a window for likelihood of containing a trade call.

    Returns (score, ticker_mentions, call_verb_hits, price_hits).
    """
    mentions = detect_tickers(text, universe)
    verb_hits = len(CALL_VERBS_PATTERN.findall(text))
    price_hits = sum(
        1 for m in PRICE_PATTERN.finditer(text) if _looks_like_price(m.group(0))
    )

    score = 0.0
    score += 1.5 * len(mentions)
    score += 1.0 * min(verb_hits, 3)  # cap so a single rant doesn't blow up
    score += 0.5 * min(price_hits, 3)

    # Bonus: a directional verb near a ticker → strong signal.
    if mentions and verb_hits:
        score += 0.5

    return score, mentions, verb_hits, price_hits


def _looks_like_price(token: str) -> bool:
    raw = token.lstrip("$")
    try:
        v = float(raw)
    except ValueError:
        return False
    if v < 1.0 or v > 100000.0:
        return False
    return True


def find_candidate_windows(
    segments: list,  # list of TranscriptSegment-like (uses start/end/text/id)
    universe: Universe,
    *,
    min_score: float = 2.0,
    window_seconds: float = 25.0,
    context_before: float = 90.0,
    context_after: float = 90.0,
    merge_gap_seconds: float = 60.0,
) -> list[CandidateWindow]:
    """Consolidate + score + select + merge adjacent candidate clusters.

    Adjacent qualifying windows (gap ≤ `merge_gap_seconds`) are clustered into a
    single CandidateWindow whose context spans the whole cluster + the configured
    before/after padding. This prevents the LLM from re-extracting the same call
    from overlapping ±N s context windows around a long discussion.
    """
    if not segments:
        return []

    seg_ids = [getattr(s, "id", i) for i, s in enumerate(segments)]
    windows = consolidate_segments(segments, window_seconds=window_seconds, segment_ids=seg_ids)

    # 1. Score every window; keep only those above threshold (with their indices).
    scored: list[tuple[int, float, list, int, int]] = []
    for i, w in enumerate(windows):
        score, mentions, verbs, prices = score_window(w.text, universe)
        if score >= min_score:
            scored.append((i, score, mentions, verbs, prices))
    if not scored:
        return []

    # 2. Cluster scored windows by adjacency. A new cluster starts when the
    #    gap between the previous qualifying window's end and the next one's
    #    start exceeds merge_gap_seconds.
    clusters: list[list[tuple[int, float, list, int, int]]] = []
    current: list[tuple[int, float, list, int, int]] = [scored[0]]
    for item in scored[1:]:
        prev_idx = current[-1][0]
        next_idx = item[0]
        gap = windows[next_idx].start_seconds - windows[prev_idx].end_seconds
        if gap > merge_gap_seconds:
            clusters.append(current)
            current = [item]
        else:
            current.append(item)
    clusters.append(current)

    # 3. Build one CandidateWindow per cluster.
    candidates: list[CandidateWindow] = []
    for cluster in clusters:
        first_idx = cluster[0][0]
        last_idx = cluster[-1][0]
        cluster_score = sum(c[1] for c in cluster)
        cluster_verbs = sum(c[3] for c in cluster)
        cluster_prices = sum(c[4] for c in cluster)

        # Aggregate ticker mentions across the cluster, deduped by (ticker, span).
        seen: set[tuple[str, int]] = set()
        cluster_mentions: list = []
        for _, _, mentions, _, _ in cluster:
            for m in mentions:
                key = (m.ticker, m.span[0])
                if key in seen:
                    continue
                seen.add(key)
                cluster_mentions.append(m)

        # Representative window = the first qualifying one in the cluster (its
        # source_segment_ids will be used for primary_segment_id downstream).
        representative = windows[first_idx]

        # Context spans the cluster + padding.
        cluster_start = windows[first_idx].start_seconds
        cluster_end = windows[last_idx].end_seconds
        lo = cluster_start - context_before
        hi = cluster_end + context_after
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
        from app.normalize.text import ConsolidatedWindow, clean_text

        ctx = ConsolidatedWindow(
            start_seconds=span_start if span_start is not None else cluster_start,
            end_seconds=span_end if span_end is not None else cluster_end,
            text=clean_text(" ".join(parts)),
            source_segment_ids=tuple(ids),
        )

        candidates.append(
            CandidateWindow(
                window=representative,
                context=ctx,
                ticker_mentions=cluster_mentions,
                call_verb_hits=cluster_verbs,
                price_hits=cluster_prices,
                score=cluster_score,
            )
        )

    return candidates
