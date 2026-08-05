"""Extraction orchestrator.

For each Document not yet processed:
  1. Load segments → candidate windows (prefilter).
  2. For each candidate, call LLM extractor (two-pass for calls; single-pass
     for claims per ADR 0006).
  3. Run validators + final confidence (calls and claims independently).
  4. Persist as ExtractedCall and/or Claim rows with status.

Idempotent: skips Documents that already have ExtractedCall OR Claim rows
tagged with the current `extractor_version`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, union_all

from app.config import load_project_settings
from app.db import session_scope
from app.extract.confidence import compute_final_confidence, decide_status
from app.extract.llm_extractor import ExtractionResult, LLMExtractor
from app.extract.prefilter import find_candidate_windows
from app.extract.validator import validate, validate_claim
from app.logging import get_logger
from app.models import (
    CALL_STATUS_REJECTED,
    CLAIM_STATUS_ACCEPTED,
    CLAIM_STATUS_PENDING_REVIEW,
    Claim,
    Creator,
    Document,
    ExtractedCall,
    SourceChannel,
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
    creator_filter: str | None = None,
) -> dict[str, int]:
    """Process pending Documents.

    Returns a summary: {extracted, no_call, rejected, errors, documents}.

    `creator_filter`, when set, restricts to documents whose Creator's
    display_name contains the filter (case-insensitive substring). Useful
    for diversifying corpus coverage across creators instead of processing
    most-recent-first across the whole pool.
    """
    settings = load_project_settings().extraction
    universe = universe or load_universe()
    extractor = extractor or LLMExtractor()

    summary = {
        "documents": 0,
        "extracted": 0,           # accepted ExtractedCall rows persisted
        "claims_extracted": 0,    # accepted Claim rows persisted
        "no_call": 0,
        "rejected": 0,
        "claims_rejected": 0,
        "errors": 0,
    }

    docs = _load_pending_documents(
        document_ids=document_ids,
        extractor_version=settings.extractor_version,
        limit=limit,
        min_segments=min_segments,
        creator_filter=creator_filter,
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
            summary["claims_extracted"] += doc_summary["claims_extracted"]
            summary["no_call"] += doc_summary["no_call"]
            summary["rejected"] += doc_summary["rejected"]
            summary["claims_rejected"] += doc_summary["claims_rejected"]
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
    creator_filter: str | None = None,
) -> list[tuple[int, str | None]]:
    """Documents with at least `min_segments` segments and no prior extracts
    (call OR claim) at this extractor_version. Sorted most-recent-first.

    Note: Documents that produced neither calls nor claims at the current
    version are reprocessed on every run. ADR 0006 §"Consequences" flagged
    this; a `DocumentExtractionRun` marker table is the planned fix.
    """
    with session_scope() as session:
        processed_calls = select(ExtractedCall.document_id).where(
            ExtractedCall.extractor_version == extractor_version
        )
        processed_claims = select(Claim.document_id).where(
            Claim.extractor_version == extractor_version
        )
        processed_subq = union_all(processed_calls, processed_claims).subquery()
        q = (
            select(Document.id, Document.title)
            .join(TranscriptSegment, TranscriptSegment.document_id == Document.id)
            .where(Document.id.notin_(select(processed_subq)))
            .group_by(Document.id, Document.title, Document.posted_at)
            .having(func.count(TranscriptSegment.id) >= min_segments)
        )
        if document_ids is not None:
            q = q.where(Document.id.in_(document_ids))
        if creator_filter is not None:
            q = (
                q.join(SourceChannel, SourceChannel.id == Document.source_channel_id)
                 .join(Creator, Creator.id == SourceChannel.creator_id)
                 .where(Creator.display_name.ilike(f"%{creator_filter}%"))
            )
        q = q.order_by(Document.posted_at.desc())
        if limit:
            q = q.limit(limit)
        rows = session.execute(q).all()
        return [(r[0], r[1]) for r in rows]


def run_extraction_batched(
    *,
    document_ids: list[int] | None = None,
    extractor: LLMExtractor | None = None,
    universe: Universe | None = None,
    limit: int | None = None,
    min_segments: int = 1,
    use_two_pass: bool = True,
    creator_filter: str | None = None,
    batch_runner=None,
) -> dict[str, int]:
    """Same extraction, submitted through the Batches API at half the price.

    Identical model, prompts, tools, validator, confidence scoring and
    persistence — only the transport differs. Suited to the backlog (thousands
    of independent windows, no latency requirement); the per-document path
    above stays the right choice for the daily cron's handful of documents,
    where waiting on a batch to land would be silly.

    Structure: prefilter every document locally (free), submit all pass-1
    windows as one batch, then — for two-pass runs — submit the windows that
    produced a call as a second batch, since pass 2's prompt embeds pass 1's
    output. Persistence happens once, after both rounds.

    `batch_runner` is injectable for tests; it defaults to the real
    `app.extract.batch.run_batch`.
    """
    from app.extract.batch import run_batch

    settings = load_project_settings().extraction
    universe = universe or load_universe()
    extractor = extractor or LLMExtractor()
    batch_runner = batch_runner or run_batch

    summary = {
        "documents": 0,
        "extracted": 0,
        "claims_extracted": 0,
        "no_call": 0,
        "rejected": 0,
        "claims_rejected": 0,
        "errors": 0,
    }

    docs = _load_pending_documents(
        document_ids=document_ids,
        extractor_version=settings.extractor_version,
        limit=limit,
        min_segments=min_segments,
        creator_filter=creator_filter,
    )

    # --- Phase 1: build every candidate window locally. No spend yet. ---
    pending: dict[str, tuple[int, Any]] = {}
    for doc_id, doc_title in docs:
        summary["documents"] += 1
        try:
            for idx, cand in enumerate(_candidates_for_document(doc_id, universe)):
                pending[f"d{doc_id}-c{idx}"] = (doc_id, cand)
        except Exception as e:  # pragma: no cover — defensive, mirrors sync path
            summary["errors"] += 1
            log.warning(
                "extract.doc.error", doc_id=doc_id, title=doc_title, error=str(e)
            )

    log.info("extract.batch.run.start", n_docs=len(docs), n_windows=len(pending))
    if not pending:
        log.info("extract.run.done", **summary)
        return summary

    # --- Phase 2: pass 1 for every window. ---
    first = batch_runner(
        extractor,
        {
            cid: extractor.build_extract_params(cand.context.text)
            for cid, (_, cand) in pending.items()
        },
        label="pass1",
    )
    summary["errors"] += first.n_failed

    results = dict(first.results)

    # --- Phase 3: pass 2 re-adjudicates only the windows that found a call. ---
    if use_two_pass:
        second_params = {
            cid: extractor.build_validation_params(
                pending[cid][1].context.text,
                result.call.model_dump_json(indent=2),
            )
            for cid, result in first.results.items()
            if result.call is not None
        }
        if second_params:
            second = batch_runner(extractor, second_params, label="pass2")
            summary["errors"] += second.n_failed
            for cid, revalidated in second.results.items():
                # Preserve pass-1 claims; pass 2 only adjudicates the call —
                # same contract as LLMExtractor.extract_with_validation.
                results[cid] = ExtractionResult(
                    call=revalidated.call,
                    claims=first.results[cid].claims,
                    no_call=revalidated.no_call if revalidated.call is None else None,
                    raw_response=revalidated.raw_response,
                )

    # --- Phase 4: validate + persist, exactly as the synchronous path does. ---
    for cid, result in results.items():
        doc_id, cand = pending[cid]
        _persist_window_result(
            result=result,
            cand=cand,
            doc_id=doc_id,
            extractor_version=settings.extractor_version,
            universe=universe,
            summary=summary,
        )

    log.info("extract.run.done", **summary)
    return summary


def _candidates_for_document(doc_id: int, universe: Universe) -> list[Any]:
    """Load a document's segments and prefilter them into candidate windows."""
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
        return []
    return list(find_candidate_windows(segments, universe))


def _persist_window_result(
    *,
    result: ExtractionResult,
    cand: Any,
    doc_id: int,
    extractor_version: str,
    universe: Universe,
    summary: dict[str, int],
) -> None:
    """Validate + persist one window's result. Shared by both transports."""
    text = cand.context.text
    primary_segment_id = (
        cand.window.source_segment_ids[0] if cand.window.source_segment_ids else None
    )
    _persist_call(
        result_call=result.call,
        text=text,
        cand=cand,
        doc_id=doc_id,
        primary_segment_id=primary_segment_id,
        extractor_version=extractor_version,
        universe=universe,
        summary=summary,
    )
    _persist_claims(
        claims=result.claims,
        text=text,
        cand=cand,
        doc_id=doc_id,
        primary_segment_id=primary_segment_id,
        extractor_version=extractor_version,
        universe=universe,
        summary=summary,
    )
    if result.no_call is not None and not result.has_claims and result.call is None:
        summary["no_call"] += 1


def _process_document(
    *,
    doc_id: int,
    extractor: LLMExtractor,
    universe: Universe,
    extractor_version: str,
    use_two_pass: bool,
) -> dict[str, int]:
    summary = {
        "extracted": 0,
        "claims_extracted": 0,
        "no_call": 0,
        "rejected": 0,
        "claims_rejected": 0,
    }

    for cand in _candidates_for_document(doc_id, universe):
        text = cand.context.text
        if use_two_pass:
            result = extractor.extract_with_validation(text)
        else:
            result = extractor.extract(text)

        # Claims and calls are independent: a window may produce one, the
        # other, both, or neither. `_persist_window_result` handles both, and
        # is shared with the batched transport so the two cannot drift.
        _persist_window_result(
            result=result,
            cand=cand,
            doc_id=doc_id,
            extractor_version=extractor_version,
            universe=universe,
            summary=summary,
        )

    return summary


def _persist_call(
    *,
    result_call,
    text: str,
    cand,
    doc_id: int,
    primary_segment_id: int | None,
    extractor_version: str,
    universe: Universe,
    summary: dict[str, int],
) -> None:
    if result_call is None:
        return

    validation = validate(result_call, source_text=text, universe=universe)
    if validation.accepted_call is None:
        summary["rejected"] += 1
        return

    final_call = validation.accepted_call
    final_conf = compute_final_confidence(final_call, validation)
    status = decide_status(final_conf, validation)

    if status == CALL_STATUS_REJECTED:
        summary["rejected"] += 1
        return

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


def _persist_claims(
    *,
    claims,
    text: str,
    cand,
    doc_id: int,
    primary_segment_id: int | None,
    extractor_version: str,
    universe: Universe,
    summary: dict[str, int],
) -> None:
    """Validate and persist each claim independently. One bad claim does not
    discard the rest."""
    if not claims:
        return

    accepted_rows: list[Claim] = []
    for raw_claim in claims:
        cv = validate_claim(raw_claim, source_text=text, universe=universe)
        if cv.accepted_claim is None:
            summary["claims_rejected"] += 1
            continue

        ac = cv.accepted_claim
        # Final confidence: harmonic-style blend of LLM + rule. Same shape as
        # call confidence but simpler — claim has only one self-reported number.
        final_conf = (ac.overall_confidence + cv.rule_confidence) / 2.0
        status = (
            CLAIM_STATUS_ACCEPTED
            if final_conf >= 0.5 and not cv.failures
            else CLAIM_STATUS_PENDING_REVIEW
        )

        accepted_rows.append(
            Claim(
                document_id=doc_id,
                primary_segment_id=primary_segment_id,
                claim_type=ac.claim_type,
                ticker=ac.ticker,
                sector=ac.sector,
                polarity=ac.polarity,
                claim_class=ac.claim_class,
                summary=ac.summary,
                evidence_quote=ac.evidence_quote,
                context_text=text,
                context_start_seconds=cand.context.start_seconds,
                context_end_seconds=cand.context.end_seconds,
                extracted_at=datetime.now(tz=UTC),
                extractor_version=extractor_version,
                llm_confidence=ac.overall_confidence,
                rule_confidence=cv.rule_confidence,
                final_confidence=final_conf,
                status=status,
                validator_notes=(
                    "; ".join(cv.warnings) if cv.warnings else None
                ),
            )
        )

    if accepted_rows:
        with session_scope() as session:
            session.add_all(accepted_rows)
        summary["claims_extracted"] += len(accepted_rows)
