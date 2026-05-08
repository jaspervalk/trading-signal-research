# Entry/Exit R/R Variance Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate run-to-run R/R variance in Quick + Deep entry/exit research, and reframe R/R as a *range* across deterministic candidate pairings (with the LLM's pick highlighted) rather than a single LLM-derived number.

**Architecture:**
- **A. Categorical picks.** Replace the LLM's free-form numeric `ZoneBand` output with categorical references (`entry_kind: "breakout"|"pullback"`, `primary_exit_index: int`, `runner_exit_index: int|null`, `invalidation_index: int`). All numbers come from `CandidateLevels`. The ±15% scaling clamp is removed.
- **B. R/R distribution.** A new `compute_rr_distribution()` helper enumerates all `(entry × primary × runner × invalidation)` combinations from `CandidateLevels`, returning `(min, median, max, n_combos, combos)`. Attached to every `EntryExitPlan` and rendered as a range bar in the UI.
- **C. Temperature=0.** Pass `temperature=0` to both `quick.run()` and `judge.run_judge()` model calls. (Analyst lenses keep default temperature — their job is divergent reads.)

**Tech Stack:** Python 3.11, Pydantic v2, Anthropic SDK (Haiku 4.5 + Sonnet 4.6), pytest, Next.js 14 / TypeScript / React for frontend.

**Out of scope:**
- Ensembling (option D) — defer until A+B+C are measured in production.
- Changes to `exits.py` candidate generation — still the source of truth.
- Changes to lens-panel agents (`technical/fundamental/sentiment/contrarian.py`) — they don't pick levels.
- Cache invalidation. Old plans without `r_r_distribution` will render without the range bar; new plans render with it. The `(ticker, mode, day_key)` key already rolls forward daily.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `src/app/research/schema.py` | modify | Add `RRCombo`, `RRDistribution`. Add `r_r_distribution` field to `EntryExitPlan`. Keep `ZoneBand` etc. unchanged. |
| `src/app/research/rr_distribution.py` | **new** | `compute_rr_distribution(candidates, chosen) → RRDistribution` — enumerates all combos and computes blended R/R for each. |
| `src/app/research/quick.py` | modify | New tool input schema (categorical). Drop `_clamp_zone` / `_clamp_value` / `_clamp_invalidation` / `LEVEL_EDIT_TOLERANCE`. New `_resolve_picks()`. Pass `temperature=0`. Populate `r_r_distribution`. |
| `src/app/research/agents/judge.py` | modify | Same shape change as `quick.py`. Pass `temperature=0`. Populate `r_r_distribution`. |
| `tests/test_research_quick.py` | modify | Drop the `_clamps_*` tests (the clamp is gone). Update mocks to emit categorical picks. Add `temperature=0` assertion. |
| `tests/test_research_deep.py` | modify | Same shape changes as quick tests. |
| `tests/test_research_rr_distribution.py` | **new** | Unit tests for the distribution helper. |
| `apps/web/src/lib/api.ts` | modify | Add `RRCombo` + `RRDistribution` TS types alongside `ResearchPlan`. |
| `apps/web/src/components/RRRangePanel.tsx` | **new** | Range bar component: shows min..max with chosen value marker. |
| `apps/web/src/components/EntryExitPanel.tsx` | modify | Mount `RRRangePanel` alongside the headline R/R number. |

---

## Task 1 — Pass `temperature=0` to Quick + Judge model calls

**Files:**
- Modify: `src/app/research/quick.py:258-271`
- Modify: `src/app/research/agents/judge.py:200-213`
- Modify: `tests/test_research_quick.py` (add temperature assertion)

- [ ] **Step 1: Write the failing test**

In `tests/test_research_quick.py`, add this test immediately after `test_quick_run_returns_validated_plan`:

```python
def test_quick_passes_temperature_zero_to_anthropic():
    """The Quick call MUST be temperature=0 so the same packet → same plan.

    R/R variance run-to-run was traced to default temperature (1.0). See
    docs/superpowers/plans/2026-05-08-rr-variance-fix.md.
    """
    packet = _stub_packet()
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "x",
        "primary_exit_index": 0,
        "primary_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["a"],
        "bear_case": ["a"],
        "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    client = _FakeClient(raw)
    run(packet, client=client)
    assert client.last_kwargs.get("temperature") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research_quick.py::test_quick_passes_temperature_zero_to_anthropic -v`
Expected: FAIL — either `KeyError`/`AssertionError` (temperature not in kwargs).

- [ ] **Step 3: Update `quick.run()` to pass `temperature=0`**

Edit `src/app/research/quick.py` `client.messages.create(...)` call (around line 258) to add `temperature=0`:

```python
response = client.messages.create(
    model=model,
    max_tokens=max_tokens,
    temperature=0,
    system=SYSTEM_PROMPT,
    tools=[
        {
            "name": QUICK_TOOL_NAME,
            "description": QUICK_TOOL_DESCRIPTION,
            "input_schema": quick_tool_input_schema(),
        }
    ],
    tool_choice={"type": "tool", "name": QUICK_TOOL_NAME},
    messages=[{"role": "user", "content": user_msg}],
)
```

NOTE: Step 3 will fail other tests because the test in step 1 references the new categorical schema (`entry_kind` etc.) — those don't exist yet. We're isolating the temperature change first; revert the test temporarily:

```python
def test_quick_passes_temperature_zero_to_anthropic():
    packet = _stub_packet()
    raw = {
        "entry_zone": {"low": 108.0, "high": 109.0, "method": "x", "rationale": "x"},
        "exit_zone_primary": {"low": 105.0, "high": 105.5, "method": "x", "rationale": "x"},
        "invalidation": 104.0,
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    client = _FakeClient(raw)
    run(packet, client=client)
    assert client.last_kwargs.get("temperature") == 0
```

(We will rewrite this test in Task 4 with the categorical schema.)

- [ ] **Step 4: Run the full Quick test file**

Run: `pytest tests/test_research_quick.py -v`
Expected: all tests PASS, including the new temperature assertion.

- [ ] **Step 5: Add same temperature change to Judge**

Edit `src/app/research/agents/judge.py` `client.messages.create(...)` call (around line 200) to add `temperature=0`:

```python
response = client.messages.create(
    model=model,
    max_tokens=max_tokens,
    temperature=0,
    system=SYSTEM_PROMPT,
    tools=[
        {
            "name": JUDGE_TOOL_NAME,
            "description": JUDGE_TOOL_DESCRIPTION,
            "input_schema": judge_tool_input_schema(),
        }
    ],
    tool_choice={"type": "tool", "name": JUDGE_TOOL_NAME},
    messages=[{"role": "user", "content": user_msg}],
)
```

- [ ] **Step 6: Add a parallel test for Deep mode**

In `tests/test_research_deep.py`, locate the existing happy-path test for the judge (it probably uses a `_FakeClient` similar to Quick). Add immediately after it:

```python
def test_judge_passes_temperature_zero_to_anthropic():
    """The judge call MUST be temperature=0 — see Task 1 of the R/R variance fix plan."""
    # Reuse whatever fixture pattern the existing judge tests use.
    # The assertion is: judge_client.last_kwargs.get("temperature") == 0
    ...
```

Inspect `tests/test_research_deep.py` first; copy the existing judge-call fixture exactly and insert the assertion. If the existing test infrastructure doesn't expose `last_kwargs`, extend the fake client minimally (mirror the Quick `_FakeClient` pattern).

- [ ] **Step 7: Run all research tests**

Run: `pytest tests/test_research_quick.py tests/test_research_deep.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/app/research/quick.py src/app/research/agents/judge.py tests/test_research_quick.py tests/test_research_deep.py
git commit -m "fix(research): pin temperature=0 on Quick + Judge for deterministic level picks

Run-to-run R/R variance traced to default Anthropic temperature (1.0).
Lens analysts keep default temperature — their job is divergent reads.
Refs: docs/superpowers/plans/2026-05-08-rr-variance-fix.md"
```

---

## Task 2 — Add `RRCombo` and `RRDistribution` schemas

**Files:**
- Modify: `src/app/research/schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_research_rr_distribution.py` with one test that just imports the new types — it will fail until they exist:

```python
"""Tests for compute_rr_distribution() and RRDistribution / RRCombo schemas.

The R/R distribution surfaces the *range* of plausible R/Rs across all
candidate combinations, so the user sees that the headline R/R is one
point in a distribution — not the answer.
"""
from app.research.schema import RRCombo, RRDistribution


def test_rr_distribution_schema_importable():
    combo = RRCombo(
        entry_kind="breakout",
        primary_index=0,
        runner_index=None,
        invalidation_index=0,
        entry_label="63d high band",
        primary_label="nearest resistance",
        runner_label=None,
        invalidation_label="nearest support",
        rr_primary=1.5,
        rr_runner=None,
        rr_blended=1.5,
        is_chosen=True,
    )
    dist = RRDistribution(
        min_rr=0.8, median_rr=1.5, max_rr=2.4, n_combos=12, combos=[combo]
    )
    assert dist.min_rr == 0.8
    assert dist.median_rr == 1.5
    assert dist.max_rr == 2.4
    assert dist.combos[0].is_chosen is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_research_rr_distribution.py::test_rr_distribution_schema_importable -v`
Expected: FAIL with `ImportError: cannot import name 'RRCombo'`.

- [ ] **Step 3: Add the schemas to `src/app/research/schema.py`**

Insert before the `EntryExitPlan` class (after `LensView`):

```python
class RRCombo(BaseModel):
    """One R/R combination across the candidate-level grid.

    A single `EntryExitPlan` may have ~10-30 of these. The LLM-chosen
    combo is marked `is_chosen=True`; all others are alternates the user
    can compare against to gauge the plan's sensitivity to level picks.
    """

    entry_kind: str  # "breakout" | "pullback"
    primary_index: int
    runner_index: int | None = None
    invalidation_index: int

    # Human-readable labels (derived from each ZoneBand.method / candidate).
    # Keeps the frontend from having to re-resolve indices into the candidate list.
    entry_label: str
    primary_label: str
    runner_label: str | None = None
    invalidation_label: str

    rr_primary: float
    rr_runner: float | None = None
    rr_blended: float

    is_chosen: bool = False


class RRDistribution(BaseModel):
    """The R/R distribution across all candidate combinations.

    `min_rr` / `max_rr` define the honest range of R/Rs supported by the
    deterministic candidates; `median_rr` is the center of mass. The chosen
    combo (LLM's pick) is one entry inside `combos` with `is_chosen=True`.
    """

    min_rr: float
    median_rr: float
    max_rr: float
    n_combos: int
    combos: list[RRCombo] = Field(default_factory=list)
```

Also add `r_r_distribution: RRDistribution | None = None` to `EntryExitPlan` (right after `plan_r_r_blended`):

```python
    plan_r_r_blended: float | None = None
    # The R/R range supported by the candidate-level grid. The headline
    # `plan_r_r_blended` is one entry within `r_r_distribution.combos`
    # marked `is_chosen=True`. Kept None for legacy plans (pre-2026-05-08
    # variance fix); always populated for new runs.
    r_r_distribution: RRDistribution | None = None
```

Update `__all__` to include `RRCombo` and `RRDistribution`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_research_rr_distribution.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/research/schema.py tests/test_research_rr_distribution.py
git commit -m "feat(research): add RRCombo + RRDistribution schemas

Surfaces the R/R range across candidate-level combinations so the
single-number headline R/R is contextualised. EntryExitPlan grows
optional r_r_distribution field; legacy cached plans render without it."
```

---

## Task 3 — Implement `compute_rr_distribution()`

**Files:**
- Create: `src/app/research/rr_distribution.py`
- Modify: `tests/test_research_rr_distribution.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_research_rr_distribution.py`:

```python
from app.research.rr_distribution import (
    Picks,
    compute_rr_distribution,
)
from app.research.schema import CandidateLevels, ZoneBand


def _candidates() -> CandidateLevels:
    """A small, fully-specified candidate grid — easy to enumerate by hand."""
    return CandidateLevels(
        breakout_entry=ZoneBand(low=108.0, high=109.0, method="63d high"),
        pullback_entry=ZoneBand(low=98.0, high=100.0, method="50-SMA"),
        primary_exit_candidates=[
            ZoneBand(low=105.0, high=105.5, method="nearest_resistance"),
            ZoneBand(low=112.0, high=112.5, method="63d high"),
        ],
        runner_exit_candidates=[
            ZoneBand(low=118.0, high=118.5, method="primary + 1.5×ATR"),
        ],
        invalidation_candidates=[92.0, 95.0],
        last_close=104.0,
        atr_14=2.0,
    )


def test_distribution_enumerates_all_combos():
    picks = Picks(
        entry_kind="breakout",
        primary_index=0,
        runner_index=0,
        invalidation_index=0,
    )
    dist = compute_rr_distribution(_candidates(), picks)
    # 2 entries × 2 primaries × (1 runner + None) × 2 invalidations = 16
    assert dist.n_combos == 16


def test_distribution_marks_chosen_combo():
    picks = Picks(
        entry_kind="pullback",
        primary_index=1,
        runner_index=0,
        invalidation_index=1,
    )
    dist = compute_rr_distribution(_candidates(), picks)
    chosen = [c for c in dist.combos if c.is_chosen]
    assert len(chosen) == 1
    assert chosen[0].entry_kind == "pullback"
    assert chosen[0].primary_index == 1
    assert chosen[0].runner_index == 0
    assert chosen[0].invalidation_index == 1


def test_distribution_min_max_median_match_combos():
    picks = Picks(
        entry_kind="breakout",
        primary_index=0,
        runner_index=None,
        invalidation_index=0,
    )
    dist = compute_rr_distribution(_candidates(), picks)
    rrs = sorted(c.rr_blended for c in dist.combos)
    assert dist.min_rr == rrs[0]
    assert dist.max_rr == rrs[-1]
    # Median for even-length list = average of middle two, rounded to 2dp.
    mid = len(rrs) // 2
    expected_median = round((rrs[mid - 1] + rrs[mid]) / 2, 2)
    assert dist.median_rr == expected_median


def test_distribution_handles_no_runner():
    picks = Picks(
        entry_kind="breakout",
        primary_index=0,
        runner_index=None,
        invalidation_index=0,
    )
    cl = _candidates()
    cl.runner_exit_candidates = []
    dist = compute_rr_distribution(cl, picks)
    # 2 entries × 2 primaries × 2 invalidations = 8
    assert dist.n_combos == 8
    for c in dist.combos:
        assert c.runner_index is None
        assert c.runner_label is None
        assert c.rr_runner is None


def test_distribution_skips_combos_with_invalid_risk():
    """If `entry.low - invalidation <= 0` the combo is skipped (R/R undefined)."""
    cl = CandidateLevels(
        breakout_entry=ZoneBand(low=100.0, high=101.0, method="x"),
        primary_exit_candidates=[ZoneBand(low=110.0, high=111.0, method="x")],
        invalidation_candidates=[95.0, 105.0],  # 105.0 is ABOVE entry.low → skip
        last_close=99.0,
        atr_14=2.0,
    )
    picks = Picks(
        entry_kind="breakout",
        primary_index=0,
        runner_index=None,
        invalidation_index=0,
    )
    dist = compute_rr_distribution(cl, picks)
    assert dist.n_combos == 1  # only the 95.0 invalidation produces a valid R/R
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_research_rr_distribution.py -v`
Expected: FAIL with `ImportError: cannot import name 'Picks'` (and `compute_rr_distribution`).

- [ ] **Step 3: Create `src/app/research/rr_distribution.py`**

```python
"""Compute the full R/R distribution across candidate-level combinations.

Used by both Quick and Deep modes after the LLM has emitted its categorical
picks. The point: surface the R/R *range* the deterministic grid supports,
so the headline R/R is one point in a distribution rather than 'the answer'.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.research.schema import (
    CandidateLevels,
    RRCombo,
    RRDistribution,
    ZoneBand,
    blended_risk_reward,
)


@dataclass
class Picks:
    """The LLM's categorical picks, used to mark the chosen combo."""

    entry_kind: str  # "breakout" | "pullback"
    primary_index: int
    runner_index: int | None
    invalidation_index: int


def compute_rr_distribution(
    candidates: CandidateLevels, picks: Picks
) -> RRDistribution:
    """Enumerate every (entry × primary × runner × invalidation) combo.

    For each combo, compute `rr_primary`, `rr_runner` (None if no runner),
    and `rr_blended` via the same blended formula as the headline plan.
    Skip combos where `entry.low - invalidation <= 0` (R/R undefined).
    """
    entries = _entries(candidates)
    primaries = candidates.primary_exit_candidates
    runners: list[ZoneBand | None] = list(candidates.runner_exit_candidates) or []
    runners_with_none: list[tuple[int | None, ZoneBand | None]] = [
        (i, r) for i, r in enumerate(runners)
    ]
    runners_with_none.append((None, None))
    invalidations = candidates.invalidation_candidates

    combos: list[RRCombo] = []
    for entry_kind, entry in entries:
        for p_idx, primary in enumerate(primaries):
            for r_idx, runner in runners_with_none:
                for inv_idx, inv in enumerate(invalidations):
                    risk = entry.low - inv
                    if risk <= 0:
                        continue
                    rr_p = _rr(entry, primary, inv)
                    rr_r = _rr(entry, runner, inv) if runner is not None else None
                    rr_b = blended_risk_reward(rr_primary=rr_p, rr_runner=rr_r)
                    if rr_b is None:
                        continue
                    combos.append(
                        RRCombo(
                            entry_kind=entry_kind,
                            primary_index=p_idx,
                            runner_index=r_idx,
                            invalidation_index=inv_idx,
                            entry_label=entry.method,
                            primary_label=primary.method,
                            runner_label=runner.method if runner else None,
                            invalidation_label=f"{inv:.2f}",
                            rr_primary=rr_p,
                            rr_runner=rr_r,
                            rr_blended=rr_b,
                            is_chosen=(
                                entry_kind == picks.entry_kind
                                and p_idx == picks.primary_index
                                and r_idx == picks.runner_index
                                and inv_idx == picks.invalidation_index
                            ),
                        )
                    )

    if not combos:
        return RRDistribution(min_rr=0.0, median_rr=0.0, max_rr=0.0, n_combos=0, combos=[])

    rrs = sorted(c.rr_blended for c in combos)
    return RRDistribution(
        min_rr=rrs[0],
        median_rr=_median(rrs),
        max_rr=rrs[-1],
        n_combos=len(combos),
        combos=combos,
    )


def _entries(candidates: CandidateLevels) -> list[tuple[str, ZoneBand]]:
    out: list[tuple[str, ZoneBand]] = []
    if candidates.breakout_entry is not None:
        out.append(("breakout", candidates.breakout_entry))
    if candidates.pullback_entry is not None:
        out.append(("pullback", candidates.pullback_entry))
    return out


def _rr(entry: ZoneBand, exit_: ZoneBand, invalidation: float) -> float:
    risk = entry.low - invalidation
    if risk <= 0:
        return 0.0
    reward = exit_.low - entry.high
    if reward <= 0:
        return 0.0
    return round(reward / risk, 2)


def _median(values: list[float]) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return round(values[n // 2], 2)
    return round((values[n // 2 - 1] + values[n // 2]) / 2, 2)


__all__ = ["Picks", "compute_rr_distribution"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_research_rr_distribution.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/research/rr_distribution.py tests/test_research_rr_distribution.py
git commit -m "feat(research): compute_rr_distribution() enumerates candidate-grid R/Rs

Returns min/median/max + every (entry × primary × runner × invalidation)
combo, with the LLM-chosen pick marked. Skips combos with non-positive
risk (entry.low <= invalidation). Used by Quick + Deep modes."
```

---

## Task 4 — Quick mode: replace numeric tool input with categorical picks

**Files:**
- Modify: `src/app/research/quick.py`
- Modify: `tests/test_research_quick.py`

- [ ] **Step 1: Update the failing tests in `tests/test_research_quick.py`**

Replace the `_FakeClient` fixture's `raw` payloads in **all** test functions to use the new categorical schema. Delete the three clamp-specific tests that no longer apply (the clamp is gone) and replace them with two new picks-resolution tests. Updated test file should look like this for the relevant happy-path test:

```python
def test_quick_run_returns_validated_plan():
    packet = _stub_packet()
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "Breakout above 63d pivot.",
        "primary_exit_index": 0,
        "primary_exit_rationale": "First test of overhead supply.",
        "invalidation_index": 0,
        "invalidation_rationale": "Below entry support.",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["bull a"],
        "bear_case": ["bear a"],
        "key_risks": ["risk a"],
        "lenses": _stub_lenses(),
    }
    client = _FakeClient(raw)
    result = run(packet, client=client)
    assert result.plan.ticker == "AAPL"
    # Numeric levels are pulled VERBATIM from CandidateLevels — no clamp.
    assert result.plan.entry_zone == packet.candidate_levels.breakout_entry
    assert result.plan.exit_zone_primary == packet.candidate_levels.primary_exit_candidates[0]
    assert result.plan.invalidation == packet.candidate_levels.invalidation_candidates[0]
    assert result.plan.confidence == "high"
    assert result.plan.timeframe == "5-15d"
    assert result.plan.mode == "quick"
    assert result.plan.r_r_distribution is not None
    assert result.plan.r_r_distribution.n_combos > 0
    chosen = [c for c in result.plan.r_r_distribution.combos if c.is_chosen]
    assert len(chosen) == 1
```

Replace the three `test_quick_clamps_*` tests with these two:

```python
def test_quick_invalid_entry_kind_falls_back_to_first_available():
    """If the LLM picks an entry_kind that isn't available, fall back to
    whichever entry candidate IS available — no exception."""
    packet = _stub_packet()
    # Force only pullback to be available.
    packet.candidate_levels.breakout_entry = None
    raw = {
        "entry_kind": "breakout",  # not available!
        "entry_rationale": "x",
        "primary_exit_index": 0,
        "primary_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    assert plan.entry_zone == packet.candidate_levels.pullback_entry


def test_quick_out_of_range_primary_index_clamps_to_last():
    """LLM emits an out-of-range index → clamp to the nearest valid index."""
    packet = _stub_packet()
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "x",
        "primary_exit_index": 99,  # out of range
        "primary_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    plan = run(packet, client=_FakeClient(raw)).plan
    last = packet.candidate_levels.primary_exit_candidates[-1]
    assert plan.exit_zone_primary == last
```

Update remaining tests (confidence-bounded, etc.) to use the new categorical raw payloads. Delete `LEVEL_EDIT_TOLERANCE` from the import line — it's gone.

Also update the temperature test from Task 1 to use the categorical schema:

```python
def test_quick_passes_temperature_zero_to_anthropic():
    packet = _stub_packet()
    raw = {
        "entry_kind": "breakout",
        "entry_rationale": "x",
        "primary_exit_index": 0,
        "primary_exit_rationale": "x",
        "invalidation_index": 0,
        "invalidation_rationale": "x",
        "confidence": "high",
        "timeframe": "5-15d",
        "bull_case": ["a"], "bear_case": ["a"], "key_risks": ["a"],
        "lenses": _stub_lenses(),
    }
    client = _FakeClient(raw)
    run(packet, client=client)
    assert client.last_kwargs.get("temperature") == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_research_quick.py -v`
Expected: FAIL — the tool input schema mismatch will surface as `KeyError: 'entry_zone'` from `_build_plan`, plus assertion failures for `r_r_distribution`.

- [ ] **Step 3: Rewrite `quick.py` tool input schema and parsing**

Replace `quick_tool_input_schema()` in `src/app/research/quick.py`:

```python
def quick_tool_input_schema() -> dict[str, Any]:
    """Strict schema. The LLM picks levels by *reference* — entry by kind,
    exits and invalidation by index into the candidate lists supplied in
    the user message. No raw numbers; no scaling. R/R variance run-to-run
    is eliminated for the same packet at temperature=0.
    """
    lens = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "direction", "conviction", "summary", "points"],
        "properties": {
            "name": {"type": "string", "enum": list(LENS_NAMES)},
            "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
            "conviction": {"type": "string", "enum": list(CONFIDENCE)},
            "summary": {"type": "string"},
            "points": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 4,
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "entry_kind",
            "entry_rationale",
            "primary_exit_index",
            "primary_exit_rationale",
            "invalidation_index",
            "invalidation_rationale",
            "confidence",
            "timeframe",
            "bull_case",
            "bear_case",
            "key_risks",
            "lenses",
        ],
        "properties": {
            "entry_kind": {"type": "string", "enum": ["breakout", "pullback"]},
            "entry_rationale": {"type": "string"},
            "primary_exit_index": {"type": "integer", "minimum": 0},
            "primary_exit_rationale": {"type": "string"},
            "runner_exit_index": {"type": "integer", "minimum": 0},
            "runner_exit_rationale": {"type": "string"},
            "invalidation_index": {"type": "integer", "minimum": 0},
            "invalidation_rationale": {"type": "string"},
            "confidence": {"type": "string", "enum": list(CONFIDENCE)},
            "timeframe": {"type": "string", "enum": list(TIMEFRAMES)},
            "bull_case": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
            "bear_case": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
            "key_risks": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
            "lenses": {"type": "array", "items": lens, "minItems": 4, "maxItems": 4},
            "note": {"type": "string"},
        },
    }
```

Update `QUICK_TOOL_DESCRIPTION`:

```python
QUICK_TOOL_DESCRIPTION = (
    "Submit an entry/exit research plan. Pick levels by REFERENCE: "
    "`entry_kind` is 'breakout' or 'pullback' (matching the candidate_levels "
    "block in the user message); `primary_exit_index`, `runner_exit_index`, "
    "and `invalidation_index` are 0-based indices into the corresponding "
    "candidate lists. Provide a short rationale per pick. You do NOT emit "
    "raw price numbers — the system resolves your picks against the "
    "deterministic candidates."
)
```

Update `SYSTEM_PROMPT` numeric-level rule (replace rule #1):

```python
1. NUMERIC LEVELS: You DO NOT emit raw prices. Pick by reference: \
   `entry_kind` is 'breakout' or 'pullback'; `primary_exit_index`, \
   `runner_exit_index` (optional), `invalidation_index` are 0-based \
   indices into the candidate_levels lists in the user message. The \
   system resolves your picks against the deterministic candidates.
```

Replace `_build_plan()` and remove `_clamp_zone`, `_clamp_value`, `_clamp_invalidation`, `_nearest_candidate`, `_all_entry_candidates`, `LEVEL_EDIT_TOLERANCE`, `MIN_RISK_ATR_MULTIPLE`, `_risk_reward`. The new `_build_plan`:

```python
def _build_plan(
    *,
    packet: ResearchPacket,
    raw: dict[str, Any],
    cost_usd: float,
    duration_ms: int,
) -> EntryExitPlan:
    """Resolve the LLM's categorical picks into the full plan."""
    cl = packet.candidate_levels
    picks = _resolve_picks(raw, cl)

    entry_zone = _entry_zone(cl, picks.entry_kind)
    exit_zone_primary = cl.primary_exit_candidates[picks.primary_index]
    exit_zone_runner = (
        cl.runner_exit_candidates[picks.runner_index]
        if picks.runner_index is not None
        else None
    )
    invalidation = float(cl.invalidation_candidates[picks.invalidation_index])

    confidence = _bound_confidence(
        raw_confidence=raw["confidence"],
        rubric_confidence=packet.view.status.confidence,
    )

    rr_primary = _rr(entry_zone, exit_zone_primary, invalidation)
    rr_runner = (
        _rr(entry_zone, exit_zone_runner, invalidation)
        if exit_zone_runner is not None
        else None
    )
    rr_blended = blended_risk_reward(rr_primary=rr_primary, rr_runner=rr_runner)

    distribution = compute_rr_distribution(cl, picks)

    lenses = _build_lenses(raw.get("lenses"))

    note = AgentNote(
        agent="quick",
        confidence=confidence,
        bull_points=list(raw.get("bull_case", [])),
        bear_points=list(raw.get("bear_case", [])),
        note=raw.get("note", ""),
    )

    sources = list(packet.sources_used)
    if (
        packet.view.valuation.forward_pe is not None
        or packet.view.valuation.market_cap is not None
    ):
        sources.append("fundamentals")

    return EntryExitPlan(
        ticker=packet.ticker,
        as_of=packet.as_of,
        entry_zone=entry_zone,
        pullback_entry_zone=None,  # one entry per plan now
        exit_zone_primary=exit_zone_primary,
        exit_zone_runner=exit_zone_runner,
        invalidation=invalidation,
        risk_reward_primary=rr_primary,
        risk_reward_runner=rr_runner,
        plan_r_r_blended=rr_blended,
        r_r_distribution=distribution,
        confidence=confidence,
        timeframe=raw["timeframe"],
        bull_case=list(raw.get("bull_case", [])),
        bear_case=list(raw.get("bear_case", [])),
        key_risks=list(raw.get("key_risks", [])),
        lenses=lenses,
        mode="quick",
        cost_usd=round(cost_usd, 6),
        duration_ms=duration_ms,
        sources_used=sources,
        agent_trace=[note],
    )


def _resolve_picks(raw: dict[str, Any], cl: Any) -> Picks:
    """Validate + clamp categorical picks. Falls back to whatever's available."""
    entry_kind = raw.get("entry_kind", "breakout")
    if entry_kind not in ("breakout", "pullback"):
        entry_kind = "breakout"
    # If the chosen kind isn't available, fall back to the other.
    if entry_kind == "breakout" and cl.breakout_entry is None:
        entry_kind = "pullback"
    elif entry_kind == "pullback" and cl.pullback_entry is None:
        entry_kind = "breakout"
    if (
        cl.breakout_entry is None and cl.pullback_entry is None
    ):
        raise RuntimeError(
            "No entry candidates available; cannot build plan."
        )

    primary_idx = _clamp_index(raw.get("primary_exit_index", 0), len(cl.primary_exit_candidates))
    runner_raw = raw.get("runner_exit_index")
    runner_idx: int | None
    if runner_raw is None or not cl.runner_exit_candidates:
        runner_idx = None
    else:
        runner_idx = _clamp_index(runner_raw, len(cl.runner_exit_candidates))
    inv_idx = _clamp_index(raw.get("invalidation_index", 0), len(cl.invalidation_candidates))

    return Picks(
        entry_kind=entry_kind,
        primary_index=primary_idx,
        runner_index=runner_idx,
        invalidation_index=inv_idx,
    )


def _entry_zone(cl: Any, entry_kind: str) -> ZoneBand:
    if entry_kind == "breakout" and cl.breakout_entry is not None:
        return cl.breakout_entry
    if cl.pullback_entry is not None:
        return cl.pullback_entry
    raise RuntimeError("No matching entry candidate")


def _clamp_index(value: int, n: int) -> int:
    if n <= 0:
        raise RuntimeError("No candidates to pick from.")
    return max(0, min(int(value), n - 1))


def _rr(entry: ZoneBand, exit_: ZoneBand, invalidation: float) -> float:
    risk = entry.low - invalidation
    if risk <= 0:
        return 0.0
    reward = exit_.low - entry.high
    if reward <= 0:
        return 0.0
    return round(reward / risk, 2)
```

Add new imports at the top of `quick.py`:

```python
from app.research.rr_distribution import Picks, compute_rr_distribution
```

Remove `LEVEL_EDIT_TOLERANCE` and `MIN_RISK_ATR_MULTIPLE` from `__all__` if listed (they aren't currently, but verify).

Update `_format_user_message` to include numbered candidate indices in the prompt — the LLM needs to know which index is which:

In `_format_candidate_levels`, replace the existing implementation:

```python
def _format_candidate_levels(p: ResearchPacket) -> str:
    cl = p.candidate_levels
    parts: list[str] = []
    if cl.breakout_entry:
        parts.append(f"- entry_kind=breakout: {_format_zone(cl.breakout_entry)}")
    if cl.pullback_entry:
        parts.append(f"- entry_kind=pullback: {_format_zone(cl.pullback_entry)}")
    if cl.primary_exit_candidates:
        parts.append("- primary_exit_candidates (pick by primary_exit_index):")
        for i, z in enumerate(cl.primary_exit_candidates):
            parts.append(f"    [{i}] {_format_zone(z)}")
    if cl.runner_exit_candidates:
        parts.append("- runner_exit_candidates (pick by runner_exit_index, optional):")
        for i, z in enumerate(cl.runner_exit_candidates):
            parts.append(f"    [{i}] {_format_zone(z)}")
    if cl.invalidation_candidates:
        parts.append("- invalidation_candidates (pick by invalidation_index):")
        for i, x in enumerate(cl.invalidation_candidates):
            parts.append(f"    [{i}] {x:.2f}")
    return "\n".join(parts) if parts else "(no deterministic candidates available)"
```

Update `USER_TEMPLATE` to remove the obsolete "±15% scaling allowed" sentence; replace the `CANDIDATE LEVELS` header line:

```python
CANDIDATE LEVELS (pick BY INDEX / KIND — the system resolves your picks):
{candidate_levels_block}
```

- [ ] **Step 4: Run all Quick tests**

Run: `pytest tests/test_research_quick.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/research/quick.py tests/test_research_quick.py
git commit -m "feat(research): Quick mode picks levels by index/kind, not raw numbers

LLM emits entry_kind ('breakout'|'pullback'), primary_exit_index,
runner_exit_index, invalidation_index — system resolves against
CandidateLevels. ±15% scaling clamp removed; min-risk-distance guard
removed (no longer reachable since picks are verbatim). plan_r_r is
deterministic given a packet at temperature=0.

EntryExitPlan now carries r_r_distribution: the full grid of
(entry × primary × runner × invalidation) R/Rs, with the chosen combo
marked. Honest range vs single-number headline."
```

---

## Task 5 — Deep mode (Judge): same categorical change

**Files:**
- Modify: `src/app/research/agents/judge.py`
- Modify: `tests/test_research_deep.py`

- [ ] **Step 1: Read `tests/test_research_deep.py` to understand the existing test fixture pattern**

Run: `cat tests/test_research_deep.py | head -200` — understand the `_FakeClient` style and how the judge mock is wired. Note the exact pattern for stubbing the judge response (which uses tool name `submit_synthesis`, not `submit_entry_exit_plan`).

- [ ] **Step 2: Update tests to expect categorical schema**

Same shape as Task 4 step 1 but for the judge tool input. The judge schema is *almost* the same — minus the `lenses` block (lenses pass through from the analysts). New raw payload shape:

```python
{
    "entry_kind": "breakout",
    "entry_rationale": "...",
    "primary_exit_index": 0,
    "primary_exit_rationale": "...",
    "invalidation_index": 0,
    "invalidation_rationale": "...",
    "confidence": "medium",
    "timeframe": "5-15d",
    "bull_case": [...],
    "bear_case": [...],
    "key_risks": [...],
}
```

Update each test in `tests/test_research_deep.py` that mocks the judge response. Drop any tests that specifically test the ±15% clamp on the judge — they're obsolete.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_research_deep.py -v`
Expected: FAIL with `KeyError: 'entry_zone'` from the existing `_build_plan` in judge.py.

- [ ] **Step 4: Rewrite `judge.py` schema, prompt, and `_build_plan`**

Mirror the Quick changes:

1. Update `judge_tool_input_schema()` to use categorical fields (same shape as Quick minus the lenses block).
2. Update `JUDGE_TOOL_DESCRIPTION` and the `SYSTEM_PROMPT` numeric-level rule.
3. Update `_format_candidate_levels` (in judge.py) to emit numbered indices, same as Quick.
4. Replace `_build_plan` to use `_resolve_picks` (copy the helper into judge.py — a small duplication is fine for now, vs. introducing a new shared module).
5. Remove `_clamp_zone` / `_clamp_value` / `_clamp_invalidation` / `_all_entry_candidates` / `LEVEL_EDIT_TOLERANCE` / `MIN_RISK_ATR_MULTIPLE` / `_risk_reward`.
6. Add import: `from app.research.rr_distribution import Picks, compute_rr_distribution`.
7. Populate `r_r_distribution` on the returned `EntryExitPlan`.
8. Set `pullback_entry_zone=None` (single entry per plan; matches Quick).

The implementation is a near-clone of Task 4 step 3 but without lenses (those come from `analyst_results` and are passed through into `EntryExitPlan.lenses`). Use the exact same `_resolve_picks`, `_entry_zone`, `_clamp_index`, `_rr` helpers.

- [ ] **Step 5: Run the Deep tests**

Run: `pytest tests/test_research_deep.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the FULL research test suite**

Run: `pytest tests/test_research_*.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the FULL test suite to catch any unrelated regression**

Run: `pytest`
Expected: 352+ tests PASS (we added new tests; subtract removed clamp tests = roughly net-positive).

- [ ] **Step 8: Commit**

```bash
git add src/app/research/agents/judge.py tests/test_research_deep.py
git commit -m "feat(research): Deep judge picks levels by index/kind, mirrors Quick

Same categorical schema as Quick mode; same Picks resolution; same
r_r_distribution attached to the synthesised EntryExitPlan. R/R run
variance for a given packet now bounded by the index-pick step alone
(temperature=0 from Task 1)."
```

---

## Task 6 — Frontend: render `RRDistribution` as a range bar

**Files:**
- Modify: `apps/web/src/lib/api.ts`
- Create: `apps/web/src/components/RRRangePanel.tsx`
- Modify: `apps/web/src/components/EntryExitPanel.tsx`

- [ ] **Step 1: Add TS types in `apps/web/src/lib/api.ts`**

Locate the existing `ResearchPlan` type (or whatever name `EntryExitPlan` is exposed as in TS). Add adjacent:

```ts
export type RRCombo = {
  entry_kind: "breakout" | "pullback";
  primary_index: number;
  runner_index: number | null;
  invalidation_index: number;
  entry_label: string;
  primary_label: string;
  runner_label: string | null;
  invalidation_label: string;
  rr_primary: number;
  rr_runner: number | null;
  rr_blended: number;
  is_chosen: boolean;
};

export type RRDistribution = {
  min_rr: number;
  median_rr: number;
  max_rr: number;
  n_combos: number;
  combos: RRCombo[];
};
```

Add `r_r_distribution: RRDistribution | null;` to the `ResearchPlan` (or equivalent) type.

- [ ] **Step 2: Create `RRRangePanel.tsx`**

```tsx
"use client";

import { RRDistribution } from "@/lib/api";

type Props = {
  distribution: RRDistribution | null;
  chosenRR: number | null;
};

/**
 * Range bar showing min..max R/R across all candidate combinations,
 * with the chosen R/R marked. Honest about how much the headline
 * single-number R/R depends on which level pairing was picked.
 */
export function RRRangePanel({ distribution, chosenRR }: Props) {
  if (!distribution || distribution.n_combos === 0) {
    return null;
  }
  const { min_rr, median_rr, max_rr, n_combos } = distribution;
  const span = Math.max(max_rr - min_rr, 0.01);
  const chosenPct =
    chosenRR === null ? null : ((chosenRR - min_rr) / span) * 100;
  const medianPct = ((median_rr - min_rr) / span) * 100;

  return (
    <div className="text-[11px] font-mono text-muted-2 space-y-1">
      <div className="flex items-baseline justify-between">
        <span className="uppercase tracking-wider">R/R range</span>
        <span>
          {min_rr.toFixed(2)} – {max_rr.toFixed(2)} ·{" "}
          <span className="text-muted">median {median_rr.toFixed(2)}</span> ·{" "}
          <span className="text-muted">n={n_combos}</span>
        </span>
      </div>
      <div className="relative h-2 bg-zinc-800 rounded">
        <div
          className="absolute top-0 h-2 w-px bg-zinc-500"
          style={{ left: `${medianPct}%` }}
          title={`median R/R ${median_rr.toFixed(2)}`}
        />
        {chosenPct !== null && (
          <div
            className="absolute top-[-2px] h-3 w-1 bg-amber-400"
            style={{ left: `${chosenPct}%` }}
            title={`chosen R/R ${chosenRR?.toFixed(2)}`}
          />
        )}
      </div>
      <div className="text-[10px] opacity-70">
        Chosen R/R is one of {n_combos} candidate-level pairings; range shown for context.
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Mount `RRRangePanel` in `EntryExitPanel.tsx`**

Locate the existing `plan_r_r_blended` headline render in `EntryExitPanel.tsx`. Immediately below the headline R/R number, render:

```tsx
<RRRangePanel
  distribution={plan.r_r_distribution}
  chosenRR={plan.plan_r_r_blended}
/>
```

Add the import at the top:

```tsx
import { RRRangePanel } from "@/components/RRRangePanel";
```

- [ ] **Step 4: Verify the dev build**

Run: `cd apps/web && npm run typecheck` (or `tsc --noEmit`)
Expected: PASS — no type errors.

Run the dev server (`npm run dev`), navigate to a ticker that has a Quick or Deep plan in cache, and confirm the range bar renders. If no cached plan exists, generate a Quick plan via the UI.

Manual UI checks:
- Range bar visible below the Plan R/R headline.
- Chosen R/R marker (amber) appears within the range bar at the correct position.
- For an old cached plan with `r_r_distribution = null`, the panel renders nothing (no crash).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/api.ts apps/web/src/components/RRRangePanel.tsx apps/web/src/components/EntryExitPanel.tsx
git commit -m "feat(web): RRRangePanel surfaces R/R range across candidate pairings

Shows min..max with chosen-R/R marker and median tick. Renders nothing
on legacy plans with r_r_distribution=null. Honest framing for the
plan_r_r_blended headline number."
```

---

## Task 7 — Smoke verification (live API, no commit)

**Files:** none.

- [ ] **Step 1: Quick mode determinism check**

Run twice on the same ticker (must hit the API; force=true bypasses cache):

```bash
curl -X POST http://localhost:8000/research/quick/AAPL?force=true | jq '.plan_r_r_blended, .r_r_distribution.min_rr, .r_r_distribution.max_rr, .r_r_distribution.n_combos'
curl -X POST http://localhost:8000/research/quick/AAPL?force=true | jq '.plan_r_r_blended, .r_r_distribution.min_rr, .r_r_distribution.max_rr, .r_r_distribution.n_combos'
```

Expected: identical `plan_r_r_blended` across both runs (temperature=0 + categorical picks). Range and combo count identical (both derived deterministically from `CandidateLevels`).

- [ ] **Step 2: Deep mode stability check**

Run twice on the same ticker:

```bash
curl -X POST http://localhost:8000/research/deep/AAPL?force=true | jq '.plan_r_r_blended, .r_r_distribution.min_rr, .r_r_distribution.max_rr'
curl -X POST http://localhost:8000/research/deep/AAPL?force=true | jq '.plan_r_r_blended, .r_r_distribution.min_rr, .r_r_distribution.max_rr'
```

Expected: identical or near-identical (analyst lenses still vary at default temperature; if they vary enough to flip a directional read, the judge may pick a different combo. But within an analyst-lens fixture, the judge is deterministic.)

If `plan_r_r_blended` varies meaningfully run-to-run, investigate whether the lens-direction differences are flipping the judge's pick. That's a *real* signal (variance in the underlying analysis), not the bug we set out to fix.

- [ ] **Step 3: Update `docs/recent-changes-2026-05-08.md`**

Append a new section:

```markdown
## 12. R/R variance fix (later 2026-05-08)

After observing that two Deep runs on AAPL produced R/R 1.77 then 0.80,
diagnosed the variance to: (a) default Anthropic temperature 1.0,
(b) ±15% scaling clamp letting the LLM emit any number near a candidate,
(c) free-form numeric output across three independent levels compounding.

Fix: A+B+C from `docs/superpowers/plans/2026-05-08-rr-variance-fix.md`.
- A: LLM picks levels by reference (`entry_kind`, `*_index`); raw numbers come from `CandidateLevels`. ±15% clamp removed.
- B: New `RRDistribution` enumerates every (entry × primary × runner × invalidation) R/R combo; UI shows the range alongside the headline.
- C: `temperature=0` on Quick + Judge calls.

Result: same packet → same plan; the plan_r_r_blended headline is now
contextualised by an honest min..max range.
```

(No commit yet — leave the doc edit unstaged for the user to review.)

---

## Self-Review Notes

**Spec coverage** (against the four ideas A+B+C from the conversation):
- A (kill ±15% scaling): Tasks 4-5.
- B (R/R distribution): Tasks 2-3, frontend in 6.
- C (temperature=0): Task 1.
- Verification: Task 7.

**Placeholder check:** No "TBD"/"add appropriate error handling" — every step has a concrete code block or shell command.

**Type consistency:** `Picks`, `RRCombo`, `RRDistribution` defined in Task 2-3; consumed in Tasks 4-5 (`_resolve_picks`, `compute_rr_distribution`). Names match across tasks. `entry_kind` strings (`"breakout"`, `"pullback"`) consistent.

**Risk:** Task 5 step 2 is the riskiest — Deep tests are heavier and the partial-failure paths need the same categorical update. If the existing tests have judge-specific clamp tests, they may need restructuring rather than just deletion. Read first, edit second.
