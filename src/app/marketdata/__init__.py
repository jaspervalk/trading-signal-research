"""Market data access — one client, one cache policy, one symbol vocabulary.

Per the ADR 0009 target module map, this package is the single seam between
the app and the outside world's price/fundamentals data. It is being filled in
incrementally; `symbols` landed first because the dot-vs-dash split between
`configs/universe.csv` (`BRK.B`) and `configs/screen_universe.csv` (`BRK-B`)
was silently losing data, not merely duplicating code.
"""

from app.marketdata.symbols import (
    canonical_symbol,
    display_symbol,
    symbol_variants,
)

__all__ = ["canonical_symbol", "display_symbol", "symbol_variants"]
