from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools.hosting_capacity import HostingCapacityMatrix


def test_hosting_capacity_cli_accepts_prepared_artifact_arguments():
    from ercot_game.hosting_capacity_cli import parse_args

    args = parse_args(
        [
            "--artifact-dir",
            "data/processed/2028/jan_jun",
            "--horizon",
            "4368",
            "--solver-backend",
            "auto",
            "--workers",
            "4",
            "--output-dir",
            "results/hosting_capacity",
        ]
    )

    assert args.horizon == 4368
    assert args.solver_backend == "auto"
    assert args.workers == 4


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--workers", "0", "workers must be at least 1"),
        ("--solver-threads", "0", "solver-threads must be at least 1"),
        ("--horizon", "-1", "horizon must be at least 1"),
        ("--period-count", "0", "period-count must be at least 1"),
    ],
)
def test_hosting_capacity_cli_rejects_invalid_ranges(option, value, message, capsys):
    from ercot_game.hosting_capacity_cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--ieee14", option, value, "--output-dir", "out"])

    assert message in capsys.readouterr().err


def test_hosting_capacity_cli_requires_one_input_mode(capsys):
    from ercot_game.hosting_capacity_cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--output-dir", "out"])

    assert "one of the arguments --artifact-dir --ieee14 is required" in capsys.readouterr().err


def test_hosting_capacity_cli_rejects_conflicting_input_modes(capsys):
    from ercot_game.hosting_capacity_cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--artifact-dir", "artifact", "--ieee14", "--output-dir", "out"])

    assert "not allowed with argument" in capsys.readouterr().err


def test_hosting_capacity_cli_rejects_nested_parallelism(capsys):
    from ercot_game.hosting_capacity_cli import parse_args

    with pytest.raises(SystemExit):
        parse_args(
            [
                "--ieee14",
                "--workers",
                "2",
                "--solver-threads",
                "2",
                "--output-dir",
                "out",
            ]
        )

    assert "workers > 1 cannot be combined with solver-threads > 1" in capsys.readouterr().err


def test_hosting_capacity_cli_prints_json_summary_and_writes_long_form(tmp_path, monkeypatch, capsys):
    from ercot_game import hosting_capacity_cli

    def fake_compute(provider, **kwargs):
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        np.save(output_dir / "hosting_capacity_mw.npy", np.array([[4.0, np.nan]]))
        np.save(
            output_dir / "hosting_capacity_status.npy",
            np.array([["optimal", "solver_error"]]),
        )
        return HostingCapacityMatrix(
            period_indices=(0,),
            bus_ids=("b1", "b2"),
            capacity_mw=np.array([[4.0, np.nan]]),
            status=np.array([["optimal", "solver_error"]]),
            objective_value=np.array([[4.0, np.nan]]),
            solve_time_s=np.array([[0.01, 0.02]]),
            metadata={
                "solver_backend": "highs",
                "counts": {
                    "completed_count": 2,
                    "failed_count": 1,
                    "point_count": 2,
                },
                "complete": True,
            },
        )

    monkeypatch.setattr(hosting_capacity_cli, "compute_hosting_capacity_matrix", fake_compute)
    output_dir = tmp_path / "output"

    return_code = hosting_capacity_cli.main(
        [
            "--ieee14",
            "--output-dir",
            str(output_dir),
            "--write-long-form",
        ]
    )

    assert return_code == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["output_dir"] == str(output_dir.resolve())
    assert summary["completed_point_count"] == 2
    assert summary["failed_point_count"] == 1
    assert summary["requested_periods"] == [0]
    assert (output_dir / "hosting_capacity_long.csv.gz").is_file()


def test_hosting_capacity_cli_returns_interrupted_and_keeps_checkpoint(tmp_path, monkeypatch, capsys):
    from ercot_game import hosting_capacity_cli

    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()

    def interrupted_compute(provider, **kwargs):
        Path(kwargs["checkpoint_dir"]).mkdir(parents=True, exist_ok=True)
        (checkpoint_dir / "period_000000.npz").write_bytes(b"partial checkpoint")
        raise KeyboardInterrupt

    monkeypatch.setattr(hosting_capacity_cli, "compute_hosting_capacity_matrix", interrupted_compute)
    output_dir = tmp_path / "output"

    return_code = hosting_capacity_cli.main(
        [
            "--ieee14",
            "--output-dir",
            str(output_dir),
            "--checkpoint-dir",
            str(checkpoint_dir),
        ]
    )

    assert return_code == 130
    summary = json.loads(capsys.readouterr().out)
    assert summary["resume_status"] == "interrupted"
    assert (checkpoint_dir / "period_000000.npz").is_file()


def test_benchmark_stages_do_not_include_full_horizon_without_flag():
    from tools.benchmarks.benchmark_hosting_capacity import stage_specs

    assert [stage.name for stage in stage_specs(has_artifact=True, full_horizon=False)] == [
        "activsg2000-1",
        "activsg2000-24",
        "activsg2000-168",
    ]

