"""Batch wrapper for `tsr scan --rerank`.

Fans out one rerank panel per ticker via ThreadPoolExecutor. Collects
results in submission order (so the user sees the same row order as the
input). Per-ticker failures degrade gracefully: a ticker whose packet
build fails returns a `ScanRerankResult` with rank='skip' and an error.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from anthropic import Anthropic

from app.db import session_scope
from app.logging import get_logger
from app.research.context import ResearchPacket, gather
from app.research.rerank import run_rerank
from app.research.schema import ScanRerankResult

log = get_logger(__name__)


def build_research_packet(ticker: str) -> ResearchPacket:
    """Top-level wrapper around `context.gather()` with managed session.

    Defined at module top-level (not inlined into `rerank_tickers`) so that
    tests can monkeypatch it without poking inside the threaded callsite.
    """
    with session_scope() as session:
        return gather(ticker=ticker, session=session)


def rerank_tickers(
    tickers: list[str],
    *,
    client: Anthropic | None = None,
    max_workers: int = 4,
) -> list[ScanRerankResult]:
    """Fan out reranks across `tickers`, return in submission order.

    Per-ticker errors are caught and surface as rank='skip' results — one
    bad packet doesn't kill the batch.
    """
    results: dict[str, ScanRerankResult] = {}

    def work(ticker: str) -> ScanRerankResult:
        try:
            packet = build_research_packet(ticker)
        except Exception as e:
            log.warning(
                "research.rerank.packet_build_failed", ticker=ticker, error=str(e)
            )
            return ScanRerankResult(
                ticker=ticker,
                as_of=datetime.now(tz=UTC),
                rank="skip",
                rationale=f"packet build failed: {e}",
                lenses=[],
                cost_usd=0.0,
                duration_ms=0,
                error=str(e),
            )
        return run_rerank(packet, client=client)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(work, t): t for t in tickers}
        for fut in as_completed(futures):
            ticker = futures[fut]
            results[ticker] = fut.result()

    return [results[t] for t in tickers]


__all__ = ["build_research_packet", "rerank_tickers"]
