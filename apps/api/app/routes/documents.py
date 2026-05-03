from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Creator,
    Document,
    SourceChannel,
    TranscriptSegment,
)
from apps.api.app.deps import db_session
from apps.api.app.schemas import DocumentOut, TranscriptSegmentOut

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, session: Session = Depends(db_session)) -> DocumentOut:
    row = session.execute(
        select(Document, Creator)
        .select_from(Document)
        .join(SourceChannel, SourceChannel.id == Document.source_channel_id)
        .join(Creator, Creator.id == SourceChannel.creator_id)
        .where(Document.id == document_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "document not found")
    doc, creator = row
    return DocumentOut(
        id=doc.id,
        source_type=doc.source_type,
        external_id=doc.external_id,
        title=doc.title,
        description=doc.description,
        posted_at=doc.posted_at,
        url=doc.url,
        duration_seconds=doc.duration_seconds,
        creator_id=creator.id,
        creator_name=creator.display_name,
    )


@router.get("/{document_id}/segments", response_model=list[TranscriptSegmentOut])
def list_segments(
    document_id: int,
    session: Session = Depends(db_session),
) -> list[TranscriptSegment]:
    return list(
        session.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.document_id == document_id)
            .order_by(TranscriptSegment.start_seconds)
        ).scalars().all()
    )
