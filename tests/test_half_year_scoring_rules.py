from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import create_half_year_scoring_rules

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("year", "round_name", "expected_horizon"),
    [
        (2027, "jan_jun", 4344),
        (2028, "jan_jun", 4368),
        (2029, "jan_jun", 4344),
        (2027, "jul_dec", 4416),
    ],
)
def test_rule_builder_derives_horizon_from_year_and_round(
    year: int, round_name: str, expected_horizon: int
):
    payload = create_half_year_scoring_rules.build_rules_for_year_season(
        year=year,
        round_name=round_name,
    )

    assert len(payload["it_value_per_mwh"]) == expected_horizon
    assert payload["time_step_hours_provenance"]["horizon_hours"] == expected_horizon
    assert payload["time_step_hours_provenance"]["calendar_block"] == round_name


def test_half_year_rules_have_complete_hourly_vectors_and_revised_economics():
    for filename, horizon in (
        ("dispatch_scoring_rules_jan_jun_v1.json", 4368),
        ("dispatch_scoring_rules_jul_dec_v1.json", 4416),
    ):
        path = ROOT / "data" / "teaching" / filename
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert len(payload["it_value_per_mwh"]) == horizon
        assert set(payload["it_value_per_mwh"]) == {1500.0}
        assert payload["time_step_hours"] == 1.0
        assert payload["generator_capital_cost_per_mw"] == 1_300_000.0
        assert payload["site_cost_per_mw"] == 20_000_000.0
        assert payload["storage_capital_cost_per_mw"] == 350_000.0
        assert payload["storage_capital_cost_per_mwh"] == 200_000.0
        assert "storage_operating_cost_per_mwh" not in payload
        assert payload["team_generator_cost_curve"] == [
            {"capacity_fraction": 0.25, "marginal_cost_per_mwh": 60.0},
            {"capacity_fraction": 0.5, "marginal_cost_per_mwh": 100.0},
            {"capacity_fraction": 0.25, "marginal_cost_per_mwh": 160.0},
        ]
        assert payload["economic_profile_artifact"]["status"] == "required"
        assert payload["economic_profile_artifact"]["it_value_json_field"] == "it_value_per_mwh"


def test_half_year_rules_record_storage_provenance_and_generation_method():
    path = ROOT / "data" / "teaching" / "dispatch_scoring_rules_jan_jun_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["time_step_hours_provenance"]["interpretation"] == "literal hourly interval"
    assert payload["storage_cost_provenance"]["four_hour_total_cost_per_kw"] == 1150.0
    assert "variable_om_proxy_per_mwh" not in payload["storage_cost_provenance"]
    assert payload["it_value_provenance"]["profile_name"] == "AI-intensive"
    assert payload["it_value_provenance"]["representation"] == "explicit full-horizon profile"
    assert payload["economic_profile_artifact"]["files"] == {
        "lmp_npz": "economic_profiles.npz",
        "economic_profile_json": "economic_profile.json",
        "manifest": "economic_profile_manifest.json",
    }
