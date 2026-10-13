from __future__ import annotations

import json
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).parents[1]
PRODUCTION_BATCH_DOCS = (
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "docs" / "ERCOT_INSTRUCTOR_BATCH_EVALUATION.md",
    REPOSITORY_ROOT / "docs" / "INSTRUCTOR_REPOSITORY_GUIDE.md",
    REPOSITORY_ROOT / "docs" / "instructor_manual" / "main.tex",
)
SCENARIO_PACKAGE_ROOT = REPOSITORY_ROOT / "data" / "processed" / "activsg2000_scenarios_v1"
SCENARIO_BLOCK_MANIFESTS = tuple(
    sorted(SCENARIO_PACKAGE_ROOT.glob("20??/*/manifest.json"))
)
ACTIVE_SETTLEMENT_DOCS = (
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "docs" / "ERCOT_INSTRUCTOR_BATCH_EVALUATION.md",
    REPOSITORY_ROOT / "docs" / "INSTRUCTOR_REPOSITORY_GUIDE.md",
    REPOSITORY_ROOT / "docs" / "student_manual" / "HYPERPARAMETERS.md",
    REPOSITORY_ROOT / "docs" / "student_manual" / "main.tex",
    REPOSITORY_ROOT / "docs" / "instructor_manual" / "main.tex",
)
ACTIVE_DISPATCH_RULES = (
    REPOSITORY_ROOT / "data" / "teaching" / "dispatch_scoring_rules_jan_jun_v1.json",
    REPOSITORY_ROOT / "data" / "teaching" / "dispatch_scoring_rules_jul_dec_v1.json",
)


@pytest.mark.parametrize("path", PRODUCTION_BATCH_DOCS)
def test_production_batch_docs_describe_submission_number_modes(path: Path):
    text = path.read_text(encoding="utf-8")

    assert "--submission-number 1" in text
    assert "--submission-number 2" in text
    assert "not a CSV" in text or "not a CSV column" in text


def test_rl_roadmap_matches_current_student_csv_contract():
    text = (REPOSITORY_ROOT / "rl_gym" / "README.md").read_text(encoding="utf-8")

    assert "current student CSV has exactly seven columns" in text
    assert "no generator-ID or" in text
    assert "legacy" in text and "student CSV contract" in text


def test_rl_phase6_docs_describe_public_surfaces_and_boundaries():
    readme = (REPOSITORY_ROOT / "rl_gym" / "README.md").read_text(encoding="utf-8")

    for term in (
        "run_parallel_episode",
        "include_private_actions=True",
        "run_experiments",
        "aggregate_records",
        "train_policy",
        "public_sanitized",
        "private_replayable",
        "evaluator-mode none",
        "solver-free",
        "does not duplicate",
    ):
        assert term in readme


def test_rl_readme_labels_phase_one_through_six_and_current_phase_seven_gate():
    readme = (REPOSITORY_ROOT / "rl_gym" / "README.md").read_text(encoding="utf-8")

    assert "Phase 1–6" in readme
    assert "current Phase 7 gate" in readme


def test_hourly_environment_has_stable_public_exports():
    import rl_gym
    from rl_gym.hourly_env import HOURLY_ENVIRONMENT_VERSION, HourlyOperationEnv

    assert rl_gym.HourlyOperationEnv is HourlyOperationEnv
    assert rl_gym.HOURLY_ENVIRONMENT_VERSION == HOURLY_ENVIRONMENT_VERSION
    assert "HourlyOperationEnv" in rl_gym.__all__
    assert "HOURLY_ENVIRONMENT_VERSION" in rl_gym.__all__
    assert not any(name.startswith("_") for name in rl_gym.__all__)


def test_rl_hourly_readme_documents_frozen_plan_usage_and_boundaries():
    readme = (REPOSITORY_ROOT / "rl_gym" / "README.md").read_text(encoding="utf-8")

    for term in (
        "from rl_gym import HourlyOperationEnv, HOURLY_ENVIRONMENT_VERSION",
        "frozen",
        "`RLPlanState`",
        "for step_index in range(10):",
        'assert info["transition_count"] == step_index + 1',
        "generator_output_mw",
        "storage_charge_mw",
        "storage_discharge_mw",
        "| 2027 | 4,344 | 4,416 |",
        "| 2028 | 4,368 | 4,416 |",
        "| 2029 | 4,344 | 4,416 |",
        "default local reward is zero",
        "no curtailment action",
        "one-time evaluator callback",
        "public_sanitized",
        "private_record",
        "not replayable",
    ):
        assert term in readme


def test_rl_phase7_gate_records_only_evidenced_hourly_criteria():
    gate = (REPOSITORY_ROOT / "rl_gym" / "PHASE7_HOURLY_GATE.md").read_text(encoding="utf-8")
    gate_lower = gate.lower()

    for term in (
        '"generator_output_mw"',
        '"storage_charge_mw"',
        '"storage_discharge_mw"',
        "solver-free exogenous schema",
        "scenario_year/scenario_block",
        "does not provide benchmark evidence",
        "solver parity remains dependency-limited",
        "private_record",
    ):
        assert term.lower() in gate_lower
    assert "- [x] Action and observation spaces are fixed" in gate
    assert "- [x] Public observations and public replay/report artifacts contain no" in gate
    assert "- [x] Same release manifest, seed, and action trace reproduce" in gate
    assert "- [x] Reward accounting demonstrates one-time terminal score handling" in gate
    assert "- [ ] Computational budgets report exact horizon" in gate
    assert "- [ ] Production evaluator parity passes" in gate


def test_rl_phase7_hourly_gate_has_required_contract_sections():
    gate = (REPOSITORY_ROOT / "rl_gym" / "PHASE7_HOURLY_GATE.md").read_text(encoding="utf-8")

    for heading in (
        "## 1. Action contract",
        "## 2. Observation contract",
        "## 3. Reward accounting",
        "## 4. Computational budget",
        "## 5. Evaluator parity",
        "## 6. Acceptance criteria",
        "## 7. Non-goals",
    ):
        assert heading in gate
    for term in (
        "exactly once",
        "one transition per released operating hour",
        "cross-team private",
        "production evaluator",
        "git diff --check",
        "network, or scoring modifications",
    ):
        assert term in gate


def test_activsg2000_manifest_distinguishes_missing_highspy_from_validation_failure():
    manifest_path = REPOSITORY_ROOT / "data" / "processed" / "activsg2000_scenarios_v1" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validation = manifest["validation"]

    assert manifest["validation_status"] == "unverified_dependency"
    assert validation["classification"] == "dependency_limited"
    assert validation["failed_hour_count"] == 0
    assert validation["unverified_hour_count"] > 0
    assert "highspy" in validation["reason"]
    assert validation["release_status"] == "blocked_pending_highspy_validation"
    assert "successful HiGHS-backed validation" in validation["required_before_release"]


def test_activsg2000_root_manifest_aggregates_all_six_attempted_blocks():
    manifest_path = SCENARIO_PACKAGE_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validation = manifest["validation"]
    block_manifests = [json.loads(path.read_text(encoding="utf-8")) for path in SCENARIO_BLOCK_MANIFESTS]
    expected_hours = sum(block["horizon"] for block in block_manifests)

    assert len(block_manifests) == 6
    assert expected_hours == 26304
    assert validation["hour_count"] == len(block_manifests)
    assert validation["expected_hour_count"] == expected_hours
    assert validation["passed_hour_count"] == 0
    assert validation["failed_hour_count"] == 0
    assert validation["unverified_hour_count"] == len(block_manifests)
    assert len(validation["unverified"]) == len(block_manifests)
    assert {
        (event["block"], event["period_index"])
        for event in validation["unverified"]
    } == {(block["block"], 0) for block in block_manifests}
    assert all("highspy" in event["reason"] for event in validation["unverified"])


def test_all_activsg2000_block_manifests_are_unverified_without_highspy():
    assert len(SCENARIO_BLOCK_MANIFESTS) == 6
    for path in SCENARIO_BLOCK_MANIFESTS:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["validation_status"] == "unverified_dependency"
        assert "highspy" in manifest["validation_reason"]


@pytest.mark.parametrize("path", ACTIVE_SETTLEMENT_DOCS)
def test_active_settlement_docs_match_joint_dispatch_modes(path: Path):
    text = path.read_text(encoding="utf-8")
    assert "Submission 1" in text
    assert "energy-purchase cost" in text
    assert "Submission 2" in text
    assert "joint dispatch" in text
    assert "LMP" in text


def test_active_student_lmp_docs_do_not_call_joint_dispatch_prices_exogenous():
    for path in (
        REPOSITORY_ROOT / "docs" / "student_manual" / "HYPERPARAMETERS.md",
        REPOSITORY_ROOT / "docs" / "student_manual" / "main.tex",
    ):
        text = path.read_text(encoding="utf-8").lower()
        assert "exogenous" not in text


def test_dispatch_calibration_documents_all_half_year_horizons():
    text = (REPOSITORY_ROOT / "docs" / "ERCOT_DISPATCH_SCORING_CALIBRATION.md").read_text(
        encoding="utf-8"
    )
    assert "4,344 hours" in text
    assert "4,368 hours" in text
    assert "4,416 hours" in text
    assert "non-leap" in text
    assert "leap" in text


def test_calibration_note_labels_superseded_values_and_points_to_active_economics():
    text = (REPOSITORY_ROOT / "docs" / "ERCOT_DATA_CENTER_VALUE_CALIBRATION.md").read_text(encoding="utf-8")
    paragraphs = text.split("\n\n")

    assert "$1.3m/MW" in text
    assert "$1500/MWh" in text
    assert "$85,000/MW-year" in text
    assert "$125/MWh" in text
    for value in ("$85,000/MW-year", "$125/MWh"):
        containing = [paragraph.lower() for paragraph in paragraphs if value.lower() in paragraph.lower()]
        assert containing
        assert all("historical" in paragraph and "superseded" in paragraph for paragraph in containing)


def test_active_dispatch_rules_are_pending_highspy_validation_without_economic_drift():
    expected_horizons = {4368, 4416}

    for path in ACTIVE_DISPATCH_RULES:
        rules = json.loads(path.read_text(encoding="utf-8"))

        assert rules["status"] == "blocked_pending_highspy_validation"
        assert rules["status"] != "instructor_review_required"
        assert len(rules["it_value_per_mwh"]) in expected_horizons
        assert set(rules["it_value_per_mwh"]) == {1500.0}
        assert rules["generator_capital_cost_per_mw"] == 1_300_000.0
        assert rules["site_cost_per_mw"] == 20_000_000.0
        assert rules["default_generator_cost_per_mwh"] == 100.0
        assert rules["storage_capital_cost_per_mw"] == 350_000.0
        assert rules["storage_capital_cost_per_mwh"] == 200_000.0
        assert [
            (item["capacity_fraction"], item["marginal_cost_per_mwh"])
            for item in rules["team_generator_cost_curve"]
        ] == [(0.25, 60.0), (0.5, 100.0), (0.25, 160.0)]


def test_release_docs_distinguish_canonical_source_control_from_validation():
    guide = (REPOSITORY_ROOT / "docs" / "INSTRUCTOR_REPOSITORY_GUIDE.md").read_text(
        encoding="utf-8"
    )
    normalized_guide = " ".join(guide.lower().split())
    package_lines = [
        line.lower()
        for line in guide.splitlines()
        if "scenario package" in line.lower() or "scenario block" in line.lower()
    ]

    assert "canonical" in normalized_guide
    assert "source-controlled" in normalized_guide
    assert "release validation is pending" in normalized_guide
    assert "not validated" in normalized_guide
    assert package_lines
    assert all("validated" not in line for line in package_lines)

    calibration = (
        REPOSITORY_ROOT / "docs" / "ERCOT_DISPATCH_SCORING_CALIBRATION.md"
    ).read_text(encoding="utf-8")
    normalized_calibration = " ".join(calibration.lower().split())
    assert "canonical checked-in calibration" in normalized_calibration
    assert "release validation pending" in normalized_calibration
    assert "highspy" in normalized_calibration
    assert "complete highs-backed validation of all 26,304 hourly states" in normalized_calibration
    assert "instructor release approval" in normalized_calibration


def test_student_manual_points_to_checked_in_profiles_and_instructor_control():
    text = (REPOSITORY_ROOT / "docs" / "student_manual" / "README.md").read_text(
        encoding="utf-8"
    )
    lowered = text.lower()

    assert "data/teaching/dispatch_scoring_rules_" in text
    assert "data/processed/activsg2000_scenarios_v1/" in text
    assert "canonical" in lowered
    assert "instructor-controlled" in lowered
    assert "hourly price/value vectors" not in lowered
    assert "storage-cost coefficients before" not in lowered
    assert "manually update" not in lowered
