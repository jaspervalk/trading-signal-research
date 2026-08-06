"""Supply-constraint screener (Layer A).

Finds companies whose gross margins are depressed against their own history and
whose cost base is fixed enough that a margin recovery would land mostly in
profit. It knows nothing about supply shortages: the shortage side of the
pattern lives in a hand-curated registry and is joined in later.

Layer A is a coarse net. Expect several hundred hits and no opinion about any
of them.
"""
