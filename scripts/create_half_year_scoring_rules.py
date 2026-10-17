#!/usr/bin/env python3
"""Generate the two literal-hour scoring-rule files for the course game."""

from __future__ import annotations

import json
import argparse
from pathlib import Path

from ercot_game.calendars import half_year_calendar
from ercot_game.temporal_aggregation import aggregated_horizon


AI_INTENSIVE_VALUE_PER_MWH = 6500.0
TEAM_GENERATOR_COST_CURVE = (
    (0.25, 80.0),
    (0.50, 150.0),
    (0.25, 250.0),
)
DEFAULT_RULE_STATUS = "blocked_pending_highspy_validation"
RELEASED_RULE_STATUS = "released"


def build_rules(
    *,
    label: str,
    horizon: int,
    time_step_hours: float = 1.0,
    source_horizon: int | None = None,
    status: str = DEFAULT_RULE_STATUS,
) -> dict[str, object]:
    if not isinstance(time_step_hours, (int, float)) or isinstance(time_step_hours, bool) or time_step_hours <= 0:
        raise ValueError("time_step_hours must be finite and positive")
    source_horizon = horizon if source_horizon is None else source_horizon
    if not isinstance(source_horizon, int) or isinstance(source_horizon, bool) or source_horizon <= 0:
        raise ValueError("source_horizon must be a positive integer")
    time_step_provenance = {
        "interpretation": "six-hour aggregate interval" if time_step_hours == 6 else "literal hourly interval",
        "horizon_hours": source_horizon,
        "calendar_block": label,
    }
    if time_step_hours == 6:
        time_step_provenance.update(
            {
                "source_horizon_hours": source_horizon,
                "horizon_periods": horizon,
                "time_step_hours": float(time_step_hours),
            }
        )
    payload = {
        "rules_version": f"dispatch-scoring-{label}-{'6h-v1' if time_step_hours == 6 else 'v1'}",
        "status": status,
        "description": (
            f"{'Six-hour aggregate' if time_step_hours == 6 else 'Literal one-hour'} scoring rules for the ACTIVSg2000 dispatch game. "
            "The AI-intensive IT-service value is an explicit full-horizon "
            "profile. Scored energy purchases settle against LMPs from the "
            "joint dispatch; these are instructional model outputs, not an "
            "official ERCOT market-price forecast."
        ),
        "it_value_per_mwh": [AI_INTENSIVE_VALUE_PER_MWH] * horizon,
        "time_step_hours": float(time_step_hours),
        "generator_capital_cost_per_mw": 1_500_000.0,
        "site_cost_per_mw": 25_000_000.0,
        "infeasible_score": 0.0,
        "default_generator_cost_per_mwh": 150.0,
        "team_generator_cost_curve": [
            {
                "capacity_fraction": fraction,
                "marginal_cost_per_mwh": marginal_cost,
            }
            for fraction, marginal_cost in TEAM_GENERATOR_COST_CURVE
        ],
        "storage_capital_cost_per_mw": 400_000.0,
        "storage_capital_cost_per_mwh": 250_000.0,
        "time_step_hours_provenance": time_step_provenance,
        "storage_cost_provenance": {
            "currency_year": "2022 USD for capital benchmarks; nominal instructional coefficients",
            "power_capital_cost_per_kw": 400.0,
            "energy_capital_cost_per_kwh": 250.0,
            "four_hour_total_cost_per_kw": 1400.0,
            "basis": "rounded 2028 moderate four-hour projection from NREL's 2023 utility-scale battery cost update",
            "sources": [
                "https://www.eia.gov/electricity/generatorcosts/",
                "https://atb.nrel.gov/electricity/2023/residential_battery_storage/utility-scale_battery_storage",
                "https://www.nrel.gov/docs/fy23osti/85332.pdf",
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
            "source_category": "SOURCE-CALIBRATED",
            "source_note": "Rounded from a recent AI-compute willingness-to-pay estimate with a blended value near $6,350/MWh; cloud VM prices remain contextual rather than a direct tariff conversion.",
            "sources": [
                "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6322318",
                "https://www.iea.org/reports/energy-and-ai/executive-summary",
            ],
        },
    }
    # The daily exact cycle is part of the canonical six-hour release only.
    # Keep the literal-hour files as historical/reference rules with their
    # original terminal-floor semantics.
    if time_step_hours == 6:
        payload["storage_boundary_mode"] = "daily_exact_cycle"
    payload["economic_profile_artifact"] = {
        "status": "optional_reference" if time_step_hours == 6 else "required",
        "source_type": "baseline ACTIVSg2000 DCOPF LMP artifact",
        "directory_pattern": (
            "data/processed/activsg2000_scenarios_v2_6h/<year>/<block>"
            if time_step_hours == 6
            else "data/processed/activsg2000_scenarios_v1/<year>/<block>"
        ),
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
    }
    if time_step_hours == 6:
        payload["economic_profile_artifact"]["release_note"] = (
            "The canonical evaluator computes Submission 2 settlement LMPs from the joint dispatch; "
            "baseline artifacts are optional diagnostics and are not required for scoring."
        )
    return payload


def build_rules_for_year_season(
    *,
    year: int,
    round_name: str,
    time_step_hours: int = 1,
) -> dict[str, object]:
    """Build rules using the released calendar for one year and half-year."""

    calendar = half_year_calendar(year, round_name)
    horizon = aggregated_horizon(calendar.horizon, time_step_hours)
    payload = build_rules(
        label=round_name,
        horizon=horizon,
        time_step_hours=float(time_step_hours),
        source_horizon=calendar.horizon,
        status=RELEASED_RULE_STATUS if time_step_hours == 6 else DEFAULT_RULE_STATUS,
    )
    provenance = payload["time_step_hours_provenance"]
    assert isinstance(provenance, dict)
    provenance["calendar_year"] = year
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2028)
    parser.add_argument("--round", dest="round_name", choices=("jan_jun", "jul_dec"))
    parser.add_argument("--time-step-hours", type=int, choices=(1, 6), default=6)
    args = parser.parse_args()
    output_dir = Path(__file__).resolve().parents[1] / "data" / "teaching"
    round_names = (args.round_name,) if args.round_name else ("jan_jun", "jul_dec")
    for label in round_names:
        suffix = "_6h_v1" if args.time_step_hours == 6 else "_v1"
        path = output_dir / f"dispatch_scoring_rules_{label}{suffix}.json"
        path.write_text(
            json.dumps(
                build_rules_for_year_season(
                    year=args.year,
                    round_name=label,
                    time_step_hours=args.time_step_hours,
                ),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(path)


if __name__ == "__main__":
    main()
