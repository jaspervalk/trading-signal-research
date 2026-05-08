# Cross-Lens Debate Round Implementation Plan (Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute task-by-task.

**Goal:** After the four Deep-mode analysts emit independent reads (round 1), give each analyst a *second* turn where they see the other three lenses' summaries and may revise their direction / conviction / summary / points. The judge then sees the revised reads where they exist; original reads where they don't. Toggle behind a config flag; default off until cost / behavior are validated.

**Architecture:**
- `LensView` gains optional `revised_summary`, `revised_points`, `responded_to` fields. Original `summary` / `points` / `direction` / `conviction` stay; revised fields are *additions*, not replacements.
- One new prompt template (per analyst module) for the revision call. Reuses the existing `submit_lens` tool schema with the optional `responded_to` field added.
- `deep.py` orchestrator gains an optional round-2 step gated on `settings.research.deep.cross_lens_round`. Round 2 = `run_agents_parallel` of revision functions, each receiving the other three round-1 `LensView`s.
- Failure semantics: round-2 failure for an analyst → fall back to round-1 lens. All-4 round-2 failure → judge sees round-1 lenses (no degraded silence).
- Frontend: `LensPanel` shows "Revised after debate" badge + the revised summary inline, with the original collapsible.

**Tech Stack:** Python 3.11, Pydantic v2, Anthropic SDK Haiku 4.5 (revision calls), pytest, Next.js / TypeScript / React for frontend.

**Out of scope:**
- Round 3+ debate. Two passes is the right cost/depth balance.
- Adversarial pair restructuring (Phase 4).
- Persistence: revised fields live on the existing `EntryExitPlan.lenses` JSON; no new ORM tables.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `src/app/research/schema.py` | modify | `LensView` adds optional `revised_summary`, `revised_points`, `responded_to`. |
| `src/app/research/agents/base.py` | modify | Add `_revision_tool_input_schema()` and a shared `_format_other_lenses()` helper. Optionally extend `_lens_tool_input_schema` with `responded_to`. |
| `src/app/research/agents/technical.py` | modify | Add `run_technical_revision(packet, others) → AgentResult`. |
| `src/app/research/agents/fundamental.py` | modify | Add `run_fundamental_revision`. |
| `src/app/research/agents/sentiment.py` | modify | Add `run_sentiment_revision`. |
| `src/app/research/agents/contrarian.py` | modify | Add `run_contrarian_revision`. |
| `src/app/research/deep.py` | modify | Settings-flag-gated round-2 step between analysts and judge. Failure-tolerant fallback to round-1 lenses. |
| `src/app/config.py` | modify | Add `research.deep.cross_lens_round: bool = False` to settings schema. |
| `configs/settings.yaml` | modify | Add the flag (default `false`). |
| `apps/web/src/lib/api.ts` | modify | Add optional `revised_summary`, `revised_points`, `responded_to` to `LensView` type. |
| `apps/web/src/components/LensPanel.tsx` | modify | Render revised_summary if present + "Revised after debate" badge + collapsible original. |
| `tests/test_research_deep.py` | modify | Test happy path + partial round-2 failure + total round-2 failure + toggle-off bypass. |

---

## Task 1 — Schema additions to `LensView`

**Files:**
- Modify: `src/app/research/schema.py`

- [ ] **Step 1: Read current `LensView` definition**

```bash
cd /Users/jaspervalk/Documents/projects/trading-signal-research && grep -n "class LensView" src/app/research/schema.py
```

Note the current fields and Pydantic style (v2, `BaseModel`, `Field`).

- [ ] **Step 2: Write a failing test for new fields**

In `tests/test_research_deep.py`, append (don't replace):

```python
from app.research.schema import LensView


def test_lens_view_supports_revised_fields():
    """Schema supports optional revised_summary / revised_points / responded_to.

    Phase 2 cross-lens debate adds these as a second-round read; absence
    means no revision happened (legacy plans + toggle-off Deep runs).
    """
    lv = LensView(
        name="quantitative",
        direction="bullish",
        conviction="high",
        summary="initial read",
        points=["p1"],
        revised_summary="after seeing fundamental's bear case I'd downgrade",
        revised_points=["bear case is real but my technicals still hold"],
        responded_to=["fundamental", "contrarian_risk"],
    )
    assert lv.revised_summary == "after seeing fundamental's bear case I'd downgrade"
    assert lv.responded_to == ["fundamental", "contrarian_risk"]


def test_lens_view_revised_fields_default_to_none_or_empty():
    lv = LensView(
        name="quantitative",
        direction="bullish",
        conviction="high",
        summary="round-1 only",
        points=["p1"],
    )
    assert lv.revised_summary is None
    assert lv.revised_points == []
    assert lv.responded_to == []
```

- [ ] **Step 3: Verify test fails**

```bash
.venv/bin/pytest tests/test_research_deep.py::test_lens_view_supports_revised_fields -v
```

Expected: FAIL — fields don't exist yet.

- [ ] **Step 4: Add fields to `LensView` in `src/app/research/schema.py`**

Inside `class LensView(BaseModel):` add (after the existing `points: list[str] = ...` field):

```python
    # Cross-lens debate (Phase 2). Populated only when round 2 ran AND
    # the analyst chose to revise. Absence means "round 2 didn't apply
    # or analyst declined to revise"; original summary / points stand.
    revised_summary: str | None = None
    revised_points: list[str] = Field(default_factory=list)
    responded_to: list[str] = Field(default_factory=list)  # lens names this analyst engaged with
```

- [ ] **Step 5: Verify both tests pass**

```bash
.venv/bin/pytest tests/test_research_deep.py::test_lens_view_supports_revised_fields tests/test_research_deep.py::test_lens_view_revised_fields_default_to_none_or_empty -v
```

Expected: 2/2 PASS.

- [ ] **Step 6: Run the full Deep test suite to confirm no regressions**

```bash
.venv/bin/pytest tests/test_research_deep.py -v 2>&1 | tail -20
```

Expected: all PASS (existing 13 tests + 2 new = 15).

- [ ] **Step 7: Commit**

```bash
git add src/app/research/schema.py tests/test_research_deep.py
git commit -m "feat(schema): LensView gains optional revised_summary/revised_points/responded_to

Phase 2 cross-lens debate uses these to surface round-2 revisions when
they happen. Absence means round 2 didn't apply or analyst declined to
revise; original summary/points stand. Backward-compatible: existing
plans deserialise with empty defaults."
```

---

## Task 2 — Settings flag for the toggle

**Files:**
- Modify: `src/app/config.py`
- Modify: `configs/settings.yaml`

- [ ] **Step 1: Read current settings schema**

```bash
grep -n "class Settings\|research\b" src/app/config.py | head -20
cat configs/settings.yaml | head -40
```

Note how the existing `research.*` keys are nested (likely via Pydantic models with dotted access).

- [ ] **Step 2: Add `cross_lens_round` field to settings schema**

In `src/app/config.py`, find the research-related Pydantic settings model (e.g. `class ResearchSettings(BaseModel)` or similar). Add to its `deep` sub-model (or wherever Deep-mode settings live):

```python
    cross_lens_round: bool = False  # Phase 2: enable round-2 debate after analysts emit
```

If there's no `deep` sub-model and Deep settings are flat, add the field at the appropriate level. Match the existing nesting.

- [ ] **Step 3: Add the key to `configs/settings.yaml`**

In `configs/settings.yaml`, find the `research:` block. Add (matching existing indentation):

```yaml
research:
  # ... existing keys ...
  deep:
    cross_lens_round: false  # Phase 2: round-2 debate; cost +~$0.02/run
```

If `research.deep.*` doesn't exist yet in the YAML, add it. If `research.*` is flat, add `cross_lens_round: false` flatly under it.

- [ ] **Step 4: Verify settings load cleanly**

```bash
.venv/bin/python -c "from app.config import load_env; s = load_env(); print(getattr(s, 'research', None))"
```

Expected: prints the research settings without error.

- [ ] **Step 5: Run full test suite**

```bash
.venv/bin/pytest -q 2>&1 | tail -3
```

Expected: 376 passed (374 + 2 from Task 1).

- [ ] **Step 6: Commit**

```bash
git add src/app/config.py configs/settings.yaml
git commit -m "feat(config): research.deep.cross_lens_round flag (default false)

Gates Phase 2 cross-lens debate. Default off so existing Deep runs
behave identically; flip to true to enable round-2 revisions
(cost +~$0.02/run, +~10s)."
```

---

## Task 3 — Revision prompts + per-analyst revision functions

**Files:**
- Modify: `src/app/research/agents/base.py`
- Modify: `src/app/research/agents/technical.py`
- Modify: `src/app/research/agents/fundamental.py`
- Modify: `src/app/research/agents/sentiment.py`
- Modify: `src/app/research/agents/contrarian.py`

- [ ] **Step 1: Read existing analyst module structure**

```bash
cat src/app/research/agents/base.py
cat src/app/research/agents/technical.py
```

Understand the existing `run_technical()` shape: prompt building, tool input schema, parsing into `LensView`, returning `AgentResult`. The revision functions will mirror this.

- [ ] **Step 2: Add a shared revision-prompt helper to `base.py`**

Append to `src/app/research/agents/base.py`:

```python
def _format_other_lenses(others: list[LensView]) -> str:
    """Format the other lenses' round-1 reads for inclusion in a revision prompt.

    Used by every analyst's revision call. Excludes the receiving analyst's
    own round-1 read (caller filters before passing in).
    """
    if not others:
        return "(no other lens reads available)"
    parts: list[str] = []
    for lv in others:
        parts.append(
            f"- [{lv.name}] direction={lv.direction} · conviction={lv.conviction}"
        )
        parts.append(f"    summary: {lv.summary}")
        for p in lv.points:
            parts.append(f"    · {p}")
    return "\n".join(parts)


def _revision_tool_input_schema(name: str) -> dict[str, Any]:
    """JSON schema for a revision call's `submit_revised_lens` tool.

    Mirrors `_lens_tool_input_schema` plus the required `responded_to`
    list and a `revised_summary` separate from the round-1 summary.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "direction",
            "conviction",
            "revised_summary",
            "revised_points",
            "responded_to",
        ],
        "properties": {
            "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
            "conviction": {"type": "string", "enum": list(CONFIDENCE)},
            "revised_summary": {"type": "string"},
            "revised_points": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 4,
            },
            "responded_to": {
                "type": "array",
                "items": {"type": "string", "enum": list(LENS_NAMES)},
                "minItems": 0,
                "maxItems": 3,
            },
        },
    }
```

The `responded_to` list is what makes the revision auditable — the analyst names the lenses they engaged with (or empty list if they're standing pat without responding to any specific other lens).

- [ ] **Step 3: Add a generic `run_revision` helper to `base.py`**

Append:

```python
REVISION_TOOL_NAME = "submit_revised_lens"


def run_revision(
    *,
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    round_one_lens: LensView,
    client: Anthropic | None = None,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 1500,
) -> AgentResult:
    """Run one analyst's revision call. On success, returns an `AgentResult`
    whose `lens` is the round-1 lens enriched with `revised_summary`,
    `revised_points`, and `responded_to`. On failure, returns the round-1
    lens verbatim (no fields revised), so the orchestrator's fallback is a
    no-op.
    """
    if client is None:
        env = load_env()
        if not env.anthropic_api_key:
            return AgentResult(
                agent_name=agent_name,
                lens=round_one_lens,
                cost_usd=0.0,
                duration_ms=0,
                error="ANTHROPIC_API_KEY not set",
            )
        client = Anthropic(api_key=env.anthropic_api_key)

    started = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,  # determinism — same packet → same revision
            system=system_prompt,
            tools=[
                {
                    "name": REVISION_TOOL_NAME,
                    "description": "Submit a revised lens read after seeing the other lenses' round-1 reads.",
                    "input_schema": _revision_tool_input_schema(agent_name),
                }
            ],
            tool_choice={"type": "tool", "name": REVISION_TOOL_NAME},
            messages=[{"role": "user", "content": user_prompt}],
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        cost = _compute_cost(response, price_in=HAIKU_PRICE_IN, price_out=HAIKU_PRICE_OUT)
        raw = _extract_tool_input(response, tool_name=REVISION_TOOL_NAME)
        if raw is None:
            return AgentResult(
                agent_name=agent_name,
                lens=round_one_lens,
                cost_usd=cost,
                duration_ms=duration_ms,
                error="no submit_revised_lens block",
            )

        # Build a fresh LensView preserving round-1 summary/points alongside
        # revised fields. Direction / conviction may change; the original
        # round-1 summary/points are intentionally preserved.
        revised_lens = round_one_lens.model_copy(
            update={
                "direction": raw.get("direction", round_one_lens.direction),
                "conviction": raw.get("conviction", round_one_lens.conviction),
                "revised_summary": raw.get("revised_summary"),
                "revised_points": list(raw.get("revised_points", [])),
                "responded_to": list(raw.get("responded_to", [])),
            }
        )
        return AgentResult(
            agent_name=agent_name,
            lens=revised_lens,
            cost_usd=cost,
            duration_ms=duration_ms,
            raw=raw,
        )
    except Exception as e:
        log.warning(f"research.deep.revision_failed.{agent_name}", error=str(e))
        return AgentResult(
            agent_name=agent_name,
            lens=round_one_lens,  # fall back to round-1
            cost_usd=0.0,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=str(e),
        )


__all__ = [
    *globals().get("__all__", []),
    "REVISION_TOOL_NAME",
    "_format_other_lenses",
    "_revision_tool_input_schema",
    "run_revision",
]
```

(If `__all__` is set explicitly somewhere, append to it rather than this `*globals()` trick.)

The key call: `_extract_tool_input(response, tool_name=REVISION_TOOL_NAME)`. If `_extract_tool_input` exists in `base.py` already, reuse it. If not, write the equivalent.

- [ ] **Step 4: Add `run_technical_revision` to `technical.py`**

Append to `src/app/research/agents/technical.py`:

```python
from app.research.agents.base import (
    AgentResult,
    _format_other_lenses,
    run_revision,
)
from app.research.context import ResearchPacket
from app.research.schema import LensView

REVISION_SYSTEM_PROMPT = """\
You are the Quantitative analyst on a four-lens swing-trading panel. You \
already submitted a round-1 read. Now you see the OTHER THREE analysts' \
round-1 reads. Your job: revise YOUR read if their evidence changes your \
analysis. Specifically:

- Engage with their reads where relevant — name which lenses you're \
  responding to via `responded_to`.
- It is FINE to leave your direction / conviction unchanged; just \
  acknowledge in `revised_summary` that you considered the other reads.
- DO NOT mimic their disciplines — stay in your lane (technicals, factor \
  exposure, momentum). You can NOTE that fundamental lens is bearish, \
  but don't suddenly start citing PEG ratios.
- `revised_summary`: short (1-2 sentences) headline of your revised read.
- `revised_points`: 1-4 bullets supporting the revised read.
- `responded_to`: lens names ('fundamental', 'sentiment_macro', 'contrarian_risk') you specifically engaged with. Empty list = standing pat without addressing any other lens.

Submit via submit_revised_lens.
"""


REVISION_USER_TEMPLATE = """\
Your round-1 read:
- direction: {round_one_direction}
- conviction: {round_one_conviction}
- summary: {round_one_summary}
- points:
{round_one_points}

OTHER LENSES (round 1):
{others_block}

Revise your read via submit_revised_lens.
"""


def run_technical_revision(
    packet: ResearchPacket,
    *,
    round_one_lens: LensView,
    others: list[LensView],
    client=None,
) -> AgentResult:
    """Round-2 revision for the Quantitative analyst. Returns the revised
    AgentResult; on failure the round-1 lens is preserved verbatim.
    """
    user_prompt = REVISION_USER_TEMPLATE.format(
        round_one_direction=round_one_lens.direction,
        round_one_conviction=round_one_lens.conviction,
        round_one_summary=round_one_lens.summary,
        round_one_points="\n".join(f"- {p}" for p in round_one_lens.points),
        others_block=_format_other_lenses(others),
    )
    return run_revision(
        agent_name="quantitative",
        system_prompt=REVISION_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        round_one_lens=round_one_lens,
        client=client,
    )
```

- [ ] **Step 5: Mirror for `fundamental.py`, `sentiment.py`, `contrarian.py`**

Each module gets its own `REVISION_SYSTEM_PROMPT` (taillored to the lens's discipline — Fundamental stays in fundamentals; Sentiment-Macro stays in sentiment / catalysts; Contrarian stays adversarial), its own `run_*_revision` function. The `REVISION_USER_TEMPLATE` is identical across all four (just round-1 + others_block) — DRY by importing from `technical.py` or duplicating the small string. Pick whichever the existing modules' style uses for shared bits.

For each:
- `run_fundamental_revision`: agent_name=`fundamental`, system prompt: "stay in valuation / sector / growth lane; cite specific numbers from your round-1 if revising."
- `run_sentiment_revision`: agent_name=`sentiment_macro`, system prompt: "stay in claims / flows / catalysts lane; engage with whether the other lenses' reads alter the *sentiment* picture."
- `run_contrarian_revision`: agent_name=`contrarian_risk`, system prompt: "you are adversarial — even if all three lenses are bullish, your job is to find what could go wrong. If you genuinely can't find a bear case after seeing them, say so explicitly with `direction='neutral'` and a `revised_summary` like 'no bear case identified after debate'."

- [ ] **Step 6: Verify imports compile**

```bash
.venv/bin/python -c "
from app.research.agents.technical import run_technical_revision
from app.research.agents.fundamental import run_fundamental_revision
from app.research.agents.sentiment import run_sentiment_revision
from app.research.agents.contrarian import run_contrarian_revision
from app.research.agents.base import run_revision, _format_other_lenses, REVISION_TOOL_NAME
print('imports OK')
"
```

Expected: `imports OK`.

- [ ] **Step 7: Commit**

```bash
git add src/app/research/agents/
git commit -m "feat(research): per-analyst round-2 revision functions

Each lens (Quantitative / Fundamental / Sentiment-Macro / Contrarian-Risk)
gains a run_*_revision(packet, round_one_lens, others) function that calls
Haiku 4.5 with temperature=0, sees the OTHER three round-1 reads, and may
revise its summary / points / direction / conviction. The contrarian
revision stays adversarial — explicitly says 'no bear case' if it can't
find one.

Failure semantics: on any error, run_revision returns the round-1 lens
verbatim — orchestrator fallback is a no-op."
```

---

## Task 4 — Orchestrator integration in `deep.py`

**Files:**
- Modify: `src/app/research/deep.py`

- [ ] **Step 1: Write a failing orchestration test**

In `tests/test_research_deep.py`, append:

```python
def test_deep_round_two_uses_revised_lenses_when_flag_on(monkeypatch):
    """When research.deep.cross_lens_round=True, revisions run and the judge
    sees revised LensView instances."""
    # Force the flag on for this test.
    from app.research import deep as deep_mod
    monkeypatch.setattr(deep_mod, "_cross_lens_enabled", lambda: True)

    # ... fixture setup analogous to the existing test_deep_run_returns_validated_plan ...
    # Provide round-1 mock lenses. Provide round-2 mock that returns revised summaries.
    # Assert: judge_result.plan.lenses[i].revised_summary is populated for each lens.
    pass  # Implementer fills in based on existing fixture style
```

Implementer: model this after the existing happy-path Deep test in `test_research_deep.py` (it stubs `run_agents_parallel` and `run_judge`). The new test should stub the revision call to return mock revised payloads and assert revised fields propagate.

- [ ] **Step 2: Implement orchestrator changes in `src/app/research/deep.py`**

Add the round-2 step. After `analyst_results = run_agents_parallel(runners)` and the `lenses = [...]` assembly, but BEFORE the existing recording call:

```python
def _cross_lens_enabled() -> bool:
    """Read the toggle once per call. Patched by tests."""
    try:
        from app.config import load_env
        return bool(load_env().research.deep.cross_lens_round)
    except Exception:
        return False
```

(Place near the top of the module or alongside helpers.)

After the round-1 lens list is assembled and BEFORE `record_lens_snapshots(...)`:

```python
    if _cross_lens_enabled() and len(lenses) >= 2:
        from app.research.agents.contrarian import run_contrarian_revision
        from app.research.agents.fundamental import run_fundamental_revision
        from app.research.agents.sentiment import run_sentiment_revision
        from app.research.agents.technical import run_technical_revision

        revision_runners: list = []
        revision_map = {
            "quantitative": run_technical_revision,
            "fundamental": run_fundamental_revision,
            "sentiment_macro": run_sentiment_revision,
            "contrarian_risk": run_contrarian_revision,
        }
        for ar in analyst_results:
            if ar.lens is None:
                continue
            others = [other.lens for other in analyst_results
                      if other.lens is not None and other.agent_name != ar.agent_name]
            runner = revision_map.get(ar.lens.name)
            if runner is None:
                continue
            revision_runners.append(
                lambda r=runner, o=others, rl=ar.lens, p=packet, c=client:
                    r(p, round_one_lens=rl, others=o, client=c)
            )

        if revision_runners:
            revision_results = run_agents_parallel(revision_runners)
            # Replace round-1 lens with revised one (or keep round-1 on error)
            revised_by_name = {ar.agent_name: ar.lens for ar in revision_results if ar.lens is not None}
            lenses = [
                revised_by_name.get(lens.name, lens) for lens in lenses
            ]
            # Track total round-2 cost so the judge's audit reflects it.
            for rr in revision_results:
                # Append revision cost to corresponding round-1 result
                for ar in analyst_results:
                    if ar.agent_name == rr.agent_name and ar.lens is not None:
                        ar.cost_usd += rr.cost_usd
                        ar.duration_ms += rr.duration_ms
                        break
```

The key invariant: `lenses` after this block contains either the revised lens (if revision succeeded and the analyst chose to revise) or the round-1 lens (if revision failed or analyst declined). The judge sees this list.

- [ ] **Step 3: Verify tests pass**

```bash
.venv/bin/pytest tests/test_research_deep.py -v 2>&1 | tail -20
```

Expected: all PASS — existing tests still green (cross-lens flag is off in the fixtures), new test asserts revisions propagate when flag is on.

- [ ] **Step 4: Commit**

```bash
git add src/app/research/deep.py tests/test_research_deep.py
git commit -m "feat(research): round-2 cross-lens debate in Deep mode

Gated on settings.research.deep.cross_lens_round (default false). When
on, after round-1 analysts emit, a parallel round-2 lets each lens see
the other three's reads and revise. Judge consumes the revised list.
Round-2 failures fall back to round-1 (no degraded silence). Cost +~\$0.02,
+~10s per Deep run when enabled."
```

---

## Task 5 — Frontend: render revised lens

**Files:**
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/components/LensPanel.tsx`

- [ ] **Step 1: Add optional fields to the `LensView` TS type**

In `apps/web/src/lib/api.ts`, find the `LensView` type and add:

```ts
  revised_summary?: string | null;
  revised_points?: string[];
  responded_to?: string[];
```

Match the existing type style (snake_case, optional with `?` or `| null` per file convention).

- [ ] **Step 2: Update `LensPanel.tsx` to render revised fields**

Find where `lens.summary` is rendered. After (or replacing, depending on UX choice) the summary block, render:

```tsx
{lens.revised_summary ? (
  <div className="mt-1 space-y-0.5">
    <div className="text-[10px] uppercase tracking-wider text-[var(--accent)]">
      Revised after debate
    </div>
    <div className="text-[12px]">{lens.revised_summary}</div>
    {lens.revised_points && lens.revised_points.length > 0 && (
      <ul className="text-[11px] space-y-0.5 mt-1">
        {lens.revised_points.map((p, i) => (
          <li key={i} className="flex gap-1.5">
            <span className="opacity-50">·</span>
            <span>{p}</span>
          </li>
        ))}
      </ul>
    )}
    {lens.responded_to && lens.responded_to.length > 0 && (
      <div className="text-[10px] opacity-60">
        Responded to: {lens.responded_to.join(", ")}
      </div>
    )}
  </div>
) : null}
```

If the existing component has its own design tokens, adapt — match the style of e.g. `RRRangePanel` (CSS vars).

- [ ] **Step 3: TS typecheck**

```bash
cd apps/web && npx tsc --noEmit 2>&1 | tail -10
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/lib/api.ts apps/web/src/components/LensPanel.tsx
git commit -m "feat(web): LensPanel renders revised lens after cross-lens debate

Shows 'Revised after debate' badge + revised summary + revised points +
list of lenses responded to. Renders nothing extra when revised_summary
is null (Phase 2 toggle off, or analyst declined to revise)."
```

---

## Task 6 — Recent-changes documentation

**Files:**
- Modify: `docs/recent-changes-2026-05-08.md`

- [ ] **Step 1: Append §14**

Append:

```markdown
## 14. Cross-lens debate (Phase 2 of agent roadmap)

Phase 2 of [docs/superpowers/plans/2026-05-08-lens-agents-roadmap.md](superpowers/plans/2026-05-08-lens-agents-roadmap.md).
Adds an opt-in second-round debate where each Deep-mode analyst sees the other
three lenses' round-1 reads and may revise their summary / points /
direction / conviction.

Toggle: `research.deep.cross_lens_round` in `configs/settings.yaml`.
Default `false` until cost / behavior are validated on real runs.

**What landed:**

| Component | File |
|---|---|
| `LensView` schema additions (`revised_summary`, `revised_points`, `responded_to`) | [src/app/research/schema.py](../src/app/research/schema.py) |
| Per-analyst `run_*_revision()` functions + shared helper | [src/app/research/agents/base.py](../src/app/research/agents/base.py), `agents/{technical,fundamental,sentiment,contrarian}.py` |
| Orchestrator integration | [src/app/research/deep.py](../src/app/research/deep.py) |
| Settings flag | [src/app/config.py](../src/app/config.py), [configs/settings.yaml](../configs/settings.yaml) |
| Frontend rendering | [apps/web/src/lib/api.ts](../apps/web/src/lib/api.ts), [apps/web/src/components/LensPanel.tsx](../apps/web/src/components/LensPanel.tsx) |

**Failure semantics:** round-2 failure for an analyst → fall back to its
round-1 lens for the judge. All-4 round-2 failure → judge sees round-1
lenses.

**Cost:** +~$0.02 / Deep run when enabled (4 extra Haiku calls at
temperature=0). Total Deep cost rises from ~$0.04-0.10 to ~$0.06-0.12.
```

- [ ] **Step 2: Final test sweep**

```bash
.venv/bin/pytest -q 2>&1 | tail -3
```

Expected: 376+ passed.

- [ ] **Step 3: Commit**

```bash
git add docs/recent-changes-2026-05-08.md
git commit -m "docs: §14 — cross-lens debate (Phase 2)

Round-2 debate where each Deep analyst sees the other three lenses'
round-1 reads and may revise. Default off; flip
research.deep.cross_lens_round to true to enable."
```

---

## Self-Review Notes

**Spec coverage:**
- Schema additions: Task 1.
- Settings flag: Task 2.
- Per-analyst revision functions: Task 3.
- Orchestrator integration: Task 4.
- Frontend: Task 5.
- Doc: Task 6.

**Placeholder check:** Step 1 of Task 4 has the test stub marked `pass # Implementer fills in based on existing fixture style`. This is a deliberate ask for the implementer to model after the existing happy-path test in `test_research_deep.py` — providing the exact mock structure here without reading that file would invent fake fixture shapes. Acceptable for this skill since the implementer has the existing fixture in context.

**Type consistency:** `LensView`, `AgentResult`, `ResearchPacket`, `_format_other_lenses`, `run_revision`, `_revision_tool_input_schema`, `REVISION_TOOL_NAME` — all consistent across tasks. `responded_to` is `list[str]` everywhere.

**Risk:** The `_extract_tool_input` helper signature in `base.py` may not accept a `tool_name` keyword. If it doesn't, Task 3 step 3's `run_revision` call needs adjustment — either extend `_extract_tool_input` to take a name (small addition) or write a local version. Implementer will catch this.
