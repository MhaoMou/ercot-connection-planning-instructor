import json
import os
import subprocess
import sys
from pathlib import Path

import nbformat
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TUTORIAL_ROOT = REPO_ROOT / "docs" / "student_tutorial"
NOTEBOOK_ROOT = TUTORIAL_ROOT / "notebooks"
RUNNER = TUTORIAL_ROOT / "scripts" / "execute_notebooks.py"
VALIDATOR = TUTORIAL_ROOT / "scripts" / "validate_tutorial_examples.py"
EXPECTED_NOTEBOOKS = [
    "01_dataset_inspection.ipynb",
    "02_network_visualization.ipynb",
    "03_three_bus_dcopf.ipynb",
    "04_storage_soc.ipynb",
    "05_csv_validation.ipynb",
    "06_student_preview.ipynb",
    "07_strategy_ablation.ipynb",
]


def test_all_required_notebooks_have_stable_fixture_first_contract():
    assert sorted(path.name for path in NOTEBOOK_ROOT.glob("*.ipynb")) == EXPECTED_NOTEBOOKS
    for path in NOTEBOOK_ROOT.glob("*.ipynb"):
        notebook = nbformat.read(path, as_version=4)
        assert len(notebook.cells) >= 5
        assert notebook.cells[0].cell_type == "markdown"
        assert "objective" in notebook.cells[0].source.casefold()
        assert notebook.cells[1].cell_type == "code"
        assert "TUTORIAL_ROOT" in notebook.cells[1].source
        assert notebook.cells[2].cell_type == "code"
        assert "environment" in notebook.cells[2].source.casefold()
        assert notebook.cells[3].cell_type == "code"
        assert "assert" in notebook.cells[3].source
        assert notebook.cells[4].cell_type == "code"
        assert "OUTPUT" in notebook.cells[4].source


def test_all_notebook_cells_have_unique_explicit_ids():
    for path in NOTEBOOK_ROOT.glob("*.ipynb"):
        notebook = nbformat.read(path, as_version=4)
        ids = [cell.get("id") for cell in notebook.cells]
        assert all(isinstance(cell_id, str) and cell_id for cell_id in ids), path.name
        assert len(ids) == len(set(ids)), path.name


def test_notebooks_use_no_private_data_or_user_absolute_paths():
    forbidden = ("/Users/", "/home/", "C:\\Users\\", "private_team", "team_secret")
    for path in NOTEBOOK_ROOT.glob("*.ipynb"):
        text = path.read_text(encoding="utf-8")
        assert not any(value in text for value in forbidden)
        assert "fixture" in text.casefold()


def test_runner_fails_clearly_for_missing_input_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--input-dir", str(tmp_path / "missing"), "--output-dir", str(tmp_path / "runs")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "input notebook directory" in result.stderr


@pytest.mark.parametrize("output_name", ["notebooks", "notebooks/nested"])
def test_notebook_runner_rejects_input_output_overlap(tmp_path, output_name):
    output_dir = NOTEBOOK_ROOT if output_name == "notebooks" else NOTEBOOK_ROOT / "nested"
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--input-dir", str(NOTEBOOK_ROOT), "--output-dir", str(output_dir)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "overlap" in result.stderr.lower()


def test_notebook_runner_and_example_validator_smoke(tmp_path):
    output_dir = tmp_path / "runs"
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--input-dir", str(NOTEBOOK_ROOT), "--output-dir", str(output_dir)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert "executed 7 notebooks" in result.stdout
    validation = subprocess.run(
        [sys.executable, str(VALIDATOR), "--run-dir", str(output_dir)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert validation.returncode == 0, validation.stderr
    assert "validated notebook outputs" in validation.stdout


def test_notebook_runner_is_deterministic_across_clean_destinations(tmp_path):
    outputs = []
    for index in (1, 2):
        output_dir = tmp_path / f"run-{index}"
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--input-dir", str(NOTEBOOK_ROOT), "--output-dir", str(output_dir)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert result.returncode == 0, result.stderr
        outputs.append(
            {
                path.relative_to(output_dir).as_posix(): path.read_bytes()
                for path in output_dir.rglob("*")
                if path.is_file()
            }
        )
    assert outputs[0] == outputs[1]
