from __future__ import annotations

import csv
import json

import pytest

from ercot_game.csv_submission import BUS_CSV_COLUMNS
from ercot_game.csv_validator import main, validate_csv_submission


def _artifact(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "bus.csv").write_text(
        "bus_id\n3002041\n3002042\n",
        encoding="utf-8",
    )
    (artifact / "generator_limits.csv").write_text(
        "generator_id,marginal_cost_c1_per_mwh\nG1,42.5\n",
        encoding="utf-8",
    )
    return artifact


def _submission(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(BUS_CSV_COLUMNS)
        writer.writerows(rows)


def test_validator_accepts_valid_submission_and_reports_summary(tmp_path):
    artifact = _artifact(tmp_path)
    path = tmp_path / "team.csv"
    _submission(path, [["team-a", "half-year-1", 1, "3002041", 40, "G1", 20, "S1", 10, 20]])

    report = validate_csv_submission(path, artifact_dir=artifact, horizon=1)

    assert report.valid
    assert report.team_id == "team-a"
    assert report.submission_id == "half-year-1"
    assert report.row_count == 1
    assert report.bus_count == 1
    assert report.generator_count == 1
    assert report.storage_count == 1
    assert report.issues == ()


def test_validator_rejects_legacy_site_header_by_default(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text(
        "team_id,submission_id,period,site_id,load_mw,generator_id,generator_capacity_mw,storage_id,storage_power_mw,storage_energy_mwh\n"
        "team-a,half-year-1,1,3002041,40,,,,,\n",
        encoding="utf-8",
    )

    report = validate_csv_submission(path, bus_ids={"3002041"}, horizon=1)

    assert not report.valid
    assert any("bus_id" in issue.message and "exactly" in issue.message for issue in report.issues)


def test_validator_collects_catalog_and_value_errors(tmp_path):
    artifact = _artifact(tmp_path)
    path = tmp_path / "bad.csv"
    _submission(
        path,
        [
            ["team-a", "half-year-1", 0, "UNKNOWN", -1, "NOPE", -2, "", "", ""],
        ],
    )

    report = validate_csv_submission(path, artifact_dir=artifact, horizon=2)

    assert not report.valid
    messages = "\n".join(issue.message for issue in report.issues)
    assert "period must be in 1..2" in messages
    assert "unknown scenario bus UNKNOWN" in messages
    assert "load_mw must be non-negative" in messages
    assert "generator capacity must be non-negative" in messages


def test_cli_returns_status_and_supports_json_output(tmp_path, capsys):
    artifact = _artifact(tmp_path)
    path = tmp_path / "team.csv"
    _submission(
        path,
        [
            ["team-a", "half-year-1", period, "3002041", 40, "G1", 20, "", "", ""]
            for period in range(1, 4369)
        ],
    )

    exit_code = main(
        [
            "--file",
            str(path),
            "--artifact-dir",
            str(artifact),
            "--horizon",
            "4368",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["row_count"] == 4368


def test_validator_rejects_partial_full_horizon_submission(tmp_path):
    artifact = _artifact(tmp_path)
    path = tmp_path / "partial.csv"
    _submission(path, [["team-a", "half-year-1", 1, "3002041", 40, "", "", "", "", ""]])

    report = validate_csv_submission(path, artifact_dir=artifact, horizon=2)

    assert not report.valid
    assert any("must cover every period 1..2" in issue.message for issue in report.issues)


def test_validator_reports_malformed_quoting_as_file_error(tmp_path):
    path = tmp_path / "malformed.csv"
    path.write_text(
        ",".join(BUS_CSV_COLUMNS) + "\n"
        'team-a,half-year-1,1,3002041,"40,G1,20,,,,\n',
        encoding="utf-8",
    )

    report = validate_csv_submission(path, bus_ids={"3002041"}, horizon=1)

    assert not report.valid
    assert any("malformed CSV" in issue.message for issue in report.issues)


def test_validator_reports_oversized_field_as_file_error(tmp_path):
    path = tmp_path / "oversized.csv"
    path.write_text(
        ",".join(BUS_CSV_COLUMNS) + "\n"
        + ("team-a,half-year-1,1,3002041," + ("9" * 131073) + ",,,,,\n"),
        encoding="utf-8",
    )

    report = validate_csv_submission(path, bus_ids={"3002041"}, horizon=1)

    assert not report.valid
    assert any("malformed CSV" in issue.message for issue in report.issues)


def test_validator_does_not_require_public_generator_catalog_for_team_assets(tmp_path):
    artifact = _artifact(tmp_path)
    (artifact / "generator_limits.csv").write_text(
        "generator_id,marginal_cost_c1_per_mwh\nG1,nan\n",
        encoding="utf-8",
    )
    path = tmp_path / "team.csv"
    _submission(path, [["team-a", "half-year-1", 1, "3002041", 40, "G1", 20, "", "", ""]])

    report = validate_csv_submission(path, artifact_dir=artifact, horizon=1)
    assert report.valid
