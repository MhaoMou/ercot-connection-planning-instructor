#!/usr/bin/env python3
"""Generate the two literal-hour scoring-rule files for the course game."""

from __future__ import annotations

import json
from pathlib import Path


AI_INTENSIVE_VALUE_PER_MWH = 1500.0


def build_rules(*, label: str, horizon: int) -> dict[str, object]:
    return {
        "rules_version": f"dispatch-scoring-{label}-v1",
        "status": "instructor_review_required",
        "description": (
            "Literal one-hour scoring rules for the ACTIVSg2000 dispatch game. "
            "The AI-intensive IT-service value is an explicit full-horizon "
            "profile. Scored energy purchases settle against LMPs from the "
            "joint dispatch; these are instructional model outputs, not an "
            "official ERCOT market-price forecast."
        ),
        "it_value_per_mwh": [AI_INTENSIVE_VALUE_PER_MWH] * horizon,
        "time_step_hours": 1.0,
        "generator_capital_cost_per_mw": 1_300_000.0,
        "site_cost_per_mw": 20_000_000.0,
        "infeasible_score": 0.0,
        "default_generator_cost_per_mwh": 100.0,
        "storage_capital_cost_per_mw": 350_000.0,
        "storage_capital_cost_per_mwh": 200_000.0,
        "time_step_hours_provenance": {
            "interpretation": "literal hourly interval",
            "horizon_hours": horizon,
            "calendar_block": label,
        },
        "storage_cost_provenance": {
            "currency_year": "2022 USD for capital benchmarks; nominal instructional coefficients",
            "power_capital_cost_per_kw": 350.0,
            "energy_capital_cost_per_kwh": 200.0,
            "four_hour_total_cost_per_kw": 1150.0,
            "basis": "approved four-hour storage split calibrated to EIA and NREL utility-scale battery benchmarks",
            "sources": [
                "https://www.eia.gov/electricity/generatorcosts/",
                "https://www.eia.gov/analysis/studies/powerplants/capitalcost/pdf/capcost_assumption.pdf",
                "https://atb.nrel.gov/electricity/2021/utility-scale_battery_storage",
                "https://atb.nrel.gov/electricity/2024/2023/utility-scale_battery_storage",
            ],
            "evaluator_mapping": "Storage operating cost is not charged.",
        },
        "it_value_provenance": {
            "basis": "instructional value of computational service",
            "profile_name": "AI-intensive",
            "base_value_per_mwh": AI_INTENSIVE_VALUE_PER_MWH,
            "method": "explicit constant full-horizon instructor profile",
            "representation": "explicit full-horizon profile",
            "horizon_hours": horizon,
            "units": "USD/MWh of served computational load",
            "source_category": "INSTRUCTOR-CHOSEN",
        },
        "economic_profile_artifact": {
            "status": "required",
            "source_type": "baseline ACTIVSg2000 DCOPF LMP artifact",
            "directory_pattern": "data/processed/activsg2000_scenarios_v1/<year>/<block>",
            "files": {
                "lmp_npz": "economic_profiles.npz",
                "economic_profile_json": "economic_profile.json",
                "manifest": "economic_profile_manifest.json",
            },
            "npz_array": "lmp_per_mwh",
            "raw_dual_array": "raw_balance_dual",
            "it_value_json_field": "it_value_per_mwh",
            "it_value_provenance_field": "it_value_provenance",
            "settlement_basis": "LMP from the selected joint dispatch at the selected bus",
            "hash_field": "artifact_sha256",
        },
    }


def main() -> None:
    output_dir = Path(__file__).resolve().parents[1] / "data" / "teaching"
    for label, horizon in (("jan_jun", 4368), ("jul_dec", 4416)):
        path = output_dir / f"dispatch_scoring_rules_{label}_v1.json"
        path.write_text(json.dumps(build_rules(label=label, horizon=horizon), indent=2) + "\n", encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
