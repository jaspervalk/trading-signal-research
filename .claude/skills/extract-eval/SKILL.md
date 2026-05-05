---
name: extract-eval
description: Run the extraction gold-set evaluation and report precision/recall/F1 deltas vs baseline. Use when the user asks to "evaluate the extractor", "run the gold set", "check extraction quality", or after any extractor/prompt/validator change.
allowed-tools: Read, Bash(pytest *), Bash(python *), Glob, Grep
---

# /extract-eval

Evaluate the call/claim extractor against `data/gold/extraction_gold.jsonl` and report deltas vs the last baseline.

This skill is a thin wrapper that delegates to the `extractor-evaluator` subagent so the heavy work (running pytest, parsing outputs, diffing against baseline) happens in an isolated context and the parent session gets back a one-page summary.

## Procedure

1. **Confirm scope** in one line: which extractor commit / change is being evaluated. If unclear, infer from `git log -1 --oneline -- src/app/extract/`.
2. **Delegate to `extractor-evaluator`** with the change description, optional baseline commit SHA, and any subset filter (date range, creator, claim type) the user specified.
3. **Surface the subagent's verdict** verbatim to the user. Do not paraphrase or expand.
4. **If the verdict is "investigate" or "revert,"** offer next steps: open the failing gold-set examples, check the prompt, re-run on a subset.

## Hard rules

- Always uses the gold set in `data/gold/extraction_gold.jsonl` — never a synthetic substitute.
- Never modifies the gold set or extractor code from this skill. Both are read-only here.
- If `data/gold/last_baseline.json` doesn't exist, the current run becomes the baseline and the user is told so explicitly.

## See also

- ADR 0002 — extraction principles: [docs/decisions/0002-extraction-hybrid.md](../../../docs/decisions/0002-extraction-hybrid.md)
- ADR 0006 — claims schema (when implemented): [docs/decisions/0006-claims-and-ticker-signals.md](../../../docs/decisions/0006-claims-and-ticker-signals.md)
- Subagent: [.claude/agents/extractor-evaluator.md](../../agents/extractor-evaluator.md)
