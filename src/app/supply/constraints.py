"""The supply-constraint registry (Layer B input).

Hand-curated, monthly cadence, YAML — see the header comment in
`configs/supply_constraints.yaml` for the curation process and why this is a
config file rather than a database table (diffable, commented, git-audited;
this data changes about once a month, not once a request).

**No source, no constraint.** Every `Constraint.deficit_source` must contain
a 4-digit publication year, or the entire file is REJECTED at load time with
a `ValueError` naming the offending entry. This is enforced in
`Constraint`'s field validator, not downstream in Layer B — a supply-deficit
number nobody can trace to a dated source must never reach a score.

**Staleness is surfaced, not enforced.** A constraint whose `last_reviewed`
is more than `STALE_AFTER_DAYS` (180) old is still loaded and still scored —
Layer C's inflection triggers, not this module, decide what to do about a
stale thesis — but `Constraint.is_stale` is set `True` so nothing downstream
can present a six-month-old number as fresh without saying so.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.config import REPO_ROOT

Confidence = Literal["high", "medium", "low"]

# A constraint not re-checked within this many days is loaded but flagged
# `is_stale=True` rather than silently trusted as current.
STALE_AFTER_DAYS = 180

# "No source, no constraint": a deficit_source must name a publication year.
_YEAR_RE = re.compile(r"(19|20)\d{2}")


class ConstraintExposure(BaseModel):
    """One ticker's estimated revenue exposure to a `Constraint`'s market."""

    ticker: str
    revenue_exposure_pct: float
    exposure_source: str
    is_pure_play: bool = False

    @field_validator("revenue_exposure_pct")
    @classmethod
    def _revenue_exposure_pct_in_range(cls, v: float) -> float:
        if not (0 < v <= 1):
            raise ValueError(
                f"revenue_exposure_pct must be in (0, 1] (a fraction of revenue), got {v}"
            )
        return v


class Constraint(BaseModel):
    """One hand-curated supply-constraint thesis plus its ticker exposures.

    `is_stale` is not read from YAML — it is computed by `load_constraints`
    against `last_reviewed` and set on the instance after validation, so it
    always reflects "stale as of when this was loaded," not a value someone
    could accidentally hardcode into the config.
    """

    id: str
    market: str
    deficit_pct: float
    deficit_source: str
    deficit_horizon: str
    expansion_lead_months: float
    demand_driver: str
    capacity_history: str
    confidence: Confidence
    last_reviewed: date
    exposures: list[ConstraintExposure] = Field(default_factory=list)

    # Set post-validation by `load_constraints`; never present in the YAML.
    is_stale: bool = False

    @field_validator("deficit_source")
    @classmethod
    def _deficit_source_must_be_dated(cls, v: str) -> str:
        if not _YEAR_RE.search(v):
            raise ValueError(
                "deficit_source must include a 4-digit publication year "
                f"(no source, no constraint): {v!r}"
            )
        return v


def load_constraints(path: Path | None = None, *, now: date | None = None) -> list[Constraint]:
    """Load and validate `configs/supply_constraints.yaml` (or `path`).

    Raises `ValueError` naming the offending entry's `id` if any constraint
    fails validation — most importantly, a `deficit_source` without a dated
    publication year. This is a hard failure at load time, not a warning:
    the registry either loads clean or does not load.

    `now` is for tests — pass a fixed `date` to make staleness deterministic
    instead of depending on wall-clock time.
    """
    constraints_path = path or (REPO_ROOT / "configs" / "supply_constraints.yaml")
    with Path(constraints_path).open() as f:
        data = yaml.safe_load(f) or {}

    reference_date = now or datetime.now(timezone.utc).date()

    constraints: list[Constraint] = []
    for i, raw in enumerate(data.get("constraints") or []):
        entry_id = raw.get("id", f"<entry {i}>") if isinstance(raw, dict) else f"<entry {i}>"
        try:
            constraint = Constraint.model_validate(raw)
        except ValidationError as e:
            raise ValueError(f"supply_constraints.yaml entry {entry_id!r} is invalid: {e}") from e
        constraint.is_stale = (reference_date - constraint.last_reviewed).days > STALE_AFTER_DAYS
        constraints.append(constraint)

    return constraints


__all__ = [
    "STALE_AFTER_DAYS",
    "Confidence",
    "Constraint",
    "ConstraintExposure",
    "load_constraints",
]
