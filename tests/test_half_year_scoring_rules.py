from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_half_year_rules_have_complete_hourly_vectors_and_storage_costs():
    for filename, horizon in (
        ("dispatch_scoring_rules_jan_jun_v1.json", 4368),
        ("dispatch_scoring_rules_jul_dec_v1.json", 4416),
    ):
        path = ROOT / "data" / "teaching" / filename
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert len(payload["it_value_per_mwh"]) == horizon
        assert payload["time_step_hours"] == 1.0
        assert payload["storage_capital_cost_per_mw"] == 200_000.0
        assert payload["storage_capital_cost_per_mwh"] == 300_000.0
        assert payload["storage_operating_cost_per_mwh"] == 8.0
        assert payload["economic_profile_artifact"]["status"] == "required"
        assert payload["economic_profile_artifact"]["it_value_json_field"] == "it_value_per_mwh"


def test_half_year_rules_record_storage_provenance_and_generation_method():
    path = ROOT / "data" / "teaching" / "dispatch_scoring_rules_jan_jun_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["time_step_hours_provenance"]["interpretation"] == "literal hourly interval"
    assert payload["storage_cost_provenance"]["fixed_om_reference_per_kw_year"] == 40.0
    assert payload["it_value_provenance"]["method"] == "constant full-horizon instructor profile"
    assert payload["economic_profile_artifact"]["files"] == {
        "lmp_npz": "economic_profiles.npz",
        "economic_profile_json": "economic_profile.json",
        "manifest": "economic_profile_manifest.json",
    }
