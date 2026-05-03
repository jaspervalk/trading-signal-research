"""Test fixtures: in-memory SQLite for fast, isolated unit tests."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Force in-memory DB before app modules load.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


@pytest.fixture
def engine():  # type: ignore[no-untyped-def]
    """In-memory SQLite engine usable across threads (FastAPI TestClient).

    StaticPool keeps a single connection alive so all threads share the same
    in-memory database; check_same_thread=False permits cross-thread access.
    """
    from app import models

    eng = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine) -> Iterator[Session]:  # type: ignore[no-untyped-def]
    SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
