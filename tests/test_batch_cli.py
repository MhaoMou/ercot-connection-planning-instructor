from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

from ercot_game import batch_evaluation
from ercot_game.batch_cli import main
from ercot_game.csv_submission import CSV_COLUMNS


def _write_submission(path: Path, team_id: str, *, load_mw: str = "2") -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {
                "team_id": team_id,
                "submission_id": "week-1",
                "period": "1",
                "site_id": "PANHANDLE",
                "load_mw": load_mw,
                "generator_id": "",
                "generator_capacity_mw": "",
                "storage_id": "",
                "storage_power_mw": "",
                "storage_energy_mwh": "",
            }
        )


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    submissions_dir = tmp_path / "submissions"
    submissions_dir.mkdir()
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    (artifact_dir / "bus.csv").write_text("bus_id\n3002041\n", encoding="utf-8")
    (artifact_dir / "generator_limits.csv").write_text(
        "generator_id,marginal_cost_c1_per_mwh\nG1,42.5\n",
        encoding="utf-8",
    )
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps(
            {
                "rules_version": "test-v1",
                "it_value_per_mwh": [200.0],
                "energy_price_per_mwh": [50.0],
                "infeasible_score": -999.0,
            }
        ),
        encoding="utf-8",
    )
    region_map_path = tmp_path / "regions.json"
    region_map_path.write_text(
        json.dumps({"regions": {"PANHANDLE": {"source_bus_id": "3002041"}}}),
        encoding="utf-8",
    )
    return submissions_dir, artifact_dir, rules_path, region_map_path


def _fake_dispatch(_artifact_dir, plans, *, period_indices):
    team_ids = tuple(sorted(plan.team_id for plan in plans))
    periods = tuple(
        SimpleNamespace(
            period_index=period_index,
            dcopf=SimpleNamespace(
                status="optimal",
                objective_cost=0.0,
                generation_mw={},
                flow_mw_by_branch={},
                balance_dual_by_bus={},
            ),
            submitted_load_mw_by_team={team_id: 2.0 for team_id in team_ids},
            generator_dispatch_mw_by_team={team_id: {} for team_id in team_ids},
        )
        for period_index in period_indices
    )
    return SimpleNamespace(team_ids=team_ids, periods=periods)


def _run_args(
    submissions_dir: Path,
    artifact_dir: Path,
    rules_path: Path,
    region_map_path: Path,
    output_dir: Path,
    *,
    period_indices: str = "0",
    source_period_offset: str | None = None,
) -> list[str]:
    args = [
        "run",
        "--submissions-dir",
        str(submissions_dir),
        "--artifact-dir",
        str(artifact_dir),
        "--rules",
        str(rules_path),
        "--output-dir",
        str(output_dir),
        "--horizon",
        "1",
        "--period-indices",
        period_indices,
        "--region-map",
        str(region_map_path),
    ]
    if source_period_offset is not None:
        args.extend(["--source-period-offset", source_period_offset])
    return args


def test_help_is_available_without_optional_solver_dependencies(capsys):
    assert main(["run", "--help"]) == 0

    captured = capsys.readouterr()
    assert "run" in captured.out
    assert "--submissions-dir" in captured.out
    assert "--workers" in captured.out
    assert "--projection-iterations" in captured.out


def test_missing_run_arguments_return_exit_code_two(capsys):
    assert main(["run"]) == 2

    assert "error:" in capsys.readouterr().err


def test_valid_run_uses_mocked_dispatch_and_writes_metadata(tmp_path, monkeypatch, capsys):
    submissions_dir, artifact_dir, rules_path, region_map_path = _write_inputs(tmp_path)
    _write_submission(submissions_dir / "team-a.csv", "team-a")
    output_dir = tmp_path / "output"
    monkeypatch.setattr(batch_evaluation, "evaluate_plans_with_prepared_dcopf", _fake_dispatch)

    assert main(
        _run_args(submissions_dir, artifact_dir, rules_path, region_map_path, output_dir)
    ) == 0

    captured = capsys.readouterr()
    assert f"output directory: {output_dir}" in captured.out
    assert "summary: 1 team" in captured.out
    payload = json.loads((output_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert payload["period_indices"] == [0]
    assert payload["scenario_version"] == artifact_dir.name
    assert payload["evaluator_version"]


def test_cli_source_period_offset_is_forwarded_and_recorded(tmp_path, monkeypatch, capsys):
    submissions_dir, artifact_dir, rules_path, region_map_path = _write_inputs(tmp_path)
    _write_submission(submissions_dir / "team-a.csv", "team-a")
    output_dir = tmp_path / "output"
    observed_periods = []

    def dispatch(_artifact_dir, plans, *, period_indices):
        observed_periods.extend(period_indices)
        return _fake_dispatch(
            _artifact_dir,
            plans,
            period_indices=period_indices,
        )

    monkeypatch.setattr(batch_evaluation, "evaluate_plans_with_prepared_dcopf", dispatch)
    assert main(
        _run_args(
            submissions_dir,
            artifact_dir,
            rules_path,
            region_map_path,
            output_dir,
            source_period_offset="4368",
        )
    ) == 0

    assert observed_periods == [4368]
    payload = json.loads((output_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert payload["period_indices"] == [0]
    assert payload["source_period_indices"] == [4368]
    period_payload = json.loads(
        (output_dir / "dispatch" / "period_0.json").read_text(encoding="utf-8")
    )
    assert period_payload["period_index"] == 0
    assert period_payload["source_period_index"] == 4368
    assert "summary: 1 team" in capsys.readouterr().out


def test_cli_rejects_unsupported_source_period_offset(tmp_path, capsys):
    submissions_dir, artifact_dir, rules_path, region_map_path = _write_inputs(tmp_path)
    _write_submission(submissions_dir / "team-a.csv", "team-a")

    assert main(
        _run_args(
            submissions_dir,
            artifact_dir,
            rules_path,
            region_map_path,
            tmp_path / "output",
            source_period_offset="1",
        )
    ) == 2
    assert "error:" in capsys.readouterr().err


def test_malformed_submission_is_isolated_and_valid_team_is_scored(
    tmp_path, monkeypatch, capsys
):
    submissions_dir, artifact_dir, rules_path, region_map_path = _write_inputs(tmp_path)
    _write_submission(submissions_dir / "valid.csv", "team-valid")
    (submissions_dir / "malformed.csv").write_text("not,a,valid,submission\n", encoding="utf-8")
    output_dir = tmp_path / "output"
    monkeypatch.setattr(batch_evaluation, "evaluate_plans_with_prepared_dcopf", _fake_dispatch)

    assert main(
        _run_args(submissions_dir, artifact_dir, rules_path, region_map_path, output_dir)
    ) == 0

    captured = capsys.readouterr()
    assert "summary: 1 team scored, 1 rejected" in captured.out
    leaderboard = (output_dir / "leaderboard.csv").read_text(encoding="utf-8")
    assert "team-valid" in leaderboard
    assert "UNPARSED::malformed.csv" in leaderboard


def test_oversized_submission_is_isolated_and_valid_team_is_scored(
    tmp_path, monkeypatch, capsys
):
    submissions_dir, artifact_dir, rules_path, region_map_path = _write_inputs(tmp_path)
    _write_submission(submissions_dir / "valid.csv", "team-valid")
    (submissions_dir / "oversized.csv").write_text(
        ",".join(CSV_COLUMNS) + "\n"
        + ("team-bad,week-1,1,PANHANDLE," + ("9" * 131073) + ",,,,,\n"),
        encoding="utf-8",
    )
    output_dir = tmp_path / "output"
    monkeypatch.setattr(batch_evaluation, "evaluate_plans_with_prepared_dcopf", _fake_dispatch)

    assert main(
        _run_args(submissions_dir, artifact_dir, rules_path, region_map_path, output_dir)
    ) == 0

    captured = capsys.readouterr()
    assert "summary: 1 team scored, 1 rejected" in captured.out
    leaderboard = (output_dir / "leaderboard.csv").read_text(encoding="utf-8")
    assert "team-valid" in leaderboard
    assert "UNPARSED::oversized.csv" in leaderboard


def test_canonical_bus_submission_runs_without_legacy_region_map(
    tmp_path, monkeypatch, capsys
):
    submissions_dir, artifact_dir, rules_path, _region_map_path = _write_inputs(tmp_path)
    (artifact_dir / "bus.csv").write_text("bus_id\n3002041\n", encoding="utf-8")
    (artifact_dir / "generator_limits.csv").write_text(
        "generator_id,marginal_cost_c1_per_mwh\nG1,42.5\n",
        encoding="utf-8",
    )
    with (submissions_dir / "team-canonical.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "team_id", "submission_id", "period", "bus_id", "load_mw",
            "generator_id", "generator_capacity_mw", "storage_id",
            "storage_power_mw", "storage_energy_mwh",
        ))
        writer.writeheader()
        writer.writerow({
            "team_id": "team-canonical", "submission_id": "half-year-1", "period": "1",
            "bus_id": "3002041", "load_mw": "2", "generator_id": "G1",
            "generator_capacity_mw": "1", "storage_id": "S1",
            "storage_power_mw": "1", "storage_energy_mwh": "2",
        })
    output_dir = tmp_path / "output"
    monkeypatch.setattr(batch_evaluation, "evaluate_plans_with_prepared_dcopf", _fake_dispatch)

    assert main([
        "run", "--submissions-dir", str(submissions_dir),
        "--artifact-dir", str(artifact_dir), "--rules", str(rules_path),
        "--output-dir", str(output_dir), "--horizon", "1",
    ]) == 0

    assert "team-canonical" in (output_dir / "leaderboard.csv").read_text(encoding="utf-8")
    assert "storage_capital_cost" in (output_dir / "leaderboard.csv").read_text(encoding="utf-8").splitlines()[0]
    assert json.loads((output_dir / "evaluation.json").read_text(encoding="utf-8"))["period_indices"] == [0]
    assert "summary: 1 team scored" in capsys.readouterr().out


def test_invalid_rules_are_reported_as_input_error(tmp_path, capsys):
    submissions_dir, artifact_dir, rules_path, region_map_path = _write_inputs(tmp_path)
    _write_submission(submissions_dir / "team-a.csv", "team-a")
    rules_path.write_text("{}", encoding="utf-8")

    assert main(
        _run_args(
            submissions_dir,
            artifact_dir,
            rules_path,
            region_map_path,
            tmp_path / "output",
        )
    ) == 2

    assert "error:" in capsys.readouterr().err


def test_missing_artifact_is_reported_as_input_error(tmp_path, capsys):
    submissions_dir, artifact_dir, rules_path, region_map_path = _write_inputs(tmp_path)
    _write_submission(submissions_dir / "team-a.csv", "team-a")

    assert main(
        _run_args(
            submissions_dir,
            artifact_dir,
            rules_path,
            region_map_path,
            tmp_path / "output",
        )
    ) == 2

    assert "error:" in capsys.readouterr().err


def test_batch_does_not_use_generator_catalog_as_submission_registry(tmp_path, capsys):
    submissions_dir, artifact_dir, rules_path, region_map_path = _write_inputs(tmp_path)
    _write_submission(submissions_dir / "team-a.csv", "team-a")
    (artifact_dir / "generator_limits.csv").write_text(
        "generator_id,marginal_cost_c1_per_mwh\nG1,nan\n",
        encoding="utf-8",
    )

    assert main(
        _run_args(
            submissions_dir,
            artifact_dir,
            rules_path,
            region_map_path,
            tmp_path / "output",
        )
    ) == 2

    captured = capsys.readouterr()
    assert "demand_bus.csv" in captured.err


def test_half_year_cli_requires_economic_profile_artifact(tmp_path, capsys):
    submissions_dir, artifact_dir, rules_path, region_map_path = _write_inputs(tmp_path)
    assert main(
        [
            "run",
            "--submissions-dir", str(submissions_dir),
            "--artifact-dir", str(artifact_dir),
            "--rules", str(rules_path),
            "--output-dir", str(tmp_path / "output"),
            "--horizon", "4368",
        ]
    ) == 2

    assert "economic profile" in capsys.readouterr().err
