"""Entry/Exit research feature — see docs/entry-exit-research-plan.md.

Two modes:
- `quick`  — single Claude call, ~$0.01, 3-5s, grounded by deterministic levels.
- `deep`   — multi-agent (Phase 2), ~$0.10, 30-60s.

Both return an `EntryExitPlan` with structured zones + qualitative reasoning.
"""
