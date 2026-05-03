from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Annotation
from apps.api.app.deps import db_session
from apps.api.app.schemas import AnnotationIn, AnnotationOut, AnnotationUpdate

router = APIRouter(prefix="/annotations", tags=["annotations"])


@router.get("", response_model=list[AnnotationOut])
def list_annotations(
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    session: Session = Depends(db_session),
) -> list[Annotation]:
    return list(
        session.execute(
            select(Annotation)
            .where(Annotation.entity_type == entity_type)
            .where(Annotation.entity_id == entity_id)
            .order_by(Annotation.created_at.desc())
        ).scalars().all()
    )


@router.post("", response_model=AnnotationOut, status_code=201)
def create_annotation(
    payload: AnnotationIn,
    session: Session = Depends(db_session),
) -> Annotation:
    a = Annotation(
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        body=payload.body,
        author=payload.author,
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


@router.patch("/{annotation_id}", response_model=AnnotationOut)
def update_annotation(
    annotation_id: int,
    payload: AnnotationUpdate,
    session: Session = Depends(db_session),
) -> Annotation:
    a = session.get(Annotation, annotation_id)
    if a is None:
        raise HTTPException(404, "annotation not found")
    a.body = payload.body
    a.updated_at = datetime.now(tz=UTC)
    session.commit()
    session.refresh(a)
    return a


@router.delete("/{annotation_id}", status_code=204)
def delete_annotation(
    annotation_id: int,
    session: Session = Depends(db_session),
) -> None:
    a = session.get(Annotation, annotation_id)
    if a is None:
        raise HTTPException(404, "annotation not found")
    session.delete(a)
    session.commit()
