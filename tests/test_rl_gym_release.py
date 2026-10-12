from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from rl_gym import (
    ENVIRONMENT_VERSION,
    PLAN_SCHEMA_VERSION,
    RELEASE_VERSION,
    ReleaseManifest,
    canonical_json,
    make_release_manifest,
    stable_hash,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RELEASE_DOC = REPOSITORY_ROOT / "rl_gym" / "RELEASE.md"
README_DOC = REPOSITORY_ROOT / "rl_gym" / "README.md"


def test_stable_hash_is_independent_of_mapping_order():
    first = {"rules": {"beta": 2, "alpha": 1}, "config": ["a", "b"]}
    second = {"config": ["a", "b"], "rules": {"alpha": 1, "beta": 2}}

    assert canonical_json(first) == canonical_json(second)
    assert stable_hash(first) == stable_hash(second)
    assert stable_hash(first) == stable_hash(first).lower()


def test_canonical_json_uses_compact_sorted_utf8_formatting():
    assert canonical_json({"z": "café", "a": [True, None, 1]}) == (
        '{"a":[true,null,1],"z":"café"}'
    )


@pytest.mark.parametrize("year", [2027, 2028, 2029])
@pytest.mark.parametrize("block", ["jan_jun", "jul_dec"])
def test_release_manifest_accepts_all_supported_half_years(year, block):
    manifest = make_release_manifest(
        scenario_year=year, scenario_block=block, rules={}, config={}
    )

    assert (manifest.scenario_year, manifest.scenario_block) == (year, block)


def test_release_manifest_round_trips_through_json():
    manifest = make_release_manifest(
        scenario_year=2029,
        scenario_block="jan_jun",
        rules={"version": "rules-v1", "limits": {"bus-b": 10}},
        config={"seed": 7, "agents": ["team-a", "team-b"]},
    )

    encoded = json.dumps(manifest.to_dict())
    restored = ReleaseManifest.from_dict(json.loads(encoded))

    assert restored == manifest
    assert restored.digest() == manifest.digest()


def test_release_manifest_contains_required_versions_and_scenario_fields():
    manifest = make_release_manifest(
        scenario_year=2028,
        scenario_block="jul_dec",
        rules={},
        config={},
    )

    assert manifest.release_version == RELEASE_VERSION
    assert manifest.environment_version == ENVIRONMENT_VERSION
    assert manifest.plan_schema_version == PLAN_SCHEMA_VERSION
    assert manifest.to_dict()["scenario_year"] == 2028
    assert manifest.to_dict()["scenario_block"] == "jul_dec"


@pytest.mark.parametrize(
    ("scenario_year", "scenario_block"),
    [(2026, "jan_jun"), (2029, "annual"), (2029, "")],
)
def test_release_manifest_rejects_invalid_scenario_metadata(scenario_year, scenario_block):
    with pytest.raises(ValueError):
        make_release_manifest(
            scenario_year=scenario_year,
            scenario_block=scenario_block,
            rules={},
            config={},
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"release_version": ""},
        {"environment_version": ""},
        {"plan_schema_version": ""},
        {"scenario_year": True},
        {"scenario_year": 2029.0},
        {"scenario_block": "annual"},
    ],
)
def test_direct_release_manifest_construction_validates_identity_fields(kwargs):
    values = {
        "release_version": RELEASE_VERSION,
        "environment_version": ENVIRONMENT_VERSION,
        "plan_schema_version": PLAN_SCHEMA_VERSION,
        "scenario_year": 2029,
        "scenario_block": "jan_jun",
        "rules": {},
        "config": {},
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        ReleaseManifest(**values)


@pytest.mark.parametrize(
    "metadata",
    [
        {1: "non-string key"},
        {"nested": {object(): "unsupported key"}},
        {"nested": {"value": object()}},
        {"nested": [math.nan]},
        {"nested": [math.inf]},
        {"nested": ("tuple is not JSON metadata",)},
        {"nested": {"set"}},
    ],
)
def test_release_manifest_rejects_unsupported_metadata(metadata):
    with pytest.raises((TypeError, ValueError), match="JSON"):
        make_release_manifest(
            scenario_year=2029,
            scenario_block="jan_jun",
            rules=metadata,
            config={},
        )


def test_release_manifest_freezes_nested_metadata_and_detaches_inputs():
    rules = {"nested": {"items": [1, {"label": "before"}]}}
    config = {"options": ["a", "b"]}
    manifest = make_release_manifest(
        scenario_year=2029,
        scenario_block="jan_jun",
        rules=rules,
        config=config,
    )
    rules["nested"]["items"][1]["label"] = "after"
    config["options"].append("c")

    assert manifest.rules["nested"]["items"][1]["label"] == "before"
    assert manifest.config["options"] == ("a", "b")
    with pytest.raises(TypeError):
        manifest.rules["nested"]["new"] = True
    with pytest.raises(TypeError):
        manifest.config["options"][0] = "changed"
    assert manifest.to_dict() == {
        "release_version": RELEASE_VERSION,
        "environment_version": ENVIRONMENT_VERSION,
        "plan_schema_version": PLAN_SCHEMA_VERSION,
        "scenario_year": 2029,
        "scenario_block": "jan_jun",
        "rules": {"nested": {"items": [1, {"label": "before"}]}},
        "config": {"options": ["a", "b"]},
    }


def test_digest_tracks_canonical_payload_and_semantic_changes():
    first = make_release_manifest(
        scenario_year=2029,
        scenario_block="jan_jun",
        rules={"b": 2, "a": 1},
        config={"seed": 4},
    )
    equivalent = make_release_manifest(
        scenario_year=2029,
        scenario_block="jan_jun",
        rules={"a": 1, "b": 2},
        config={"seed": 4},
    )
    changed = make_release_manifest(
        scenario_year=2029,
        scenario_block="jan_jun",
        rules={"a": 1, "b": 3},
        config={"seed": 4},
    )

    assert first.digest() == equivalent.digest()
    assert first.digest() == stable_hash(first.to_dict())
    assert first.digest() != changed.digest()


@pytest.mark.parametrize("field", ["release_version", "environment_version", "plan_schema_version", "scenario_year", "scenario_block"])
def test_release_manifest_requires_version_and_scenario_fields(field):
    payload = make_release_manifest(
        scenario_year=2029,
        scenario_block="jan_jun",
        rules={},
        config={},
    ).to_dict()
    del payload[field]

    with pytest.raises(ValueError, match=field):
        ReleaseManifest.from_dict(payload)


def test_release_document_describes_the_implemented_research_release_contract():
    document = RELEASE_DOC.read_text(encoding="utf-8")

    required_terms = (
        "Phase 5",
        "phase5-release-v1",
        "phase5-environment-v1",
        "phase5-plan-schema-v1",
        "2027",
        "2028",
        "2029",
        "jan_jun",
        "jul_dec",
        "canonical JSON",
        "SHA-256",
        "replay",
        "privacy",
        "evaluator",
        "solver",
        "known limitations",
        "hourly operation",
        "full training",
    )

    missing = [term for term in required_terms if term.lower() not in document.lower()]
    assert not missing, f"release contract is missing: {missing}"


def test_release_document_includes_replay_and_manifest_examples():
    document = RELEASE_DOC.read_text(encoding="utf-8")

    for example in (
        "examples/rl_gym/single_agent_baseline.py",
        "examples/rl_gym/competitive_parallel.py",
        "PYTHONPATH=src:. python examples/rl_gym/single_agent_baseline.py \\",
        "PYTHONPATH=src:. python examples/rl_gym/competitive_parallel.py \\",
        "make_release_manifest(",
        "python -m pytest tests/test_rl_gym_release.py",
    ):
        assert example in document


def test_readme_points_to_authoritative_release_contract_and_future_work():
    document = README_DOC.read_text(encoding="utf-8")

    assert "[Phase 5 release contract](RELEASE.md)" in document
    assert "Phase 5 research release is implemented" in document
    assert "hourly operation" in document.lower()
    assert "full training" in document.lower()
    assert "deferred to later phases" not in document.lower()
    assert "Phase 5 -- Research release (deferred)" not in document
