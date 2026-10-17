from __future__ import annotations

import json
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).parents[1]
PRODUCTION_BATCH_DOCS = (
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "docs" / "ERCOT_INSTRUCTOR_BATCH_EVALUATION.md",
    REPOSITORY_ROOT / "docs" / "INSTRUCTOR_REPOSITORY_GUIDE.md",
)
ACTIVE_SETTLEMENT_DOCS = (
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "docs" / "ERCOT_INSTRUCTOR_BATCH_EVALUATION.md",
    REPOSITORY_ROOT / "docs" / "INSTRUCTOR_REPOSITORY_GUIDE.md",
    REPOSITORY_ROOT / "docs" / "HYPERPARAMETERS.md",
)
ACTIVE_DISPATCH_RULES = (
    REPOSITORY_ROOT / "data" / "teaching" / "dispatch_scoring_rules_jan_jun_6h_v1.json",
    REPOSITORY_ROOT / "data" / "teaching" / "dispatch_scoring_rules_jul_dec_6h_v1.json",
)


@pytest.mark.parametrize("path", PRODUCTION_BATCH_DOCS)
def test_production_batch_docs_describe_submission_number_modes(path: Path):
    text = path.read_text(encoding="utf-8")

    assert "--submission-number 1" in text
    assert "--submission-number 2" in text
    assert "not a CSV" in text or "not a CSV column" in text






















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
        REPOSITORY_ROOT / "docs" / "HYPERPARAMETERS.md",
        REPOSITORY_ROOT / "docs" / "student_tutorial" / "main.tex",
    ):
        text = path.read_text(encoding="utf-8").lower()
        assert "exogenous" not in text






def test_active_six_hour_rules_are_released_without_economic_drift():
    expected_horizons = {728, 736}

    for path in ACTIVE_DISPATCH_RULES:
        rules = json.loads(path.read_text(encoding="utf-8"))

        assert rules["status"] == "released"
        assert rules["time_step_hours"] == 6.0
        assert len(rules["it_value_per_mwh"]) in expected_horizons
        assert set(rules["it_value_per_mwh"]) == {6500.0}
        assert rules["generator_capital_cost_per_mw"] == 1_500_000.0
        assert rules["site_cost_per_mw"] == 25_000_000.0
        assert rules["default_generator_cost_per_mwh"] == 150.0
        assert rules["storage_capital_cost_per_mw"] == 400_000.0
        assert rules["storage_capital_cost_per_mwh"] == 250_000.0
        assert [
            (item["capacity_fraction"], item["marginal_cost_per_mwh"])
            for item in rules["team_generator_cost_curve"]
        ] == [(0.25, 80.0), (0.5, 150.0), (0.25, 250.0)]




