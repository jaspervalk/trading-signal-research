"""Creator coverage — additive bonus column, never a penalty.

The whole point of the screener is to surface tickers our YouTube/Twitter
universe hasn't found yet. A ticker with zero coverage must NOT be ranked
lower than one with coverage on otherwise-identical metrics.

This module is read-only: it queries the existing ORM tables (Claim,
ExtractedCall) for "have we ever heard about this ticker?" and returns
small `(n_mentions, avg_confidence)` tuples that the pipeline attaches
to each row.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models import (
    CALL_STATUS_ACCEPTED,
    CLAIM_STATUS_ACCEPTED,
    Claim,
    ExtractedCall,
)

log = get_logger(__name__)


def coverage_for(
    tickers: Iterable[str],
    *,
    session: Session,
) -> dict[str, tuple[int, float | None]]:
    """Return {ticker: (n_mentions, avg_confidence_or_None)}.

    A ticker not present in the dict (or with `n_mentions == 0`) means
    zero coverage — that is **not** a negative signal; the caller must
    treat it as missing data, never as a penalty.
    """
    tickers_up = sorted({t.upper() for t in tickers if t})
    if not tickers_up:
        return {}

    out: dict[str, tuple[int, float | None]] = {t: (0, None) for t in tickers_up}

    # Accepted ExtractedCalls per ticker
    call_rows = session.execute(
        select(
            ExtractedCall.ticker,
            func.count(ExtractedCall.id),
            func.avg(ExtractedCall.final_confidence),
        )
        .where(ExtractedCall.ticker.in_(tickers_up))
        .where(ExtractedCall.status == CALL_STATUS_ACCEPTED)
        .group_by(ExtractedCall.ticker)
    ).all()

    # Accepted Claims per ticker
    claim_rows = session.execute(
        select(
            Claim.ticker,
            func.count(Claim.id),
            func.avg(Claim.final_confidence),
        )
        .where(Claim.ticker.in_(tickers_up))
        .where(Claim.status == CLAIM_STATUS_ACCEPTED)
        .group_by(Claim.ticker)
    ).all()

    by_ticker: dict[str, list[tuple[int, float | None]]] = {t: [] for t in tickers_up}
    for tkr, n, avg in call_rows:
        if tkr in by_ticker:
            by_ticker[tkr].append((int(n or 0), float(avg) if avg is not None else None))
    for tkr, n, avg in claim_rows:
        if tkr in by_ticker:
            by_ticker[tkr].append((int(n or 0), float(avg) if avg is not None else None))

    for t, pieces in by_ticker.items():
        total = sum(n for n, _ in pieces)
        confs = [(n, c) for n, c in pieces if c is not None]
        weighted = (
            sum(n * c for n, c in confs) / sum(n for n, _ in confs)
            if confs and sum(n for n, _ in confs) > 0
            else None
        )
        out[t] = (total, weighted)
    return out


__all__ = ["coverage_for"]
