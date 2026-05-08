# Lens Accuracy Infrastructure Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every Deep + Quick lens call alongside the eventual price-outcome at 1d/3d/5d/21d horizons, then compute per-lens accuracy scorecards (Wilson CIs, regime breakdown). Recording is silent — does NOT change runtime behavior. Unblocks Phase 6 (lens-weighted judge synthesis).

**Architecture:**
- Two new ORM tables: `LensSnapshot` (one row per lens × Deep/Quick run) and `LensOutcome` (one row per snapshot × horizon). Foreign key to existing `ResearchPlan` (nullable for legacy backfill).
- Recording integrated into `deep.py` and `quick.py` after analyst results land, before judge runs. Idempotent on `(ticker, as_of, lens_name)`.
- Outcome computation = nightly cron job that finds snapshots whose horizon has elapsed and writes the realized return + SPY-excess + regime classification (reuses ADR 0003 / 0007 outcome-window machinery).
- `LensScorecard` analytics analogous to `CreatorScorecard`: per-lens hit rate + Wilson CI + regime/conviction/direction split.
- Surfaced via `tsr lens-scorecards` CLI and `GET /research/lens-scorecards` HTTP.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x, Alembic (already wired per [recent-changes-2026-05-08.md §7](../recent-changes-2026-05-08.md)), Pydantic v2, FastAPI, pytest, Typer for the CLI.

**Out of scope (defer to Phase 6):**
- Injecting `LensScorecard` into the judge prompt.
- Backfill of historical `ResearchPlan.lenses` (covered by Task 9 *only if* cached plans exist with non-null `lenses`).
- Adversarial pairing, style-aware judges, debate rounds. Those are Phases 2-5.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `src/app/models.py` | modify | Add `LensSnapshot` + `LensOutcome` ORM tables. |
| `alembic/versions/<hash>_add_lens_snapshots_and_outcomes.py` | **new** (autogen) | Schema migration. |
| `src/app/research/lens_recording.py` | **new** | `record_lens_snapshots(plan_id, packet, lenses, sources, durations, costs) -> list[int]`. Idempotent. |
| `src/app/research/deep.py` | modify | Call `record_lens_snapshots` after `run_agents_parallel` lands; pass returned snapshot ids forward only for logging. |
| `src/app/research/quick.py` | modify | Call `record_lens_snapshots` after lens parse from raw response. |
| `src/app/scoring/lens_outcomes.py` | **new** | `compute_lens_outcomes(session, max_age_days=60) -> int` — finds snapshots whose horizons elapsed, computes realized returns via `app.market` cache, writes `LensOutcome` rows. |
| `src/app/scoring/lens_scorecards.py` | **new** | `LensScorecard` dataclass + `compute_lens_scorecards(session, lookback_days=90) -> list[LensScorecard]` using existing `wilson_ci` from `app.scoring`. |
| `src/app/cli.py` | modify | Add `tsr score-lens-outcomes` and `tsr lens-scorecards` Typer commands. |
| `apps/api/app/routes/research.py` | modify | Add `GET /research/lens-scorecards` endpoint. |
| `scripts/daily_run.sh` | modify | Append `tsr score-lens-outcomes` step before `tsr score`. |
| `tests/test_lens_recording.py` | **new** | Idempotency, both modes write 4 lenses, plan_id linkage. |
| `tests/test_lens_outcomes.py` | **new** | Horizon math + direction-correctness rules. |
| `tests/test_lens_scorecards.py` | **new** | Wilson CI, regime split, conviction split. |

---

## Task 1 — ORM models for `LensSnapshot` and `LensOutcome`

**Files:**
- Modify: `src/app/models.py`

- [ ] **Step 1: Read current models for ORM-style match**

```bash
cd /Users/jaspervalk/Documents/projects/trading-signal-research && grep -n "class Creator\|class ResearchPlan\|class CreatorScorecard\|class OutcomeWindow" src/app/models.py
```

Confirm SQLAlchemy 2.x `Mapped[...]`, `mapped_column(...)` style. If the codebase uses Declarative Base + `Column(...)` style, follow that.

- [ ] **Step 2: Add the new ORM classes**

Append (in the part of the file where other research-related tables are defined; if ordering matters for relationship resolution, place them after `ResearchPlan`):

```python
class LensSnapshot(Base):
    """One per (Deep|Quick run × lens). Captures the lens's direction call
    at decision time. Linked to the parent ResearchPlan when available so
    we can reconstruct the full Deep run later.
    """

    __tablename__ = "lens_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_plans.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)  # "quick" | "deep"
    lens_name: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # "bullish" | "bearish" | "neutral"
    conviction: Mapped[str] = mapped_column(String(8), nullable=False)  # "low" | "medium" | "high"
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    points_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array of strings
    sources_used: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint("ticker", "as_of", "mode", "lens_name", name="uq_lens_snapshot_run"),
    )


class LensOutcome(Base):
    """One per (LensSnapshot × horizon). Computed nightly once the horizon
    has elapsed. `direction_correct` is the binary outcome used for hit-rate
    Wilson CIs; `excess_vs_spy_pct` is the more honest metric when reported.
    """

    __tablename__ = "lens_outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("lens_snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    horizon: Mapped[str] = mapped_column(String(8), nullable=False)  # "1d" | "3d" | "5d" | "21d"
    return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    excess_vs_spy_pct: Mapped[float] = mapped_column(Float, nullable=False)
    direction_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    regime: Mapped[str] = mapped_column(String(8), nullable=False)  # "up" | "flat" | "down"
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint("snapshot_id", "horizon", name="uq_lens_outcome_horizon"),
    )
```

If the codebase imports look different (e.g. `from sqlalchemy import Column, Integer, String`), adapt — but match exactly what other tables use.

- [ ] **Step 3: Verify Python syntax compiles**

```bash
cd /Users/jaspervalk/Documents/projects/trading-signal-research && python -c "from app import models; print(models.LensSnapshot.__tablename__, models.LensOutcome.__tablename__)"
```

Expected output: `lens_snapshots lens_outcomes`

- [ ] **Step 4: Commit ORM changes only (no migration yet)**

```bash
git add src/app/models.py
git commit -m "feat(models): add LensSnapshot + LensOutcome ORM tables

LensSnapshot persists each Deep/Quick lens direction call at decision
time; LensOutcome computes realised returns + direction-correct flags
at 1d/3d/5d/21d horizons. Foundation for Phase 1 lens accuracy
scorecards. Migration follows in next commit."
```

---

## Task 2 — Alembic migration

**Files:**
- Create (autogen): `alembic/versions/<hash>_add_lens_snapshots_and_outcomes.py`

- [ ] **Step 1: Generate migration**

```bash
cd /Users/jaspervalk/Documents/projects/trading-signal-research && alembic revision --autogenerate -m "add_lens_snapshots_and_outcomes"
```

Expected: a new file in `alembic/versions/` with an autogenerated hash. Note the filename.

- [ ] **Step 2: Inspect the generated migration**

```bash
cat alembic/versions/*add_lens_snapshots_and_outcomes*.py
```

Verify it contains:
- `op.create_table('lens_snapshots', ...)` with all columns from Task 1.
- `op.create_table('lens_outcomes', ...)` with all columns + the FK.
- `op.create_index(...)` for the indexes (`plan_id`, `ticker`, `as_of`, `lens_name`, `snapshot_id`).
- The `UniqueConstraint`s for `uq_lens_snapshot_run` and `uq_lens_outcome_horizon`.
- A matching `op.drop_table(...)` in the `downgrade()` function.

If anything is missing — usually because of import or naming-convention differences — hand-edit the file to add it. Common gotcha: SQLite doesn't enforce `ondelete='CASCADE'` natively; the migration should still emit it for forward portability.

- [ ] **Step 3: Apply the migration**

```bash
alembic upgrade head
```

Expected: log lines about creating both tables. No errors.

- [ ] **Step 4: Verify with a no-op autogen**

```bash
alembic check
```

Expected: `No new upgrade operations detected.`

If anything is detected, the migration is incomplete — go back to Step 2 and add the missing bits.

- [ ] **Step 5: Smoke roundtrip — downgrade then upgrade**

```bash
alembic downgrade -1
alembic upgrade head
```

Expected: clean output. Confirms downgrade path works.

- [ ] **Step 6: Commit migration**

```bash
git add alembic/versions/*add_lens_snapshots_and_outcomes*.py
git commit -m "feat(alembic): migration for lens_snapshots + lens_outcomes tables"
```

---

## Task 3 — Recording helper + wire into Deep mode

**Files:**
- Create: `src/app/research/lens_recording.py`
- Modify: `src/app/research/deep.py`
- Create: `tests/test_lens_recording.py`

- [ ] **Step 1: Write the failing test**

`tests/test_lens_recording.py`:

```python
"""Tests for lens-snapshot recording from Deep mode.

Records are idempotent on (ticker, as_of, mode, lens_name) so re-running
the same Deep call (e.g. force=true) does not duplicate snapshots.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, LensSnapshot
from app.research.lens_recording import record_lens_snapshots
from app.research.schema import LensView


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _lens(name: str, direction: str = "bullish", conviction: str = "high") -> LensView:
    return LensView(
        name=name,
        direction=direction,
        conviction=conviction,
        summary=f"stub {name}",
        points=["p1", "p2"],
    )


def test_record_writes_one_row_per_lens(session: Session):
    lenses = [
        _lens("quantitative"),
        _lens("fundamental", direction="bearish"),
        _lens("sentiment_macro", conviction="low"),
        _lens("contrarian_risk", direction="neutral"),
    ]
    ids = record_lens_snapshots(
        session,
        plan_id=None,
        ticker="AAPL",
        as_of=datetime(2026, 5, 8, tzinfo=UTC),
        mode="deep",
        lenses=lenses,
        sources_used=["technicals", "claims"],
        durations_ms=[100, 120, 110, 130],
        costs_usd=[0.001, 0.002, 0.001, 0.0015],
    )
    assert len(ids) == 4
    rows = session.execute(select(LensSnapshot)).scalars().all()
    assert len(rows) == 4
    by_name = {r.lens_name: r for r in rows}
    assert by_name["fundamental"].direction == "bearish"
    assert by_name["sentiment_macro"].conviction == "low"
    assert json.loads(by_name["quantitative"].points_json) == ["p1", "p2"]


def test_record_is_idempotent_on_run_key(session: Session):
    lens = _lens("quantitative")
    args = dict(
        session=session,
        plan_id=None,
        ticker="AAPL",
        as_of=datetime(2026, 5, 8, tzinfo=UTC),
        mode="deep",
        sources_used=[],
        durations_ms=[100],
        costs_usd=[0.001],
    )
    record_lens_snapshots(lenses=[lens], **args)
    record_lens_snapshots(lenses=[lens], **args)  # second run, same key
    rows = session.execute(select(LensSnapshot)).scalars().all()
    assert len(rows) == 1


def test_record_handles_per_lens_cost_mismatch(session: Session):
    """If costs_usd is shorter than lenses, missing entries default to 0.0."""
    lenses = [_lens("quantitative"), _lens("fundamental")]
    record_lens_snapshots(
        session=session,
        plan_id=None,
        ticker="AAPL",
        as_of=datetime(2026, 5, 8, tzinfo=UTC),
        mode="deep",
        lenses=lenses,
        sources_used=[],
        durations_ms=[100],  # only one — second defaults to 0
        costs_usd=[0.001],
    )
    rows = session.execute(select(LensSnapshot)).scalars().all()
    by_name = {r.lens_name: r for r in rows}
    assert by_name["quantitative"].duration_ms == 100
    assert by_name["fundamental"].duration_ms == 0
```

- [ ] **Step 2: Verify the test fails**

```bash
pytest tests/test_lens_recording.py -v
```

Expected: FAIL with `ImportError: cannot import name 'record_lens_snapshots'`.

- [ ] **Step 3: Create `src/app/research/lens_recording.py`**

```python
"""Persist `LensView` reads to `LensSnapshot` rows for later accuracy scoring.

Idempotent on (ticker, as_of, mode, lens_name). Safe to call from cached
Deep runs (cache hit re-emits the same lenses; recording skips duplicates).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LensSnapshot
from app.research.schema import LensView


def record_lens_snapshots(
    session: Session,
    *,
    plan_id: int | None,
    ticker: str,
    as_of: datetime,
    mode: str,  # "quick" | "deep"
    lenses: Sequence[LensView],
    sources_used: Sequence[str],
    durations_ms: Sequence[int] = (),
    costs_usd: Sequence[float] = (),
) -> list[int]:
    """Write one `LensSnapshot` per lens. Returns the row ids (existing or new).

    Idempotent: a duplicate `(ticker, as_of, mode, lens_name)` is detected
    and the existing row's id is returned without rewriting. This lets the
    cache layer re-record on cache-hit without polluting the table.
    """
    out: list[int] = []
    sources_json = json.dumps(list(sources_used))
    for i, lens in enumerate(lenses):
        existing = session.execute(
            select(LensSnapshot).where(
                LensSnapshot.ticker == ticker,
                LensSnapshot.as_of == as_of,
                LensSnapshot.mode == mode,
                LensSnapshot.lens_name == lens.name,
            )
        ).scalar_one_or_none()
        if existing is not None:
            out.append(existing.id)
            continue

        row = LensSnapshot(
            plan_id=plan_id,
            ticker=ticker,
            as_of=as_of,
            mode=mode,
            lens_name=lens.name,
            direction=lens.direction,
            conviction=lens.conviction,
            summary=lens.summary,
            points_json=json.dumps(list(lens.points)),
            sources_used=sources_json,
            cost_usd=costs_usd[i] if i < len(costs_usd) else 0.0,
            duration_ms=durations_ms[i] if i < len(durations_ms) else 0,
        )
        session.add(row)
        session.flush()  # populate row.id without committing
        out.append(row.id)
    return out


__all__ = ["record_lens_snapshots"]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_lens_recording.py -v
```

Expected: 3/3 PASS.

- [ ] **Step 5: Wire into `deep.py`**

Read `src/app/research/deep.py` `run()` function. After `analyst_results = run_agents_parallel(runners)` lands and `lenses` is assembled (around line 68 today), add a recording call **before** the judge runs. Pass `plan_id=None` for now — the plan doesn't exist yet at this point in the flow; the cache layer is what assigns `plan_id`. The recording API tolerates `None`.

In the same `run()` function:

```python
    # Persist lens snapshots for later accuracy scoring (Phase 1).
    # plan_id is None here — the cache layer assigns ids after the judge
    # synthesises. Recording is idempotent so a future "link plan_id"
    # backfill is a separate concern.
    from app.db import session_scope  # local import to avoid cycle at module load
    from app.research.lens_recording import record_lens_snapshots

    durations = [ar.duration_ms for ar in analyst_results if ar.lens is not None]
    costs = [ar.cost_usd for ar in analyst_results if ar.lens is not None]
    try:
        with session_scope() as recording_session:
            record_lens_snapshots(
                recording_session,
                plan_id=None,
                ticker=packet.ticker,
                as_of=packet.as_of,
                mode="deep",
                lenses=lenses,
                sources_used=packet.sources_used,
                durations_ms=durations,
                costs_usd=costs,
            )
    except Exception as e:
        log.warning("research.deep.lens_recording_failed", error=str(e))
```

The `try/except` swallows recording failures so a flaky DB never breaks Deep mode. Recording is best-effort; a missed snapshot just means missing data for one run.

- [ ] **Step 6: Run all research tests to confirm no regression**

```bash
pytest tests/test_research_*.py tests/test_lens_recording.py -v
```

Expected: all PASS, ≥ previous count + 3 new lens-recording tests.

- [ ] **Step 7: Commit**

```bash
git add src/app/research/lens_recording.py src/app/research/deep.py tests/test_lens_recording.py
git commit -m "feat(research): record lens snapshots from Deep mode

Persists each Deep-run lens direction call to lens_snapshots after the
analysts land but before the judge synthesises. Idempotent on
(ticker, as_of, mode, lens_name) so cached re-runs don't duplicate.
Recording is best-effort: DB failures log and continue; Deep mode
runtime behavior unchanged."
```

---

## Task 4 — Wire recording into Quick mode

**Files:**
- Modify: `src/app/research/quick.py`
- Modify: `tests/test_lens_recording.py` (add Quick coverage)

- [ ] **Step 1: Append a Quick-mode recording test**

In `tests/test_lens_recording.py`, append:

```python
def test_record_distinct_per_mode(session: Session):
    """Same ticker × as_of × lens, different modes → two rows (one each)."""
    lens = _lens("quantitative")
    args = dict(
        session=session,
        plan_id=None,
        ticker="AAPL",
        as_of=datetime(2026, 5, 8, tzinfo=UTC),
        sources_used=[],
        durations_ms=[100],
        costs_usd=[0.001],
    )
    record_lens_snapshots(mode="quick", lenses=[lens], **args)
    record_lens_snapshots(mode="deep", lenses=[lens], **args)
    rows = session.execute(select(LensSnapshot)).scalars().all()
    assert len(rows) == 2
    modes = sorted(r.mode for r in rows)
    assert modes == ["deep", "quick"]
```

- [ ] **Step 2: Verify it passes (existing helper already discriminates by mode)**

```bash
pytest tests/test_lens_recording.py::test_record_distinct_per_mode -v
```

Expected: PASS (the recording function already keys on `mode`).

- [ ] **Step 3: Wire into `quick.py`**

In `src/app/research/quick.py` `run()` function, after `_build_plan(...)` returns the plan but before `return QuickResult(...)`, add:

```python
    # Persist lens snapshots for later accuracy scoring (Phase 1).
    from app.db import session_scope
    from app.research.lens_recording import record_lens_snapshots

    if plan.lenses:  # only when the LLM emitted the lens block
        try:
            with session_scope() as recording_session:
                record_lens_snapshots(
                    recording_session,
                    plan_id=None,
                    ticker=packet.ticker,
                    as_of=packet.as_of,
                    mode="quick",
                    lenses=plan.lenses,
                    sources_used=packet.sources_used,
                    durations_ms=[duration_ms] * len(plan.lenses),
                    costs_usd=[round(cost / max(len(plan.lenses), 1), 6)] * len(plan.lenses),
                )
        except Exception as e:
            log.warning("research.quick.lens_recording_failed", error=str(e))
```

The cost-per-lens estimate is the total Quick cost divided evenly across the 4 lenses — Quick is one Haiku call, so attribution is approximate. That's fine; aggregate scorecards are what matters.

- [ ] **Step 4: Run all tests**

```bash
pytest tests/test_research_*.py tests/test_lens_recording.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/research/quick.py tests/test_lens_recording.py
git commit -m "feat(research): record lens snapshots from Quick mode

Same recording helper, mode='quick'. Cost-per-lens is the total Quick
cost / 4 (single Haiku call, attribution is approximate). Skipped when
the lens block is missing from the response (legacy / partial responses)."
```

---

## Task 5 — Outcome computation + CLI

**Files:**
- Create: `src/app/scoring/lens_outcomes.py`
- Modify: `src/app/cli.py`
- Create: `tests/test_lens_outcomes.py`

- [ ] **Step 1: Read existing outcome / market machinery**

```bash
cd /Users/jaspervalk/Documents/projects/trading-signal-research && grep -n "compute_outcome\|MarketReader\|trading_days_after" src/app/backtest/*.py src/app/market/*.py | head -30
```

Find the canonical "give me close at as_of + N trading days" and "give me SPY return over the same window" helpers. Reuse them.

- [ ] **Step 2: Write failing tests**

`tests/test_lens_outcomes.py`:

```python
"""Tests for lens outcome computation: realised returns + direction-correct
classification at 1d/3d/5d/21d horizons.

Uses an in-memory SQLite + a stub market reader that returns deterministic
returns. Real market data is exercised only by the integration tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, LensOutcome, LensSnapshot
from app.scoring.lens_outcomes import (
    HORIZONS,
    compute_lens_outcomes,
    direction_correct,
    regime_for,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _snapshot(session: Session, **overrides) -> LensSnapshot:
    defaults = dict(
        plan_id=None,
        ticker="AAPL",
        as_of=datetime(2026, 4, 1, tzinfo=UTC),  # >21 trading days ago
        mode="deep",
        lens_name="quantitative",
        direction="bullish",
        conviction="high",
        summary="stub",
        points_json=json.dumps(["p1"]),
        sources_used=json.dumps([]),
        cost_usd=0.001,
        duration_ms=100,
    )
    defaults.update(overrides)
    row = LensSnapshot(**defaults)
    session.add(row)
    session.flush()
    return row


class _StubMarket:
    """Returns deterministic returns by horizon + regime."""

    def __init__(self, returns_by_horizon: dict[str, float], spy_returns: dict[str, float], regime: str = "up"):
        self.returns_by_horizon = returns_by_horizon
        self.spy_returns = spy_returns
        self.regime = regime

    def ticker_return(self, ticker: str, as_of: datetime, horizon: str) -> float | None:
        return self.returns_by_horizon.get(horizon)

    def spy_return(self, as_of: datetime, horizon: str) -> float | None:
        return self.spy_returns.get(horizon)

    def regime_for(self, as_of: datetime) -> str:
        return self.regime


def test_direction_correct_bullish_up_move():
    assert direction_correct("bullish", 0.025) is True
    assert direction_correct("bullish", -0.005) is False


def test_direction_correct_bearish_down_move():
    assert direction_correct("bearish", -0.030) is True
    assert direction_correct("bearish", 0.010) is False


def test_direction_correct_neutral_within_threshold():
    """Neutral is correct when |return| <= 0.5% (small move)."""
    assert direction_correct("neutral", 0.003) is True
    assert direction_correct("neutral", -0.004) is True
    assert direction_correct("neutral", 0.020) is False  # too big to be neutral


def test_compute_lens_outcomes_writes_one_row_per_horizon(session: Session):
    snap = _snapshot(session)
    market = _StubMarket(
        returns_by_horizon={"1d": 0.005, "3d": 0.012, "5d": 0.025, "21d": 0.060},
        spy_returns={"1d": 0.001, "3d": 0.005, "5d": 0.010, "21d": 0.020},
        regime="up",
    )
    n = compute_lens_outcomes(session, market=market, max_age_days=60)
    assert n == 4  # 4 horizons
    rows = session.execute(select(LensOutcome).order_by(LensOutcome.horizon)).scalars().all()
    horizons = sorted(r.horizon for r in rows)
    assert horizons == sorted(HORIZONS)
    by_h = {r.horizon: r for r in rows}
    assert by_h["5d"].return_pct == pytest.approx(0.025)
    assert by_h["5d"].excess_vs_spy_pct == pytest.approx(0.025 - 0.010)
    assert by_h["5d"].direction_correct is True
    assert by_h["5d"].regime == "up"


def test_compute_lens_outcomes_is_idempotent(session: Session):
    snap = _snapshot(session)
    market = _StubMarket(
        returns_by_horizon={"1d": 0.005, "3d": 0.012, "5d": 0.025, "21d": 0.060},
        spy_returns={"1d": 0.001, "3d": 0.005, "5d": 0.010, "21d": 0.020},
    )
    compute_lens_outcomes(session, market=market, max_age_days=60)
    n2 = compute_lens_outcomes(session, market=market, max_age_days=60)
    assert n2 == 0  # already computed
    rows = session.execute(select(LensOutcome)).scalars().all()
    assert len(rows) == 4


def test_compute_lens_outcomes_skips_too_recent(session: Session):
    """Snapshots whose as_of + horizon hasn't elapsed yet are skipped."""
    very_recent = _snapshot(
        session, as_of=datetime.now(UTC) - timedelta(hours=12)  # < 1d
    )
    market = _StubMarket(returns_by_horizon={"1d": 0.005}, spy_returns={"1d": 0.001})
    n = compute_lens_outcomes(session, market=market, max_age_days=60)
    assert n == 0
```

- [ ] **Step 3: Verify tests fail**

```bash
pytest tests/test_lens_outcomes.py -v
```

Expected: FAIL — `ImportError: cannot import name 'compute_lens_outcomes'`.

- [ ] **Step 4: Create `src/app/scoring/lens_outcomes.py`**

```python
"""Compute realized outcomes for `LensSnapshot` rows whose horizon has elapsed.

For each (snapshot, horizon) pair where `as_of + horizon` is in the past:
- `return_pct`: ticker close at horizon vs close at `as_of`.
- `excess_vs_spy_pct`: ticker return minus SPY return over the same window.
- `direction_correct`: True if the lens's direction call matched what
  the price action actually did (with a small ±0.5% band for "neutral").
- `regime`: SPY 21-day trend at `as_of` (up / flat / down) — same regime
  classifier used by the strategy walk-forward (ADR 0007).

Idempotent: a row already in `lens_outcomes` for the same (snapshot, horizon)
is skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LensOutcome, LensSnapshot

HORIZONS: tuple[str, ...] = ("1d", "3d", "5d", "21d")
HORIZON_DAYS: dict[str, int] = {"1d": 1, "3d": 3, "5d": 5, "21d": 21}
NEUTRAL_BAND = 0.005  # ±0.5% — anything inside this counts as "neutral correct"


class MarketProvider(Protocol):
    """Minimal interface for return + regime lookup. The real implementation
    lives in `app.market` / `app.backtest.market_reader`; tests pass a stub.
    """

    def ticker_return(self, ticker: str, as_of: datetime, horizon: str) -> float | None: ...
    def spy_return(self, as_of: datetime, horizon: str) -> float | None: ...
    def regime_for(self, as_of: datetime) -> str: ...


def direction_correct(direction: str, return_pct: float) -> bool:
    if direction == "bullish":
        return return_pct > NEUTRAL_BAND
    if direction == "bearish":
        return return_pct < -NEUTRAL_BAND
    return abs(return_pct) <= NEUTRAL_BAND


def regime_for(as_of: datetime, market: MarketProvider) -> str:
    return market.regime_for(as_of)


def compute_lens_outcomes(
    session: Session,
    *,
    market: MarketProvider,
    max_age_days: int = 60,
) -> int:
    """Compute outcomes for all eligible snapshots. Returns the number of
    LensOutcome rows newly created.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    snapshots = (
        session.execute(
            select(LensSnapshot).where(LensSnapshot.as_of >= cutoff)
        )
        .scalars()
        .all()
    )

    n_created = 0
    for snap in snapshots:
        for horizon in HORIZONS:
            # Skip if snapshot is younger than the horizon
            elapsed = datetime.now(timezone.utc) - snap.as_of
            if elapsed < timedelta(days=HORIZON_DAYS[horizon]):
                continue
            # Skip if outcome already computed
            existing = session.execute(
                select(LensOutcome).where(
                    LensOutcome.snapshot_id == snap.id,
                    LensOutcome.horizon == horizon,
                )
            ).scalar_one_or_none()
            if existing is not None:
                continue

            ticker_ret = market.ticker_return(snap.ticker, snap.as_of, horizon)
            if ticker_ret is None:
                continue
            spy_ret = market.spy_return(snap.as_of, horizon) or 0.0
            outcome = LensOutcome(
                snapshot_id=snap.id,
                horizon=horizon,
                return_pct=ticker_ret,
                excess_vs_spy_pct=ticker_ret - spy_ret,
                direction_correct=direction_correct(snap.direction, ticker_ret),
                regime=regime_for(snap.as_of, market),
            )
            session.add(outcome)
            n_created += 1

    if n_created:
        session.flush()
    return n_created


__all__ = [
    "HORIZONS",
    "HORIZON_DAYS",
    "MarketProvider",
    "compute_lens_outcomes",
    "direction_correct",
    "regime_for",
]
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_lens_outcomes.py -v
```

Expected: 6/6 PASS.

- [ ] **Step 6: Add a real `MarketProvider` adapter (production)**

Read `src/app/backtest/market_reader.py` to find the existing close-price fetcher. Create a small adapter `src/app/scoring/lens_market_adapter.py`:

```python
"""Production MarketProvider adapter — wraps the existing market_reader / SPY
helpers to fit the `MarketProvider` Protocol used by lens-outcome scoring.
"""

from __future__ import annotations

from datetime import datetime

from app.backtest.market_reader import MarketReader  # whatever the canonical name is
from app.scoring.lens_outcomes import HORIZON_DAYS


class CachedMarketAdapter:
    def __init__(self, reader: MarketReader):
        self._reader = reader

    def ticker_return(self, ticker: str, as_of: datetime, horizon: str) -> float | None:
        n_days = HORIZON_DAYS[horizon]
        return self._reader.return_over_window(ticker, as_of, n_trading_days=n_days)

    def spy_return(self, as_of: datetime, horizon: str) -> float | None:
        return self.ticker_return("SPY", as_of, horizon)

    def regime_for(self, as_of: datetime) -> str:
        # SPY 21-day trend: up if > +2%, down if < -2%, else flat.
        # ADR 0007 / 0003 use this exact threshold; reuse if available.
        ret = self.ticker_return("SPY", as_of, "21d")
        if ret is None:
            return "flat"
        if ret > 0.02:
            return "up"
        if ret < -0.02:
            return "down"
        return "flat"
```

If `MarketReader.return_over_window` doesn't exist with that exact signature, adapt — read the actual API in `market_reader.py`. The contract is "given a ticker + decision date + N trading days, return the realised return."

- [ ] **Step 7: Add CLI command `tsr score-lens-outcomes`**

In `src/app/cli.py`, add a Typer command. Find the existing pattern for commands like `tsr backtest` or `tsr score` and mirror it. Skeleton:

```python
@app.command("score-lens-outcomes")
def score_lens_outcomes_cmd(
    max_age_days: int = typer.Option(60, help="Snapshots older than this are skipped."),
):
    """Compute realized outcomes for accumulated lens snapshots."""
    from app.backtest.market_reader import MarketReader  # or whatever import is canonical
    from app.db import session_scope
    from app.scoring.lens_market_adapter import CachedMarketAdapter
    from app.scoring.lens_outcomes import compute_lens_outcomes

    with session_scope() as session:
        reader = MarketReader(session)  # follow existing constructor signature
        adapter = CachedMarketAdapter(reader)
        n = compute_lens_outcomes(session, market=adapter, max_age_days=max_age_days)
        typer.echo(f"Computed {n} new lens outcomes.")
```

- [ ] **Step 8: Smoke test CLI (no real data needed — empty DB OK)**

```bash
.venv/bin/tsr score-lens-outcomes --max-age-days 30
```

Expected: `Computed 0 new lens outcomes.` (assuming no snapshots yet — that's correct).

- [ ] **Step 9: Run full test suite to confirm no regressions**

```bash
pytest -q
```

Expected: pre-existing tests still green; +6 new lens-outcome tests.

- [ ] **Step 10: Commit**

```bash
git add src/app/scoring/lens_outcomes.py src/app/scoring/lens_market_adapter.py src/app/cli.py tests/test_lens_outcomes.py
git commit -m "feat(scoring): compute lens outcomes at 1d/3d/5d/21d horizons

Reuses the existing market_reader for SPY-relative returns + ADR 0007's
regime classifier. Outcome scoring is idempotent on (snapshot, horizon)
and skips snapshots whose horizon hasn't elapsed yet.

CLI: tsr score-lens-outcomes [--max-age-days N]"
```

---

## Task 6 — `LensScorecard` analytics + CLI

**Files:**
- Create: `src/app/scoring/lens_scorecards.py`
- Modify: `src/app/cli.py`
- Create: `tests/test_lens_scorecards.py`

- [ ] **Step 1: Inspect existing Wilson CI helper**

```bash
grep -rn "def wilson_ci\|wilson(" src/app/scoring/ | head
```

There should be an existing `wilson_ci(successes, n) -> (lo, hi)` helper from `CreatorScorecard` work. Reuse it.

- [ ] **Step 2: Write failing tests**

`tests/test_lens_scorecards.py`:

```python
"""Tests for LensScorecard aggregations: Wilson CI, regime split, conviction split."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, LensOutcome, LensSnapshot
from app.scoring.lens_scorecards import LensScorecard, compute_lens_scorecards


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _row(session: Session, lens: str, direction: str, conviction: str, correct: bool, regime: str = "up", days_ago: int = 30):
    snap = LensSnapshot(
        plan_id=None,
        ticker="AAPL",
        as_of=datetime.now(UTC) - timedelta(days=days_ago),
        mode="deep",
        lens_name=lens,
        direction=direction,
        conviction=conviction,
        summary="stub",
        points_json=json.dumps([]),
        sources_used=json.dumps([]),
        cost_usd=0.0,
        duration_ms=0,
    )
    session.add(snap)
    session.flush()
    out = LensOutcome(
        snapshot_id=snap.id,
        horizon="5d",
        return_pct=0.02 if correct else -0.02,
        excess_vs_spy_pct=0.01 if correct else -0.01,
        direction_correct=correct,
        regime=regime,
    )
    session.add(out)


def test_lens_scorecard_aggregates_per_lens(session: Session):
    for _ in range(8):
        _row(session, "quantitative", "bullish", "high", correct=True)
    for _ in range(2):
        _row(session, "quantitative", "bullish", "high", correct=False)
    for _ in range(4):
        _row(session, "fundamental", "bearish", "high", correct=True)
    session.flush()

    cards = compute_lens_scorecards(session, lookback_days=90, horizon="5d")
    by_name = {c.lens_name: c for c in cards}
    assert by_name["quantitative"].n == 10
    assert by_name["quantitative"].hit_rate == pytest.approx(0.8, abs=0.001)
    assert 0.4 < by_name["quantitative"].hit_rate_lo < 0.8
    assert by_name["quantitative"].hit_rate_hi <= 1.0
    assert by_name["fundamental"].n == 4


def test_lens_scorecard_regime_split(session: Session):
    for _ in range(3):
        _row(session, "quantitative", "bullish", "high", correct=True, regime="up")
    for _ in range(3):
        _row(session, "quantitative", "bullish", "high", correct=False, regime="down")
    session.flush()

    cards = compute_lens_scorecards(session, lookback_days=90, horizon="5d")
    quant = next(c for c in cards if c.lens_name == "quantitative")
    assert quant.by_regime["up"]["hit_rate"] == pytest.approx(1.0)
    assert quant.by_regime["down"]["hit_rate"] == pytest.approx(0.0)


def test_lens_scorecard_skips_unscored_snapshots(session: Session):
    """A snapshot without a matching outcome row is excluded from the scorecard."""
    snap = LensSnapshot(
        plan_id=None,
        ticker="AAPL",
        as_of=datetime.now(UTC) - timedelta(days=10),
        mode="deep",
        lens_name="quantitative",
        direction="bullish",
        conviction="high",
        summary="stub",
        points_json=json.dumps([]),
        sources_used=json.dumps([]),
        cost_usd=0.0,
        duration_ms=0,
    )
    session.add(snap)
    session.flush()
    cards = compute_lens_scorecards(session, lookback_days=90, horizon="5d")
    assert cards == []  # no outcomes yet → no scorecards
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
pytest tests/test_lens_scorecards.py -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 4: Create `src/app/scoring/lens_scorecards.py`**

```python
"""Aggregate `LensSnapshot` × `LensOutcome` into per-lens accuracy scorecards.

Mirrors `CreatorScorecard` style: hit rate with Wilson 95% CI, plus regime
and conviction breakdowns. Used by the CLI / API surface; later phases
inject this into the judge's prompt for differentiated weighting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LensOutcome, LensSnapshot
from app.scoring.wilson import wilson_ci  # adapt import path to whatever the existing helper is


@dataclass
class LensScorecard:
    lens_name: str
    horizon: str
    n: int
    hit_rate: float
    hit_rate_lo: float
    hit_rate_hi: float
    avg_excess_vs_spy: float
    by_regime: dict[str, dict[str, float]] = field(default_factory=dict)
    by_conviction: dict[str, dict[str, float]] = field(default_factory=dict)
    by_direction: dict[str, dict[str, float]] = field(default_factory=dict)


def compute_lens_scorecards(
    session: Session,
    *,
    lookback_days: int = 90,
    horizon: str = "5d",
) -> list[LensScorecard]:
    """One LensScorecard per lens_name. Snapshots without matching outcome
    rows are excluded.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = session.execute(
        select(LensSnapshot, LensOutcome)
        .join(LensOutcome, LensOutcome.snapshot_id == LensSnapshot.id)
        .where(LensOutcome.horizon == horizon)
        .where(LensSnapshot.as_of >= cutoff)
    ).all()

    by_lens: dict[str, list[tuple[LensSnapshot, LensOutcome]]] = {}
    for snap, outc in rows:
        by_lens.setdefault(snap.lens_name, []).append((snap, outc))

    cards: list[LensScorecard] = []
    for lens_name, pairs in by_lens.items():
        n = len(pairs)
        hits = sum(1 for _, o in pairs if o.direction_correct)
        lo, hi = wilson_ci(hits, n)
        avg_excess = sum(o.excess_vs_spy_pct for _, o in pairs) / n if n else 0.0

        by_regime = _bucket(pairs, key=lambda p: p[1].regime)
        by_conviction = _bucket(pairs, key=lambda p: p[0].conviction)
        by_direction = _bucket(pairs, key=lambda p: p[0].direction)

        cards.append(
            LensScorecard(
                lens_name=lens_name,
                horizon=horizon,
                n=n,
                hit_rate=hits / n if n else 0.0,
                hit_rate_lo=lo,
                hit_rate_hi=hi,
                avg_excess_vs_spy=round(avg_excess, 4),
                by_regime=by_regime,
                by_conviction=by_conviction,
                by_direction=by_direction,
            )
        )
    return cards


def _bucket(
    pairs: list[tuple[LensSnapshot, LensOutcome]],
    key,
) -> dict[str, dict[str, float]]:
    """Group pairs by `key(pair)`; return {bucket_name: {n, hits, hit_rate, lo, hi}}."""
    buckets: dict[str, list[tuple[LensSnapshot, LensOutcome]]] = {}
    for p in pairs:
        buckets.setdefault(key(p), []).append(p)
    out: dict[str, dict[str, float]] = {}
    for k, group in buckets.items():
        n = len(group)
        hits = sum(1 for _, o in group if o.direction_correct)
        lo, hi = wilson_ci(hits, n)
        out[k] = {
            "n": n,
            "hits": hits,
            "hit_rate": hits / n if n else 0.0,
            "hit_rate_lo": lo,
            "hit_rate_hi": hi,
        }
    return out


__all__ = ["LensScorecard", "compute_lens_scorecards"]
```

If `app.scoring.wilson` doesn't exist with that name, find the actual location and update the import. Common alternatives: `app.scoring.wilson_ci`, `app.scoring.metrics`.

- [ ] **Step 5: Add CLI command `tsr lens-scorecards`**

In `src/app/cli.py`:

```python
@app.command("lens-scorecards")
def lens_scorecards_cmd(
    lookback_days: int = typer.Option(90, help="Lookback window in days."),
    horizon: str = typer.Option("5d", help="One of 1d, 3d, 5d, 21d."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of table."),
):
    """Print per-lens accuracy scorecards (Wilson CI + regime/conviction splits)."""
    import json as _json
    from app.db import session_scope
    from app.scoring.lens_scorecards import compute_lens_scorecards

    with session_scope() as session:
        cards = compute_lens_scorecards(session, lookback_days=lookback_days, horizon=horizon)

    if json_out:
        typer.echo(_json.dumps([card.__dict__ for card in cards], default=str, indent=2))
        return

    if not cards:
        typer.echo("(no lens scorecards — accumulate Deep runs first)")
        return

    for c in cards:
        typer.echo(f"{c.lens_name} ({c.horizon}, n={c.n})")
        typer.echo(f"  hit_rate: {c.hit_rate:.2%}  [{c.hit_rate_lo:.2%}, {c.hit_rate_hi:.2%}]")
        typer.echo(f"  avg_excess_vs_spy: {c.avg_excess_vs_spy:+.2%}")
        if c.by_regime:
            typer.echo(f"  by_regime: {c.by_regime}")
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_lens_scorecards.py -v
```

Expected: 3/3 PASS.

- [ ] **Step 7: Smoke CLI**

```bash
.venv/bin/tsr lens-scorecards
```

Expected: `(no lens scorecards — accumulate Deep runs first)` until real data accumulates.

- [ ] **Step 8: Commit**

```bash
git add src/app/scoring/lens_scorecards.py src/app/cli.py tests/test_lens_scorecards.py
git commit -m "feat(scoring): LensScorecard analytics with Wilson CI + regime split

Mirrors CreatorScorecard: per-lens hit rate at chosen horizon (default 5d),
Wilson 95% CI, plus regime / conviction / direction breakdowns. Snapshots
without matching outcome rows are excluded.

CLI: tsr lens-scorecards [--lookback-days N] [--horizon 5d] [--json]"
```

---

## Task 7 — API endpoint

**Files:**
- Modify: `apps/api/app/routes/research.py`

- [ ] **Step 1: Read existing research route patterns**

```bash
grep -n "@router.get\|@router.post" apps/api/app/routes/research.py | head -20
```

Note style — Pydantic response_models, Depends for sessions, etc.

- [ ] **Step 2: Add the endpoint**

Append to `apps/api/app/routes/research.py`:

```python
from app.scoring.lens_scorecards import LensScorecard, compute_lens_scorecards


@router.get("/lens-scorecards", response_model=list[dict])
def get_lens_scorecards(
    lookback_days: int = 90,
    horizon: str = "5d",
    db: Session = Depends(get_db),
):
    """Per-lens accuracy scorecards (Wilson CI, regime split, conviction split).

    Empty list until enough Deep/Quick runs accumulate. Phase 6 of the
    lens-agents roadmap injects these into the judge's prompt.
    """
    cards = compute_lens_scorecards(db, lookback_days=lookback_days, horizon=horizon)
    return [
        {
            "lens_name": c.lens_name,
            "horizon": c.horizon,
            "n": c.n,
            "hit_rate": c.hit_rate,
            "hit_rate_lo": c.hit_rate_lo,
            "hit_rate_hi": c.hit_rate_hi,
            "avg_excess_vs_spy": c.avg_excess_vs_spy,
            "by_regime": c.by_regime,
            "by_conviction": c.by_conviction,
            "by_direction": c.by_direction,
        }
        for c in cards
    ]
```

If the existing route file uses a different DB-session dependency name (e.g. `Session = Depends(session_scope)`), follow that.

- [ ] **Step 3: Smoke the endpoint**

In one terminal:
```bash
.venv/bin/uvicorn apps.api.app.main:app --reload --port 8001
```

In another:
```bash
curl -s http://localhost:8001/research/lens-scorecards | jq '.'
```

Expected: `[]` (empty until data accumulates).

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/routes/research.py
git commit -m "feat(api): GET /research/lens-scorecards endpoint

Returns per-lens accuracy scorecards. Empty list until Deep/Quick runs
accumulate at least one snapshot per lens with an elapsed horizon."
```

---

## Task 8 — Cron wiring

**Files:**
- Modify: `scripts/daily_run.sh`

- [ ] **Step 1: Read current cron pipeline**

```bash
cat scripts/daily_run.sh
```

Note the order of pipeline steps: typically `ingest → extract → backtest → score → aggregate-signals`.

- [ ] **Step 2: Add `tsr score-lens-outcomes` step**

Insert the new step between `tsr backtest` and `tsr score` (the natural place — after market data is fresh, before scorecards). Edit `scripts/daily_run.sh`:

```bash
# Compute realized lens outcomes for snapshots whose horizons elapsed.
# Best-effort — failure should not block the rest of the pipeline.
.venv/bin/tsr score-lens-outcomes --max-age-days 60 || \
  echo "warn: lens outcome scoring failed; continuing" >&2
```

- [ ] **Step 3: Verify shell syntax**

```bash
bash -n scripts/daily_run.sh
```

Expected: no output = clean syntax.

- [ ] **Step 4: Commit**

```bash
git add scripts/daily_run.sh
git commit -m "chore(cron): score lens outcomes nightly in daily_run.sh

Step is best-effort — a DB / market-data hiccup will not block ingest /
extract / backtest. Runs after backtest (market data fresh) and before
score (no dependency, just natural placement)."
```

---

## Task 9 — Backfill historical `ResearchPlan.lenses` (optional)

**Files:**
- Create: `scripts/backfill_lens_snapshots.py`

- [ ] **Step 1: Check whether any cached plans have lenses**

```bash
.venv/bin/python -c "
from app.db import session_scope
from app.models import ResearchPlan
with session_scope() as s:
    n_total = s.query(ResearchPlan).count()
    n_with_lenses = sum(1 for p in s.query(ResearchPlan).all() if p.plan_json and 'lenses' in p.plan_json)
    print(f'ResearchPlan total: {n_total}, with lenses: {n_with_lenses}')
"
```

If `n_with_lenses == 0`, **skip this entire task** — there's nothing to backfill. Commit nothing. Mark the task done with a one-line note in the commit log of Task 10.

- [ ] **Step 2 (only if there's data to backfill): write the script**

`scripts/backfill_lens_snapshots.py`:

```python
"""One-shot backfill: replay cached ResearchPlan.lenses → LensSnapshot rows.

Idempotent (record_lens_snapshots dedupes on the run-key). Run once after
deploying Phase 1; subsequent Deep/Quick runs record live.
"""

from __future__ import annotations

import json

from app.db import session_scope
from app.models import ResearchPlan
from app.research.lens_recording import record_lens_snapshots
from app.research.schema import LensView


def main():
    with session_scope() as session:
        plans = session.query(ResearchPlan).all()
        n_recorded = 0
        for plan in plans:
            if not plan.plan_json:
                continue
            payload = json.loads(plan.plan_json)
            lenses_raw = payload.get("lenses") or []
            if not lenses_raw:
                continue
            lenses = [
                LensView(
                    name=l["name"],
                    direction=l.get("direction", "neutral"),
                    conviction=l.get("conviction", "low"),
                    summary=l.get("summary", ""),
                    points=l.get("points", []),
                )
                for l in lenses_raw
            ]
            ids = record_lens_snapshots(
                session,
                plan_id=plan.id,
                ticker=plan.ticker,
                as_of=payload.get("as_of"),  # ResearchPlan.as_of also works
                mode=plan.mode or "quick",
                lenses=lenses,
                sources_used=payload.get("sources_used", []),
                durations_ms=[plan.duration_ms or 0] * len(lenses),
                costs_usd=[round((plan.cost_usd or 0) / max(len(lenses), 1), 6)] * len(lenses),
            )
            n_recorded += len(ids)
        print(f"Backfilled {n_recorded} lens snapshots from {len(plans)} cached plans.")


if __name__ == "__main__":
    main()
```

Adapt field accessors to whatever `ResearchPlan` actually exposes (the plan's persisted as_of may live on the row, on the JSON, or both).

- [ ] **Step 3: Run the backfill once**

```bash
.venv/bin/python scripts/backfill_lens_snapshots.py
```

Expected: a count > 0.

- [ ] **Step 4: Commit (only if data was actually backfilled)**

```bash
git add scripts/backfill_lens_snapshots.py
git commit -m "chore: backfill cached ResearchPlan.lenses → LensSnapshot rows

One-shot replay of historical Deep/Quick runs into the new
lens_snapshots table. Idempotent on (ticker, as_of, mode, lens_name)
so safe to re-run."
```

---

## Task 10 — Recent-changes documentation + final verification

**Files:**
- Modify: `docs/recent-changes-2026-05-08.md`

- [ ] **Step 1: Append §13**

Append to the end of `docs/recent-changes-2026-05-08.md`:

```markdown
## 13. Lens accuracy infrastructure (Phase 1 of agent roadmap)

Phase 1 of [docs/superpowers/plans/2026-05-08-lens-agents-roadmap.md](superpowers/plans/2026-05-08-lens-agents-roadmap.md).
Recording infrastructure for per-lens accuracy scoring — silent now,
unblocks Phase 6 (lens-weighted judge synthesis) once data accumulates.

**What landed:**

| Component | File |
|---|---|
| ORM tables `LensSnapshot` + `LensOutcome` | [src/app/models.py](../src/app/models.py) |
| Alembic migration | [alembic/versions/](../alembic/versions/) |
| Recording helper (idempotent on run-key) | [src/app/research/lens_recording.py](../src/app/research/lens_recording.py) |
| Wired into Deep + Quick runtimes | [src/app/research/deep.py](../src/app/research/deep.py), [src/app/research/quick.py](../src/app/research/quick.py) |
| Outcome computation @ 1d/3d/5d/21d horizons | [src/app/scoring/lens_outcomes.py](../src/app/scoring/lens_outcomes.py) |
| `LensScorecard` analytics (Wilson CI, regime split) | [src/app/scoring/lens_scorecards.py](../src/app/scoring/lens_scorecards.py) |
| CLI: `tsr score-lens-outcomes`, `tsr lens-scorecards` | [src/app/cli.py](../src/app/cli.py) |
| API: `GET /research/lens-scorecards` | [apps/api/app/routes/research.py](../apps/api/app/routes/research.py) |
| Cron wiring | [scripts/daily_run.sh](../scripts/daily_run.sh) |
| Backfill (one-shot) | [scripts/backfill_lens_snapshots.py](../scripts/backfill_lens_snapshots.py) |

**Behavioral note:** Phase 1 is recording-only. Runtime Deep/Quick decisions
are unchanged — the judge does not yet see the scorecards. That's Phase 6.

**Next:** Phase 2 (cross-lens debate round) and Phase 3 (watchlist scan
reranking) are the two cheapest follow-ups; both are independent of Phase 1
data accumulation.
```

- [ ] **Step 2: Final test sweep**

```bash
pytest -q 2>&1 | tail -3
alembic check
```

Expected: all green; `No new upgrade operations detected.`

- [ ] **Step 3: Commit and finish**

```bash
git add docs/recent-changes-2026-05-08.md
git commit -m "docs: §13 — lens accuracy infrastructure (Phase 1)

Recording-only; runtime decisions unchanged. Unblocks Phase 6 once
≥30 days of LensOutcome rows accumulate."
```

---

## Self-Review Notes

**Spec coverage** (against the goal stated at the top):
- LensSnapshot persistence: Tasks 1, 3, 4.
- LensOutcome computation: Task 5.
- LensScorecard analytics: Task 6.
- CLI surfacing: Tasks 5, 6.
- API surfacing: Task 7.
- Cron integration: Task 8.
- Optional backfill: Task 9.
- Doc update: Task 10.
- All recording is idempotent: covered by Tasks 3, 4 tests.
- Outcome computation is idempotent: covered by Task 5 test.
- Phase 1 does NOT change runtime decisions: confirmed in Tasks 3, 4 (recording is best-effort `try/except`).

**Placeholder check:** Every step has actual code blocks or shell commands. The one place I rely on the engineer to adapt is the existing `MarketReader` API in Task 5 — the plan tells them to read `market_reader.py` and adapt the adapter. That's necessary because I don't have the canonical signature in front of me; the plan can't make it up.

**Type consistency:** `LensSnapshot`, `LensOutcome`, `LensScorecard`, `MarketProvider`, `compute_lens_outcomes`, `compute_lens_scorecards`, `record_lens_snapshots`, `HORIZONS`, `direction_correct` — all consistently named across tasks. `mode` field is always `"quick" | "deep"`. `horizon` is always one of `"1d" | "3d" | "5d" | "21d"`.

**Risk:** Task 5 (outcome computation) requires the existing market-reader API to support a "give me ticker return over N trading days from `as_of`" call. If that's missing, Task 5's adapter can't be implemented as-shown — the engineer will need to either extend `market_reader.py` or write a small inline helper. Either is reasonable; flag it in the implementer's report if they hit this.
