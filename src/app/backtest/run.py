"""Backtest orchestrator.

For each accepted ExtractedCall not yet evaluated at `evaluator_version`:
  1. Pull daily bars for the ticker + benchmark over [posted_at − 5d, posted_at + 60d].
  2. Determine activation within `trigger_window_days`.
  3. For each configured horizon, compute outcome metrics.
  4. Persist OutcomeWindow rows.

Idempotent by `(call_id, horizon, evaluator_version)`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.backtest.activation import determine_activation
from app.backtest.outcomes import compute_outcome
from app.config import load_project_settings
from app.db import session_scope
from app.logging import get_logger
from app.market.calendar import (
    horizon_to_trading_days,
    n_trading_days_after_close,
)
from app.market.yfinance_client import get_daily_bars
from app.models import (
    CALL_STATUS_ACCEPTED,
    OUTCOME_STATUS_DATA_MISSING,
    OUTCOME_STATUS_EVALUATED,
    OUTCOME_STATUS_FAILED,
    OUTCOME_STATUS_NOT_TRIGGERED,
    Document,
    ExtractedCall,
    OutcomeWindow,
)

log = get_logger(__name__)

EVALUATOR_VERSION = "v0.1"
BENCHMARK_TICKER = "SPY"


def run_backtest(
    *,
    call_ids: list[int] | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    settings = load_project_settings().backtest
    summary = {
        "calls": 0,
        "activated": 0,
        "not_triggered": 0,
        "outcome_rows": 0,
        "data_missing": 0,
        "errors": 0,
    }

    pending = _load_pending_calls(call_ids=call_ids, limit=limit)
    log.info("backtest.run.start", n_calls=len(pending))

    for call_id in pending:
        summary["calls"] += 1
        try:
            doc_summary = _process_call(call_id, settings=settings)
            summary["outcome_rows"] += doc_summary["rows"]
            if doc_summary["activated"]:
                summary["activated"] += 1
            else:
                summary["not_triggered"] += 1
            if doc_summary["data_missing"]:
                summary["data_missing"] += 1
        except Exception as e:  # pragma: no cover
            summary["errors"] += 1
            log.warning("backtest.call.error", call_id=call_id, error=str(e))

    log.info("backtest.run.done", **summary)
    return summary


def _load_pending_calls(
    *, call_ids: list[int] | None, limit: int | None
) -> list[int]:
    """Accepted calls without OutcomeWindow rows at this evaluator_version."""
    with session_scope() as session:
        evaluated_subq = (
            select(OutcomeWindow.call_id)
            .where(OutcomeWindow.evaluator_version == EVALUATOR_VERSION)
            .distinct()
            .subquery()
        )
        q = (
            select(ExtractedCall.id)
            .where(ExtractedCall.status == CALL_STATUS_ACCEPTED)
            .where(ExtractedCall.id.notin_(select(evaluated_subq)))
        )
        if call_ids is not None:
            q = q.where(ExtractedCall.id.in_(call_ids))
        q = q.order_by(ExtractedCall.id)
        if limit:
            q = q.limit(limit)
        return list(session.execute(q).scalars().all())


def _process_call(call_id: int, *, settings) -> dict:  # type: ignore[no-untyped-def]
    """Backtest a single call across all configured horizons."""
    summary = {"rows": 0, "activated": False, "data_missing": False}

    with session_scope() as session:
        call = session.get(ExtractedCall, call_id)
        if call is None:
            return summary
        doc = session.get(Document, call.document_id)
        posted_at = doc.posted_at if doc else None
        ticker = call.ticker
        # Snapshot frozen call attrs we'll need outside the session.
        frozen = _freeze_call(call)

    if posted_at is None:
        return summary

    # Window: posted_at .. + ~60 calendar days (covers 21d horizon comfortably).
    start = posted_at - timedelta(days=5)
    end = posted_at + timedelta(days=60)
    daily = get_daily_bars(ticker, start=start, end=end)
    benchmark = get_daily_bars(BENCHMARK_TICKER, start=start, end=end)

    if daily.empty:
        log.warning("backtest.no_price_data", call_id=call_id, ticker=ticker)
        for horizon in settings.horizons:
            _persist_outcome(
                call_id=call_id,
                horizon=horizon,
                window_start_at=posted_at,
                window_end_at=end,
                activated=False,
                activation_at=None,
                fill_price=None,
                outcome=None,
                status=OUTCOME_STATUS_DATA_MISSING,
                cost_bps=settings.round_trip_cost_bps,
                notes="no price data from yfinance",
            )
            summary["rows"] += 1
        summary["data_missing"] = True
        return summary

    # Activation: only bars strictly after posted_at can drive activation.
    bars_after = daily[daily.index > _ts(posted_at)]

    # If there are no bars at all after posted_at (e.g. call posted on
    # Friday after close, today is still Saturday), don't persist — leave
    # the call as "pending" so the next backtest run can pick it up.
    if bars_after.empty:
        log.info(
            "backtest.skip.no_post_bars",
            call_id=call_id,
            ticker=ticker,
            posted_at=str(posted_at),
        )
        summary["data_missing"] = True
        return summary

    activation = determine_activation(
        frozen, bars_after, trigger_window_days=settings.trigger_window_days
    )

    if not activation.activated:
        # If the trigger window has fully expired (we've seen all
        # `trigger_window_days` worth of bars), the call definitively did
        # NOT trigger. Otherwise we're still inside the window — wait.
        sessions_seen = len(bars_after)
        if sessions_seen < settings.trigger_window_days:
            log.info(
                "backtest.skip.window_open",
                call_id=call_id,
                ticker=ticker,
                sessions_seen=sessions_seen,
                window_days=settings.trigger_window_days,
            )
            summary["data_missing"] = True
            return summary

        for horizon in settings.horizons:
            _persist_outcome(
                call_id=call_id,
                horizon=horizon,
                window_start_at=posted_at,
                window_end_at=end,
                activated=False,
                activation_at=None,
                fill_price=None,
                outcome=None,
                status=OUTCOME_STATUS_NOT_TRIGGERED,
                cost_bps=settings.round_trip_cost_bps,
                notes=activation.notes,
            )
            summary["rows"] += 1
        return summary

    summary["activated"] = True

    for horizon in settings.horizons:
        try:
            n_days = horizon_to_trading_days(horizon)
            horizon_end = n_trading_days_after_close(activation.activation_at, n_days)
        except ValueError:
            # Not enough trading sessions after activation_at → horizon
            # window hasn't closed yet. Skip persistence; retry next run.
            log.info(
                "backtest.skip.horizon_open",
                call_id=call_id, horizon=horizon, ticker=ticker,
            )
            continue

        # Confirm we actually have bars covering the full horizon. yfinance
        # may not yet have published the last day even after the calendar
        # says it should exist.
        if daily[daily.index >= _ts(horizon_end)].empty:
            log.info(
                "backtest.skip.horizon_data_pending",
                call_id=call_id, horizon=horizon, ticker=ticker,
            )
            continue

        outcome = compute_outcome(
            frozen,
            fill_price=activation.fill_price,
            activation_at=activation.activation_at,
            daily_bars=daily,
            benchmark_bars=benchmark,
            horizon_end_at=horizon_end,
            cost_bps=settings.round_trip_cost_bps,
        )

        _persist_outcome(
            call_id=call_id,
            horizon=horizon,
            window_start_at=activation.activation_at,
            window_end_at=horizon_end,
            activated=True,
            activation_at=activation.activation_at,
            fill_price=activation.fill_price,
            outcome=outcome,
            status=OUTCOME_STATUS_EVALUATED,
            cost_bps=settings.round_trip_cost_bps,
            notes=activation.notes,
        )
        summary["rows"] += 1

    return summary


def _ts(dt: datetime):  # type: ignore[no-untyped-def]
    import pandas as pd

    if dt.tzinfo is None:
        return pd.Timestamp(dt).tz_localize("UTC")
    return pd.Timestamp(dt).tz_convert("UTC")


def _freeze_call(call: ExtractedCall) -> ExtractedCall:
    """Detach a copy of the call so we can use it outside its session."""
    f = ExtractedCall(
        id=call.id,
        ticker=call.ticker,
        direction=call.direction,
        entry_type=call.entry_type,
        entry_price=call.entry_price,
        target_price=call.target_price,
        stop_price=call.stop_price,
        timeframe=call.timeframe,
    )
    return f


def _persist_outcome(
    *,
    call_id: int,
    horizon: str,
    window_start_at: datetime,
    window_end_at: datetime,
    activated: bool,
    activation_at: datetime | None,
    fill_price: float | None,
    outcome,  # OutcomeMetrics | None
    status: str,
    cost_bps: int,
    notes: str | None,
) -> None:
    with session_scope() as session:
        # Idempotency by (call_id, horizon, evaluator_version).
        existing = session.scalar(
            select(OutcomeWindow.id).where(
                OutcomeWindow.call_id == call_id,
                OutcomeWindow.horizon == horizon,
                OutcomeWindow.evaluator_version == EVALUATOR_VERSION,
            )
        )
        if existing is not None:
            return

        try:
            session.add(
                OutcomeWindow(
                    call_id=call_id,
                    horizon=horizon,
                    window_start_at=_ensure_utc(window_start_at),
                    window_end_at=_ensure_utc(window_end_at),
                    activated=activated,
                    activation_at=_ensure_utc(activation_at) if activation_at else None,
                    entry_fill_price=fill_price,
                    exit_at=outcome.exit_at if outcome else None,
                    exit_price=outcome.exit_price if outcome else None,
                    gross_return_pct=outcome.gross_return_pct if outcome else None,
                    return_pct=outcome.return_pct if outcome else None,
                    mfe=outcome.mfe if outcome else None,
                    mae=outcome.mae if outcome else None,
                    hit_target=outcome.hit_target if outcome else None,
                    hit_stop=outcome.hit_stop if outcome else None,
                    benchmark_return_pct=outcome.benchmark_return_pct if outcome else None,
                    excess_return_pct=outcome.excess_return_pct if outcome else None,
                    assumptions_cost_bps=cost_bps,
                    status=status,
                    notes=notes,
                    evaluator_version=EVALUATOR_VERSION,
                    computed_at=datetime.now(tz=UTC),
                )
            )
        except Exception as e:  # pragma: no cover
            log.warning(
                "backtest.persist_failed",
                call_id=call_id, horizon=horizon, status=OUTCOME_STATUS_FAILED, error=str(e),
            )


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt
