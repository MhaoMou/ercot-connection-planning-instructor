from __future__ import annotations

import csv
import json

import pytest

from ercot_game.csv_submission import CANONICAL_CSV_COLUMNS
from ercot_game.csv_validator import main, validate_csv_submission


def _artifact(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "bus.csv").write_text("bus_id\n3002041\n3002042\n", encoding="utf-8")
    (artifact / "generator_limits.csv").write_text(
        "generator_id,marginal_cost_c1_per_mwh\nG1,42.5\n", encoding="utf-8"
    )
    return artifact


def _submission(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CANONICAL_CSV_COLUMNS)
        writer.writerows(rows)


def test_validator_accepts_valid_multi_site_submission_and_reports_summary(tmp_path):
    artifact = _artifact(tmp_path)
    path = tmp_path / "team.csv"
    _submission(
        path,
        [
            ["team-a", "site-north", "3002041", 40, 20, 10, 40],
            ["team-a", "site-south", "3002042", 15, 0, 0, 0],
        ],
    )

    report = validate_csv_submission(path, artifact_dir=artifact, horizon=4368)

    assert report.valid
    assert report.team_id == "team-a"
    assert report.submission_id is None
    assert report.row_count == 2
    assert report.site_count == 2
    assert report.bus_count == 2
    assert report.generator_count == 1
    assert report.storage_count == 1
    assert report.issues == ()


def test_validator_rejects_legacy_header_by_default_with_example(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text(
        "team_id,submission_id,period,site_id,load_mw,generator_id,generator_capacity_mw,storage_id,storage_power_mw,storage_energy_mwh\n"
        "team-a,half-year-1,1,3002041,40,,,,,\n",
        encoding="utf-8",
    )

    report = validate_csv_submission(path, bus_ids={"3002041"}, horizon=1)

    assert not report.valid
    assert "team_id,site_id,bus_id,connection_capacity_mw" in report.issues[0].message
    assert "example row" in report.issues[0].message


def test_validator_collects_catalog_and_capacity_errors(tmp_path):
    artifact = _artifact(tmp_path)
    path = tmp_path / "bad.csv"
    _submission(path, [["team-a", "site-1", "UNKNOWN", -1, 2, 1, 3]])

    report = validate_csv_submission(path, artifact_dir=artifact, horizon=2)

    assert not report.valid
    messages = "\n".join(issue.message for issue in report.issues)
    assert "unknown scenario bus UNKNOWN" in messages
    assert "connection_capacity_mw must be non-negative" in messages
    assert "generator capacity cannot exceed connection capacity" in messages
    assert "storage energy must equal four times storage power" in messages


@pytest.mark.parametrize(
    "row",
    (
        ["team a", "site-1", "3002041", 40, 0, 0, 0],
        ["team-a", "site 1", "3002041", 40, 0, 0, 0],
        ["team-a", "site-1", "3002041", 40, 0, "nan", 0],
        ["team-a", "site-1", "3002041", 40, 0, 0, 1],
    ),
    ids=("bad-team-id", "bad-site-id", "nonfinite-capacity", "bad-storage-ratio"),
)
def test_validator_rejects_malformed_or_adversarial_site_rows(tmp_path, row):
    path = tmp_path / "bad.csv"
    _submission(path, [row])

    report = validate_csv_submission(path, bus_ids={"3002041"}, horizon=4368)

    assert not report.valid
    assert report.issues


def test_validator_rejects_duplicate_sites(tmp_path):
    path = tmp_path / "duplicate.csv"
    _submission(
        path,
        [
            ["team-a", "site-1", "3002041", 40, 0, 0, 0],
            ["team-a", "site-1", "3002042", 30, 0, 0, 0],
        ],
    )

    report = validate_csv_submission(path, bus_ids={"3002041", "3002042"})

    assert not report.valid
    assert any("duplicate site_id" in issue.message for issue in report.issues)


def test_cli_accepts_compact_submission_without_hourly_rows(tmp_path, capsys):
    artifact = _artifact(tmp_path)
    path = tmp_path / "team.csv"
    _submission(path, [["team-a", "site-1", "3002041", 40, 20, 0, 0]])

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
    assert payload["row_count"] == 1
    assert payload["site_count"] == 1


def test_cli_accepts_canonical_submission_with_only_file(tmp_path, capsys):
    path = tmp_path / "team.csv"
    _submission(path, [["team-a", "site-1", "3002041", 40, 20, 0, 0]])

    exit_code = main(["--file", str(path)])

    assert exit_code == 0
    assert "VALID:" in capsys.readouterr().out


def test_validator_reports_malformed_quoting_as_file_error(tmp_path):
    path = tmp_path / "malformed.csv"
    path.write_text(
        ",".join(CANONICAL_CSV_COLUMNS) + "\n"
        'team-a,site-1,3002041,"40,0,0\n',
        encoding="utf-8",
    )

    report = validate_csv_submission(path, bus_ids={"3002041"}, horizon=1)

    assert not report.valid
    assert any("malformed CSV" in issue.message for issue in report.issues)


def test_validator_reports_oversized_field_as_file_error(tmp_path):
    path = tmp_path / "oversized.csv"
    path.write_text(
        ",".join(CANONICAL_CSV_COLUMNS) + "\n"
        + "team-a,site-1,3002041," + ("9" * 131073) + ",0,0,0\n",
        encoding="utf-8",
    )

    report = validate_csv_submission(path, bus_ids={"3002041"}, horizon=1)

    assert not report.valid
    assert any("malformed CSV" in issue.message for issue in report.issues)


def test_validator_does_not_require_public_generator_catalog_for_team_assets(tmp_path):
    artifact = _artifact(tmp_path)
    (artifact / "generator_limits.csv").write_text(
        "generator_id,marginal_cost_c1_per_mwh\nG1,nan\n", encoding="utf-8"
    )
    path = tmp_path / "team.csv"
    _submission(path, [["team-a", "site-1", "3002041", 40, 20, 0, 0]])

    report = validate_csv_submission(path, artifact_dir=artifact, horizon=1)
    assert report.valid
