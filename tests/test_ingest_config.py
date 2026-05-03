"""Verify creators.yaml is loadable and has the expected shape."""

from __future__ import annotations

from app.ingest.run import load_creator_configs


def test_creators_yaml_loads_and_has_active_youtube_creators():
    cfgs = load_creator_configs()
    assert len(cfgs) >= 5, "expected at least 5 curated creators"

    youtube = [c for c in cfgs if c.source_type == "youtube"]
    assert len(youtube) == len(cfgs), "Phase 1 should be YouTube-only"

    active = [c for c in cfgs if c.active]
    assert len(active) >= 5

    handles = {c.external_id for c in cfgs}
    assert all(h.startswith("@") or h.startswith("UC") for h in handles), (
        "external_id should be a @handle or a UC channel id"
    )

    names = {c.display_name for c in cfgs}
    assert len(names) == len(cfgs), "display_name should be unique"
