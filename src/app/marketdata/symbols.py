"""One canonical spelling for a ticker, everywhere.

Share classes are written at least three ways in this codebase's own inputs:
a transcript says "BRK.B", `configs/universe.csv` stores `BRK.B`,
`configs/screen_universe.csv` stores `BRK-B`, and yfinance answers only to
`BRK-B`. Before this module, those were four independent conventions that
happened to agree on the ~99% of symbols with no punctuation.

Where that bit: a call extracted as `BRK.B` passed universe validation, then
the market layer asked yfinance for `BRK.B`, got nothing back, and produced no
bars — so no activation, no outcome, no row. Silent, and indistinguishable
from "the call never activated". The failure mode is always this shape: the
symbol is *valid enough* to pass every check that doesn't hit the network, and
only the network call knows it's wrong.

The rule: **`canonical_symbol` is the market-facing spelling** (dash form,
what yfinance wants) and is what every lookup, cache key, and stored ticker
should use. `display_symbol` is for humans reading a page. `symbol_variants`
exists for membership checks against inputs we don't control (transcripts,
hand-edited CSVs), which may legitimately arrive in either spelling.
"""

from __future__ import annotations

__all__ = ["canonical_symbol", "display_symbol", "symbol_variants"]


def canonical_symbol(raw: str) -> str:
    """Market-facing spelling: upper-case, trimmed, dots → dashes.

    `BRK.B` → `BRK-B`. `brk-b` → `BRK-B`. ` aapl ` → `AAPL`.

    Use this for anything that leaves the process — yfinance calls, cache
    filenames, stored `ticker` columns — so a symbol has exactly one identity
    across the system.
    """
    return raw.strip().upper().replace(".", "-")


def display_symbol(raw: str) -> str:
    """Human-facing spelling: dashes → dots for share classes.

    `BRK-B` → `BRK.B`, which is how it's quoted on a brokerage statement and
    how a creator says it out loud. Presentation only — never round-trip this
    back into a lookup.
    """
    return canonical_symbol(raw).replace("-", ".")


def symbol_variants(raw: str) -> set[str]:
    """Every spelling of `raw` that a membership check should accept.

    For punctuation-free symbols this is just `{"AAPL"}`. For share classes it
    is `{"BRK-B", "BRK.B"}` — so a universe loaded from either CSV recognises a
    ticker written either way, without either file having to be rewritten.
    """
    canonical = canonical_symbol(raw)
    return {canonical, display_symbol(canonical)}
