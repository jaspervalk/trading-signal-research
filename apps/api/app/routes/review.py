from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models import (
    ExtractedCall,
    MANUAL_STATUS_CONFIRMED,
    MANUAL_STATUS_FLAGGED,
    MANUAL_STATUS_REJECTED,
    MANUAL_STATUS_UNREVIEWED,
)
from apps.api.app.deps import db_session
from apps.api.app.schemas import CallOut, ReviewIn

router = APIRouter(prefix="/review", tags=["review"])


VALID = {
    MANUAL_STATUS_UNREVIEWED,
    MANUAL_STATUS_CONFIRMED,
    MANUAL_STATUS_REJECTED,
    MANUAL_STATUS_FLAGGED,
}


@router.post("/calls/{call_id}", response_model=CallOut)
def review_call(
    call_id: int,
    payload: ReviewIn,
    session: Session = Depends(db_session),
) -> ExtractedCall:
    if payload.manual_status not in VALID:
        raise HTTPException(
            400, f"manual_status must be one of {sorted(VALID)}"
        )
    call = session.get(ExtractedCall, call_id)
    if call is None:
        raise HTTPException(404, "call not found")
    call.manual_status = payload.manual_status
    if payload.manual_notes is not None:
        call.manual_notes = payload.manual_notes
    if payload.manual_status == MANUAL_STATUS_UNREVIEWED:
        call.manual_reviewed_at = None
    else:
        call.manual_reviewed_at = datetime.now(tz=UTC)
    session.commit()
    session.refresh(call)
    return call
