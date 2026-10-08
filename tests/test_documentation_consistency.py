from __future__ import annotations

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
