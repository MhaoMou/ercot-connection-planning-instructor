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
        assert set(payload["it_value_per_mwh"]) == {6500.0}
        assert payload["time_step_hours"] == 1.0
        assert payload["generator_capital_cost_per_mw"] == 1_500_000.0
        assert payload["site_cost_per_mw"] == 25_000_000.0
        assert payload["storage_capital_cost_per_mw"] == 400_000.0
        assert payload["storage_capital_cost_per_mwh"] == 250_000.0
        assert "storage_operating_cost_per_mwh" not in payload
        assert payload["team_generator_cost_curve"] == [
            {"capacity_fraction": 0.25, "marginal_cost_per_mwh": 80.0},
            {"capacity_fraction": 0.5, "marginal_cost_per_mwh": 150.0},
            {"capacity_fraction": 0.25, "marginal_cost_per_mwh": 250.0},
        ]
        assert payload["economic_profile_artifact"]["status"] == "required"
        assert payload["economic_profile_artifact"]["it_value_json_field"] == "it_value_per_mwh"


def test_half_year_rules_record_storage_provenance_and_generation_method():
    path = ROOT / "data" / "teaching" / "dispatch_scoring_rules_jan_jun_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["time_step_hours_provenance"]["interpretation"] == "literal hourly interval"
    assert payload["storage_cost_provenance"]["four_hour_total_cost_per_kw"] == 1400.0
    assert "variable_om_proxy_per_mwh" not in payload["storage_cost_provenance"]
    assert payload["it_value_provenance"]["profile_name"] == "AI-intensive"
    assert payload["it_value_provenance"]["representation"] == "explicit full-horizon profile"
    assert payload["economic_profile_artifact"]["files"] == {
        "lmp_npz": "economic_profiles.npz",
        "economic_profile_json": "economic_profile.json",
        "manifest": "economic_profile_manifest.json",
    }


@pytest.mark.parametrize("round_name", ["jan_jun", "jul_dec"])
def test_active_rule_metadata_matches_generator_and_scenario_manifests(round_name: str):
    year = 2028
    active_rules_path = ROOT / "data" / "teaching" / f"dispatch_scoring_rules_{round_name}_v1.json"
    block_manifest_path = (
        ROOT
        / "data"
        / "processed"
        / "activsg2000_scenarios_v1"
        / str(year)
        / round_name
        / "manifest.json"
    )
    package_manifest_path = (
        ROOT / "data" / "processed" / "activsg2000_scenarios_v1" / "manifest.json"
    )

    generated = create_half_year_scoring_rules.build_rules_for_year_season(
        year=year,
        round_name=round_name,
    )
    active_rules = json.loads(active_rules_path.read_text(encoding="utf-8"))
    block_manifest = json.loads(block_manifest_path.read_text(encoding="utf-8"))
    package_manifest = json.loads(package_manifest_path.read_text(encoding="utf-8"))

    assert generated == active_rules
    assert generated["status"] == package_manifest["validation"]["release_status"]

    provenance = generated["time_step_hours_provenance"]
    assert provenance["calendar_year"] == block_manifest["year"] == year
    assert provenance["calendar_block"] == round_name
    assert provenance["horizon_hours"] == block_manifest["horizon"]
    assert block_manifest["block"] == f"{year}-{round_name}"
    assert package_manifest["blocks"][block_manifest["block"]]["horizon"] == block_manifest["horizon"]
    assert package_manifest["blocks"][block_manifest["block"]]["calendar_block"] == round_name


def test_runtime_team_generator_cost_curve_matches_released_rules():
    from ercot_game.csv_submission import TEAM_GENERATOR_COST_CURVE

    for filename in (
        "dispatch_scoring_rules_jan_jun_v1.json",
        "dispatch_scoring_rules_jul_dec_v1.json",
    ):
        payload = json.loads(
            (ROOT / "data" / "teaching" / filename).read_text(encoding="utf-8")
        )
        assert TEAM_GENERATOR_COST_CURVE == tuple(
            (item["capacity_fraction"], item["marginal_cost_per_mwh"])
            for item in payload["team_generator_cost_curve"]
        )
