"""Extraction orchestrator.

For each Document not yet processed:
  1. Load segments → candidate windows (prefilter).
  2. For each candidate, call LLM extractor (two-pass).
  3. Run validator + final confidence.
  4. Persist as ExtractedCall rows with status.

Idempotent: skips Documents that already have ExtractedCall rows tagged with
the current `extractor_version`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.config import load_project_settings
from app.db import session_scope
from app.extract.confidence import compute_final_confidence, decide_status
from app.extract.llm_extractor import LLMExtractor
from app.extract.prefilter import find_candidate_windows
from app.extract.validator import validate
from app.logging import get_logger
from app.models import (
    CALL_STATUS_REJECTED,
    Document,
    ExtractedCall,
    TranscriptSegment,
)
from app.normalize.tickers import Universe, load_universe

log = get_logger(__name__)


def run_extraction(
    *,
    document_ids: list[int] | None = None,
    extractor: LLMExtractor | None = None,
    universe: Universe | None = None,
    limit: int | None = None,
    min_segments: int = 1,
    use_two_pass: bool = True,
) -> dict[str, int]:
    """Process pending Documents.

    Returns a summary: {extracted, no_call, rejected, errors, documents}.
    """
    settings = load_project_settings().extraction
    universe = universe or load_universe()
    extractor = extractor or LLMExtractor()

    summary = {
        "documents": 0,
        "extracted": 0,
        "no_call": 0,
        "rejected": 0,
        "errors": 0,
    }

    docs = _load_pending_documents(
        document_ids=document_ids,
        extractor_version=settings.extractor_version,
        limit=limit,
        min_segments=min_segments,
    )
    log.info("extract.run.start", n_docs=len(docs))

    for doc_id, doc_title in docs:
        summary["documents"] += 1
        try:
            doc_summary = _process_document(
                doc_id=doc_id,
                extractor=extractor,
                universe=universe,
                extractor_version=settings.extractor_version,
                use_two_pass=use_two_pass,
            )
            summary["extracted"] += doc_summary["extracted"]
            summary["no_call"] += doc_summary["no_call"]
            summary["rejected"] += doc_summary["rejected"]
            log.info("extract.doc.done", doc_id=doc_id, **doc_summary)
        except Exception as e:  # pragma: no cover
            summary["errors"] += 1
            log.warning("extract.doc.error", doc_id=doc_id, title=doc_title, error=str(e))

    log.info("extract.run.done", **summary)
    return summary


def _load_pending_documents(
    *,
    document_ids: list[int] | None,
    extractor_version: str,
    limit: int | None,
    min_segments: int = 1,
) -> list[tuple[int, str | None]]:
    """Documents with at least `min_segments` segments and no prior extracts
    at this extractor_version. Sorted most-recent-first."""
    with session_scope() as session:
        processed_subq = (
            select(ExtractedCall.document_id)
            .where(ExtractedCall.extractor_version == extractor_version)
            .distinct()
            .subquery()
        )
        q = (
            select(Document.id, Document.title)
            .join(TranscriptSegment, TranscriptSegment.document_id == Document.id)
            .where(Document.id.notin_(select(processed_subq)))
            .group_by(Document.id, Document.title, Document.posted_at)
            .having(func.count(TranscriptSegment.id) >= min_segments)
        )
        if document_ids is not None:
            q = q.where(Document.id.in_(document_ids))
        q = q.order_by(Document.posted_at.desc())
        if limit:
            q = q.limit(limit)
        rows = session.execute(q).all()
        return [(r[0], r[1]) for r in rows]


def _process_document(
    *,
    doc_id: int,
    extractor: LLMExtractor,
    universe: Universe,
    extractor_version: str,
    use_two_pass: bool,
) -> dict[str, int]:
    summary = {"extracted": 0, "no_call": 0, "rejected": 0}

    with session_scope() as session:
        segments = (
            session.execute(
                select(TranscriptSegment)
                .where(TranscriptSegment.document_id == doc_id)
                .order_by(TranscriptSegment.start_seconds)
            )
            .scalars()
            .all()
        )

    if not segments:
        return summary

    candidates = find_candidate_windows(segments, universe)

    for cand in candidates:
        text = cand.context.text
        if use_two_pass:
            result = extractor.extract_with_validation(text)
        else:
            result = extractor.extract(text)

        if result.no_call is not None:
            summary["no_call"] += 1
            continue

        if result.call is None:
            summary["rejected"] += 1
            continue

        validation = validate(result.call, source_text=text, universe=universe)
        if validation.accepted_call is None:
            summary["rejected"] += 1
            continue

        final_call = validation.accepted_call
        final_conf = compute_final_confidence(final_call, validation)
        status = decide_status(final_conf, validation)

        if status == CALL_STATUS_REJECTED:
            summary["rejected"] += 1
            continue

        primary_segment_id = (
            cand.window.source_segment_ids[0] if cand.window.source_segment_ids else None
        )

        with session_scope() as session:
            session.add(
                ExtractedCall(
                    document_id=doc_id,
                    primary_segment_id=primary_segment_id,
                    ticker=final_call.ticker,
                    direction=final_call.direction,
                    entry_type=final_call.entry_type,
                    entry_price=final_call.entry_price,
                    target_price=final_call.target_price,
                    stop_price=final_call.stop_price,
                    timeframe=final_call.timeframe,
                    reasoning_summary=final_call.reasoning_summary,
                    evidence_quote=final_call.ticker_evidence,
                    context_text=text,
                    context_start_seconds=cand.context.start_seconds,
                    context_end_seconds=cand.context.end_seconds,
                    extracted_at=datetime.now(tz=UTC),
                    extractor_version=extractor_version,
                    llm_confidence=final_call.overall_confidence,
                    rule_confidence=validation.rule_confidence,
                    final_confidence=final_conf,
                    status=status,
                    validator_notes=(
                        "; ".join(validation.warnings) if validation.warnings else None
                    ),
                )
            )
        summary["extracted"] += 1

    return summary
