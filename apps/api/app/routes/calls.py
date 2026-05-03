from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Creator,
    Document,
    ExtractedCall,
    OutcomeWindow,
    SourceChannel,
)
from apps.api.app.deps import db_session
from apps.api.app.schemas import (
    CallOut,
    CallsPage,
    CallWithContext,
    OutcomeOut,
)

router = APIRouter(prefix="/calls", tags=["calls"])


def _attach_context(
    session: Session, calls: list[ExtractedCall]
) -> list[CallWithContext]:
    if not calls:
        return []
    doc_ids = {c.document_id for c in calls}
    call_ids = [c.id for c in calls]

    doc_rows = session.execute(
        select(
            Document.id, Document.title, Document.url, Document.posted_at,
            Creator.id.label("creator_id"), Creator.display_name.label("creator_name"),
        )
        .select_from(Document)
        .join(SourceChannel, SourceChannel.id == Document.source_channel_id)
        .join(Creator, Creator.id == SourceChannel.creator_id)
        .where(Document.id.in_(doc_ids))
    ).all()
    doc_map = {r.id: r for r in doc_rows}

    outcomes = session.execute(
        select(OutcomeWindow).where(OutcomeWindow.call_id.in_(call_ids))
    ).scalars().all()
    outcomes_by_call: dict[int, list[OutcomeWindow]] = {}
    for o in outcomes:
        outcomes_by_call.setdefault(o.call_id, []).append(o)

    out: list[CallWithContext] = []
    for c in calls:
        ctx = doc_map.get(c.document_id)
        out.append(
            CallWithContext(
                call=CallOut.model_validate(c),
                creator_id=ctx.creator_id if ctx else None,
                creator_name=ctx.creator_name if ctx else None,
                document_title=ctx.title if ctx else None,
                document_url=ctx.url if ctx else None,
                posted_at=ctx.posted_at if ctx else c.extracted_at,
                outcomes=[
                    OutcomeOut.model_validate(o)
                    for o in sorted(outcomes_by_call.get(c.id, []), key=lambda o: o.horizon)
                ],
            )
        )
    return out


@router.get("", response_model=CallsPage)
def list_calls(
    creator_id: int | None = None,
    ticker: str | None = None,
    direction: str | None = None,
    status: str | None = None,
    manual_status: str | None = None,
    min_confidence: float | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    order: str = Query("recent", description="'recent' | 'confidence' | 'return'"),
    session: Session = Depends(db_session),
) -> CallsPage:
    base = select(ExtractedCall).select_from(ExtractedCall).join(
        Document, Document.id == ExtractedCall.document_id
    ).join(SourceChannel, SourceChannel.id == Document.source_channel_id)

    if creator_id is not None:
        base = base.where(SourceChannel.creator_id == creator_id)
    if ticker:
        base = base.where(ExtractedCall.ticker == ticker.upper())
    if direction:
        base = base.where(ExtractedCall.direction == direction)
    if status:
        base = base.where(ExtractedCall.status == status)
    if manual_status:
        base = base.where(ExtractedCall.manual_status == manual_status)
    if min_confidence is not None:
        base = base.where(ExtractedCall.final_confidence >= min_confidence)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    if order == "confidence":
        base = base.order_by(ExtractedCall.final_confidence.desc())
    elif order == "return":
        # Order by best 5d return where available; tied by recency.
        base = base.order_by(Document.posted_at.desc())
    else:
        base = base.order_by(Document.posted_at.desc())

    base = base.limit(limit).offset(offset)
    calls = list(session.execute(base).scalars().all())
    return CallsPage(
        items=_attach_context(session, calls),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{call_id}", response_model=CallWithContext)
def get_call(call_id: int, session: Session = Depends(db_session)) -> CallWithContext:
    call = session.get(ExtractedCall, call_id)
    if call is None:
        raise HTTPException(404, "call not found")
    return _attach_context(session, [call])[0]
