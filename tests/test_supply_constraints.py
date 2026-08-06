"""Tests for the supply-constraint registry (Layer B input).

The single most important rule in `configs/supply_constraints.yaml` is "no
source, no constraint" — every entry's `deficit_source` must carry a dated
publication year, or the whole file fails to load. These tests exercise that
rule plus the other load-time validations directly, via `tmp_path` fixture
files, and separately confirm the real seeded config loads clean.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.supply.constraints import (
    STALE_AFTER_DAYS,
    Constraint,
    ConstraintExposure,
    load_constraints,
)


def _base_entry(**overrides) -> dict:
    entry = {
        "id": "test_constraint",
        "market": "widgets",
        "deficit_pct": 0.05,
        "deficit_source": "Some Industry Report (2025)",
        "deficit_horizon": "2025-2026",
        "expansion_lead_months": 18,
        "demand_driver": "test demand driver",
        "capacity_history": "test capacity history",
        "confidence": "high",
        "last_reviewed": "2026-08-01",
        "exposures": [
            {
                "ticker": "TEST",
                "revenue_exposure_pct": 0.5,
                "exposure_source": "unverified estimate",
                "is_pure_play": False,
            }
        ],
    }
    entry.update(overrides)
    return entry


def _write(tmp_path, *entries: dict):
    import yaml

    path = tmp_path / "supply_constraints.yaml"
    path.write_text(yaml.safe_dump({"constraints": list(entries)}))
    return path


# --- the load-bearing rule: no dated source, no constraint -----------------


def test_entry_without_a_deficit_source_year_is_rejected(tmp_path):
    path = _write(tmp_path, _base_entry(deficit_source="Some Industry Report, no date"))
    with pytest.raises(ValueError, match="deficit_source"):
        load_constraints(path)


def test_entry_with_a_deficit_source_year_loads(tmp_path):
    path = _write(tmp_path, _base_entry(deficit_source="Some Industry Report (2025)"))
    constraints = load_constraints(path, now=date(2026, 8, 6))
    assert len(constraints) == 1
    assert constraints[0].deficit_source == "Some Industry Report (2025)"


def test_rejection_error_names_the_offending_entry(tmp_path):
    path = _write(tmp_path, _base_entry(id="bad_entry", deficit_source="undated claim"))
    with pytest.raises(ValueError, match="bad_entry"):
        load_constraints(path)


def test_constraint_model_itself_rejects_an_undated_source():
    """The rule is enforced on the model, not just the loader."""
    with pytest.raises(ValueError, match="deficit_source"):
        Constraint.model_validate(_base_entry(deficit_source="no year here"))


# --- confidence must be high/medium/low -------------------------------------


@pytest.mark.parametrize("confidence", ["high", "medium", "low"])
def test_valid_confidence_values_load(tmp_path, confidence):
    path = _write(tmp_path, _base_entry(confidence=confidence))
    constraints = load_constraints(path, now=date(2026, 8, 6))
    assert constraints[0].confidence == confidence


def test_invalid_confidence_value_is_rejected(tmp_path):
    path = _write(tmp_path, _base_entry(confidence="very high"))
    with pytest.raises(ValueError):
        load_constraints(path)


# --- revenue_exposure_pct must be in (0, 1] ---------------------------------


def test_revenue_exposure_pct_of_one_is_valid_boundary():
    exposure = ConstraintExposure(
        ticker="TEST", revenue_exposure_pct=1.0, exposure_source="10-K segment"
    )
    assert exposure.revenue_exposure_pct == pytest.approx(1.0)


def test_revenue_exposure_pct_of_zero_is_rejected():
    with pytest.raises(ValueError, match="revenue_exposure_pct"):
        ConstraintExposure(ticker="TEST", revenue_exposure_pct=0.0, exposure_source="x")


def test_revenue_exposure_pct_above_one_is_rejected():
    with pytest.raises(ValueError, match="revenue_exposure_pct"):
        ConstraintExposure(ticker="TEST", revenue_exposure_pct=1.5, exposure_source="x")


def test_revenue_exposure_pct_negative_is_rejected():
    with pytest.raises(ValueError, match="revenue_exposure_pct"):
        ConstraintExposure(ticker="TEST", revenue_exposure_pct=-0.1, exposure_source="x")


# --- staleness ---------------------------------------------------------------


def test_recently_reviewed_constraint_is_not_stale(tmp_path):
    path = _write(tmp_path, _base_entry(last_reviewed="2026-08-01"))
    constraints = load_constraints(path, now=date(2026, 8, 6))  # 5 days later
    assert constraints[0].is_stale is False


def test_constraint_older_than_180_days_is_flagged_stale_but_still_loaded(tmp_path):
    path = _write(tmp_path, _base_entry(last_reviewed="2026-01-01"))
    reference = date(2026, 8, 6)  # 217 days later
    assert (reference - date(2026, 1, 1)).days > STALE_AFTER_DAYS
    constraints = load_constraints(path, now=reference)
    assert len(constraints) == 1  # still loaded, not dropped
    assert constraints[0].is_stale is True


def test_exactly_at_the_boundary_is_not_yet_stale(tmp_path):
    path = _write(tmp_path, _base_entry(last_reviewed="2026-01-01"))
    reference = date(2026, 1, 1) + timedelta(days=STALE_AFTER_DAYS)
    constraints = load_constraints(path, now=reference)
    assert constraints[0].is_stale is False  # > 180, not >= 180


# --- the real seeded file ----------------------------------------------------


def test_the_real_seed_file_has_exactly_one_entry():
    """Per the plan: seed with exactly the NAND historical precedent, no
    invented constraints."""
    constraints = load_constraints(now=date(2026, 8, 6))
    assert len(constraints) == 1
    assert constraints[0].id == "nand_2024_2025_deficit"


def test_the_real_seed_file_loads_clean_and_is_not_stale_on_its_review_date():
    constraints = load_constraints(now=date(2026, 8, 6))
    nand = constraints[0]
    assert nand.confidence == "high"
    assert nand.is_stale is False
    assert "2026-08-06" in nand.deficit_source or "2026" in nand.deficit_source


def test_the_real_seed_file_has_wdc_and_mu_exposures():
    constraints = load_constraints(now=date(2026, 8, 6))
    tickers = {e.ticker for e in constraints[0].exposures}
    assert tickers == {"WDC", "MU"}
    for exposure in constraints[0].exposures:
        assert 0 < exposure.revenue_exposure_pct <= 1
        assert "unverified" in exposure.exposure_source.lower()
