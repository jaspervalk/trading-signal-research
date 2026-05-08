"""FastAPI application for the research dashboard.

Read-only views over the existing SQLAlchemy models, plus write endpoints
for the research-workbench actions: annotations, tags, watchlists,
manual review, gold-set labels.

Mounted as a separate app under apps/api/ so the core CLI pipeline stays
free of HTTP concerns.

Run locally:
    uvicorn apps.api.app.main:app --reload --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.logging import configure_logging, get_logger
from apps.api.app.routes import (
    annotations,
    calls,
    creators,
    documents,
    gold,
    health,
    leaderboard,
    review,
    tags,
    tickers,
    watchlist,
)

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
    configure_logging()
    log.info("api.startup")
    yield
    log.info("api.shutdown")


app = FastAPI(
    title="trading-signal-research API",
    version="0.1.0",
    description="Research workbench backend. Read views over the pipeline + write endpoints for review/annotate/tag/watchlist/gold-set.",
    lifespan=lifespan,
)

# CORS — Next.js dev server runs on :3000 by default; :3001 is the fallback when
# :3000 is occupied by another local project (parallel Healthcare-Policy-Copilot).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Mount routers. Group by entity for clarity in the OpenAPI doc.
app.include_router(health.router)
app.include_router(creators.router)
app.include_router(leaderboard.router)
app.include_router(calls.router)
app.include_router(documents.router)
app.include_router(tickers.router)
from apps.api.app.routes import research  # noqa: E402

app.include_router(research.router)
app.include_router(annotations.router)
app.include_router(tags.router)
app.include_router(watchlist.router)
app.include_router(review.router)
app.include_router(gold.router)
