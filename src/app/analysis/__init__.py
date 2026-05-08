"""Ticker research analysis layer.

This module turns raw price + transcript data into a `TickerResearchView` —
the structured "decision support" answer for any valid ticker. Per ADR 0005
the output is decision support, never advice. Per ADR 0006 transcript signals
are an indicator class, not the output.

Design rules carried forward from existing ADRs:
- Pure functions; no DB writes from this module. The orchestrator may READ
  from the DB (TickerSignal / Claim / Coverage), but it does not persist.
- Leakage-controlled: every compute path takes an `as_of` and slices only
  bars / signals with timestamp <= as_of. `MarketDataReader`-style discipline
  from ADR 0007 §"Bias prevention" applies.
- Universe-agnostic: random tickers that yfinance can resolve are valid
  inputs. Universe gating remains in the extractor validator path only.
- Rule-based, transparent: setup / style / status logic is a rubric the user
  can read. No magic composite scores.
"""
