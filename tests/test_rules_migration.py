from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _rules(filename: str) -> dict:
    return json.loads(
        (ROOT / "data" / "teaching" / filename).read_text(encoding="utf-8")
    )


def test_production_rules_identify_full_horizon_it_profiles():
    for filename, horizon in (
        ("dispatch_scoring_rules_jan_jun_6h_v1.json", 728),
        ("dispatch_scoring_rules_jul_dec_6h_v1.json", 736),
    ):
        payload = _rules(filename)
        assert len(payload["it_value_per_mwh"]) == horizon
        assert payload["it_value_provenance"]["horizon_hours"] == horizon
        assert payload["it_value_provenance"]["method"] == "explicit constant full-horizon instructor profile"
        assert "energy_price_per_mwh" not in payload
        assert "vector_generation" not in payload
        contract = payload["economic_profile_artifact"]
        assert contract["files"] == {
            "lmp_npz": "economic_profiles.npz",
            "economic_profile_json": "economic_profile.json",
            "manifest": "economic_profile_manifest.json",
        }
        assert contract["npz_array"] == "lmp_per_mwh"


def test_production_rules_document_lmp_migration_boundary():
    for filename in (
        "dispatch_scoring_rules_jan_jun_6h_v1.json",
        "dispatch_scoring_rules_jul_dec_6h_v1.json",
    ):
        payload = _rules(filename)
        assert payload["economic_profile_artifact"]["source_type"] == "baseline ACTIVSg2000 DCOPF LMP artifact"
        assert payload["economic_profile_artifact"]["status"] == "optional_reference"
        assert "Zenodo" not in payload["description"]
