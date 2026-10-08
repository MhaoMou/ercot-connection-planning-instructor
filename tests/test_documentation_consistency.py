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
