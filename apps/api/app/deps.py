"""FastAPI dependencies — primarily a SQLAlchemy session per request."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.db import get_session_factory


def db_session() -> Iterator[Session]:
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
