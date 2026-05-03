"""Compute CreatorScorecard rows from OutcomeWindow data.

For each (creator, window_label, horizon) combination we aggregate:
  - call counts (total + activated + unique tickers)
  - activation rate
  - hit rate with Wilson CI (conditional on activation)
  - mean / median / std of conditional returns
  - unconditional expectancy (treating non-activated as 0%)
  - sharpe-like ratio
  - mean / worst MAE (drawdown proxy)
  - excess-vs-SPY mean / median / hit rate
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.config import load_project_settings
from app.db import session_scope
from app.logging import get_logger
from app.models import (
    Creator,
    CreatorScorecard,
    Document,
    ExtractedCall,
    OUTCOME_STATUS_EVALUATED,
    OUTCOME_STATUS_NOT_TRIGGERED,
    OutcomeWindow,
    SourceChannel,
)
from app.scoring.metrics import (
    expectancy,
    hit_rate_with_ci,
    median,
    sharpe_like,
    std,
)

log = get_logger(__name__)

EVALUATOR_VERSION = "v0.1"


def _window_cutoff(window_label: str, *, now: datetime | None = None) -> datetime | None:
    if window_label == "all":
        return None
    now = now or datetime.now(tz=UTC)
    if window_label.endswith("d"):
        days = int(window_label[:-1])
        return now - timedelta(days=days)
    raise ValueError(f"unsupported window_label: {window_label!r}")


def compute_scorecards(
    *,
    creator_ids: list[int] | None = None,
    horizons: list[str] | None = None,
    windows: list[str] | None = None,
    now: datetime | None = None,
) -> list[CreatorScorecard]:
    """Compute (and persist) scorecards. Returns the list of saved rows.

    Idempotency: existing rows for (creator, window, horizon, evaluator_version)
    are deleted-and-replaced atomically.
    """
    settings = load_project_settings()
    horizons = horizons or settings.backtest.horizons
    windows = windows or settings.scoring.windows
    now = now or datetime.now(tz=UTC)

    saved: list[CreatorScorecard] = []

    with session_scope() as session:
        creator_q = select(Creator.id, Creator.display_name)
        if creator_ids is not None:
            creator_q = creator_q.where(Creator.id.in_(creator_ids))
        creators = list(session.execute(creator_q).all())

    for creator_id, display_name in creators:
        for window_label in windows:
            cutoff = _window_cutoff(window_label, now=now)
            for horizon in horizons:
                card = _compute_one(
                    creator_id=creator_id,
                    creator_name=display_name,
                    window_label=window_label,
                    horizon=horizon,
                    cutoff=cutoff,
                    min_n=settings.scoring.min_n_for_scoring,
                )
                if card is None:
                    continue
                _persist(card)
                saved.append(card)

    log.info("scoring.run.done", n_scorecards=len(saved), n_creators=len(creators))
    return saved


def _compute_one(
    *,
    creator_id: int,
    creator_name: str,
    window_label: str,
    horizon: str,
    cutoff: datetime | None,
    min_n: int,
) -> CreatorScorecard | None:
    """Pull rows for this (creator, window, horizon) and produce a scorecard."""
    with session_scope() as session:
        q = (
            select(
                ExtractedCall.id,
                ExtractedCall.ticker,
                Document.posted_at,
                OutcomeWindow.activated,
                OutcomeWindow.return_pct,
                OutcomeWindow.excess_return_pct,
                OutcomeWindow.mae,
            )
            .select_from(SourceChannel)
            .join(Document, Document.source_channel_id == SourceChannel.id)
            .join(ExtractedCall, ExtractedCall.document_id == Document.id)
            .join(OutcomeWindow, OutcomeWindow.call_id == ExtractedCall.id)
            .where(SourceChannel.creator_id == creator_id)
            .where(OutcomeWindow.horizon == horizon)
            .where(OutcomeWindow.evaluator_version == EVALUATOR_VERSION)
            .where(OutcomeWindow.status.in_([OUTCOME_STATUS_EVALUATED, OUTCOME_STATUS_NOT_TRIGGERED]))
        )
        if cutoff is not None:
            q = q.where(Document.posted_at >= cutoff)
        rows = list(session.execute(q).all())

    if not rows:
        return None

    call_ids = {r[0] for r in rows}
    tickers = {r[1] for r in rows}
    n_calls = len(call_ids)
    if n_calls < min_n:
        log.debug(
            "scoring.skip.below_min_n",
            creator=creator_name, window=window_label, horizon=horizon,
            n=n_calls, min_n=min_n,
        )
        return None

    activated_rows = [r for r in rows if r[3]]
    n_activated = len({r[0] for r in activated_rows})
    activation_rate = n_activated / n_calls if n_calls else None

    activated_returns = [r[4] for r in activated_rows]
    excess_returns = [r[5] for r in activated_rows]
    mae_values = [r[6] for r in activated_rows if r[6] is not None]

    hit, hit_lo, hit_hi, _ = hit_rate_with_ci(activated_returns)
    mean_ret = expectancy(activated_returns)
    med_ret = median(activated_returns)
    std_ret = std(activated_returns)
    sharpe = sharpe_like(activated_returns)

    # Unconditional expectancy: treat non-activated as 0% return.
    unconditional = list(activated_returns) + [0.0] * (n_calls - n_activated)
    expect_uncond = expectancy(unconditional)

    mean_mae = expectancy(mae_values) if mae_values else None
    worst_mae = min(mae_values) if mae_values else None  # most negative

    mean_excess = expectancy(excess_returns)
    med_excess = median(excess_returns)
    excess_hit, _, _, _ = hit_rate_with_ci(excess_returns)

    return CreatorScorecard(
        creator_id=creator_id,
        window_label=window_label,
        horizon=horizon,
        n_calls=n_calls,
        n_activated=n_activated,
        n_unique_tickers=len(tickers),
        activation_rate=activation_rate,
        hit_rate=hit,
        hit_rate_lower_ci=hit_lo,
        hit_rate_upper_ci=hit_hi,
        mean_return=mean_ret,
        median_return=med_ret,
        std_return=std_ret,
        expectancy_unconditional=expect_uncond,
        sharpe_like=sharpe,
        mean_mae=mean_mae,
        worst_mae=worst_mae,
        mean_excess_return=mean_excess,
        median_excess_return=med_excess,
        excess_hit_rate=excess_hit,
        evaluator_version=EVALUATOR_VERSION,
        computed_at=datetime.now(tz=UTC),
    )


def _persist(card: CreatorScorecard) -> None:
    """Replace any prior scorecard at the same (creator, window, horizon, version)."""
    with session_scope() as session:
        existing = session.execute(
            select(CreatorScorecard).where(
                CreatorScorecard.creator_id == card.creator_id,
                CreatorScorecard.window_label == card.window_label,
                CreatorScorecard.horizon == card.horizon,
                CreatorScorecard.evaluator_version == card.evaluator_version,
            )
        ).scalar_one_or_none()
        if existing is not None:
            session.delete(existing)
            session.flush()
        session.add(card)


def run_scoring() -> dict[str, int]:
    """CLI entry point: recompute all scorecards."""
    cards = compute_scorecards()
    return {"scorecards": len(cards)}
