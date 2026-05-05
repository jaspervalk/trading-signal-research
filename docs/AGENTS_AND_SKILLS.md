# Claude Code agents and skills for `trading-signal-research`

This document is the source of truth for what Claude Code subagents and skills exist in this repo, why they exist, and the principles for adding new ones.

## Principle: agent ≠ module

A subagent earns its place when it encapsulates a **recurring development task** that benefits from one or more of:
- **Isolated context** — the task produces noisy output (test runs, gold-set diffs) that would dilute the main session.
- **Tool restriction** — the task should be locked to read-only or to a narrow set of tools.
- **Specialized prompting** — the task has a domain-specific protocol that's worth codifying so it's done the same way every time.

If a "candidate agent" is just "a Python module that does X," it's a module, not a subagent. The runtime extractor, backtester, and scorer all live in `src/app/` as code — they are not Claude Code agents.

By that test, this project has **three subagents** and **one skill**. Adding more requires the same justification.

## Subagents (`.claude/agents/`)

### `extractor-evaluator`

**When the parent session uses it.** Any time the extraction prompts, schemas, validator rules, or confidence formula change. Also after a gold-set update.

**What it does.** Runs the extraction gold-set evaluation (`pytest tests/test_extract_eval.py` + the eval notebook's harness), computes precision / recall / F1 per claim type and per call field, compares against the last baseline stored in `data/gold/last_baseline.json` (planned), and reports a tight summary: deltas, regressions, and the smallest set of failing examples that explain a regression.

**Why it's a subagent, not a module.** Gold-set diffs are noisy (per-segment outputs, full LLM JSON). Running this in the main session pollutes context for several thousand tokens; running in a subagent returns a one-page summary.

**Tool restriction.** Read, Bash (pytest only), no Edit/Write.

### `backtest-auditor`

**When the parent session uses it.** Before blessing a backtest result — particularly a strategy walk-forward run (per ADR 0007). Also when a backtest result looks "too good" and you want a leakage hunt.

**What it does.** Loads a recent `OutcomeWindow` or `WalkForwardResult` set, runs the sanity checks from ADR 0003 §"Sanity checks" and ADR 0007 §"Sanity checks," and reports any violations. Specifically: bars referenced before `posted_at`, fill prices outside the day's range, suspicious return-distribution outliers, regime imbalance, sample sizes below `min_n`, survivorship issues. Read-only; never modifies data.

**Why it's a subagent.** Leakage hunts are exploratory and produce verbose query output. The summary is what matters; the trail isn't.

**Tool restriction.** Read, Bash (sqlite3, python -c, pytest), no Edit/Write.

### `adr-writer`

**When the parent session uses it.** Whenever an architectural decision is made in conversation that needs to land in `docs/decisions/`. The user has built up a strong style for ADRs (see 0001–0007) and re-deriving it each time wastes effort.

**What it does.** Given a description of the decision, the context, and the consequences (in conversation), produces a properly-formatted ADR matching the existing repo style: numbered, dated, status, context/decision/consequences sections, explicit "what this ADR is *not* doing," and explicit "out of scope" lists.

**Why it's a subagent.** Format discipline. The ADRs are the single most-read documents in this repo; consistency matters. A subagent with the format frozen into its prompt produces ADR drafts in the right shape on the first try.

**Tool restriction.** Read, Write (only under `docs/decisions/`), no Bash.

## Skills (`.claude/skills/`)

### `/extract-eval`

**Invocation.** `/extract-eval` (slash command) or auto-routed when the user asks to "evaluate the extractor."

**What it does.** A thin slash-command wrapper that invokes the `extractor-evaluator` subagent on demand without typing the full delegation prompt. Reusable from any session.

**Why a skill (not just an agent invocation).** Repeatable procedure with arguments (e.g., `/extract-eval --since 2026-04-01`). Skills are the right home for "things you run by name."

## What we deliberately did *not* create

The original brief listed ten candidate agents. Eight were rejected with reasoning:

- **"Product architecture agent"** — that's the main Claude session in collaboration with the user. Outsourcing it to a subagent gives the user a worse architect and adds a layer of indirection.
- **"Quant research agent"** — too broad. "Research" isn't a task pattern; the actual recurring tasks (gold-set eval, leakage audit, ADR writing) are already covered.
- **"Data engineering agent"** / **"Financial data agent"** — these are modules in `src/app/`, not agents. Adding subagents here would be cargo-culting.
- **"YouTube/transcript ingestion agent"** / **"NLP extraction agent"** — runtime services, not dev-time agents. They live as code.
- **"Documentation agent"** — modern Claude Code is competent at docs; a dedicated agent is friction without payoff. ADR-writer is the exception because *format consistency* across many ADRs justifies it.
- **"Security/compliance agent"** — overkill for a personal research tool with no shared infra. Revisit if/when this becomes a multi-user system.
- **"Evaluation/testing agent"** — partially covered by `extractor-evaluator` and `backtest-auditor`. A general-purpose test-running agent doesn't add anything over `pytest` from the main session.

## Adding a new agent or skill

Two questions to answer before opening a new file:

1. **Is there a recurring task that meets the isolation / tool-restriction / specialized-prompting test?** If no, write a Python module instead.
2. **Has the task been done at least three times in the main session in the past month?** If no, codify it as a skill first (cheaper to maintain). Promote to a subagent only if the context-isolation argument becomes compelling.

If both are yes, follow the file-format conventions used by the existing agents/skills in this repo and reference the relevant ADRs in the agent's prompt body.

## File-format reference

- **Subagent file:** `.claude/agents/<name>.md` with YAML frontmatter (`name`, `description`, `tools`, optionally `model`). The markdown body is the system prompt.
- **Skill file:** `.claude/skills/<name>/SKILL.md` with YAML frontmatter (`name`, `description`, optionally `allowed-tools`, `arguments`, `model`). Supporting scripts/templates live in the same directory.
- **Description routing:** the `description` field is what the parent agent reads to decide whether to delegate. Keywords matter; vague descriptions don't get invoked.
