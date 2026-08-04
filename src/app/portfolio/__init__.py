"""Portfolio Manager: manual trade ledger and derived positions (ADR 0009).

The only stored entity is `PortfolioTrade`. Positions are derived by folding
trades chronologically with weighted-average cost — see `ledger.py`, which is
pure (no DB, no network, no clock) and therefore exhaustively testable.

Layers:
    schema.py   Pydantic models shared by ledger, service, API and CLI
    ledger.py   the money math: fold + validation
    pricing.py  intraday quotes and the EUR/USD rate, with a short TTL cache
    service.py  DB reads/writes, then fold + pricing into a PortfolioView
"""
