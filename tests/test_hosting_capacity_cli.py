from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from tools.hosting_capacity import HostingCapacityMatrix


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_hosting_capacity_module_help_works_from_source_checkout():
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, "-m", "ercot_game.hosting_capacity_cli", "--help"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Compute exact one-bus hosting capacity" in completed.stdout


def test_hosting_capacity_benchmark_help_works_from_another_cwd(tmp_path):
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools/benchmarks/benchmark_hosting_capacity.py"),
            "--help",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Run staged hosting-capacity measurements" in completed.stdout


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


def _write_interrupt_checkpoint(path: Path, period_index: int, statuses: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    point_count = len(statuses)
    capacities = np.full(point_count, np.nan)
    objectives = np.full(point_count, np.nan)
    optimal = np.asarray(statuses) == "optimal"
    capacities[optimal] = 1.0
    objectives[optimal] = 1.0
    np.savez_compressed(
        path,
        period_index=np.asarray(period_index, dtype=np.int64),
        identity=np.asarray("{}"),
        capacity_mw=capacities,
        status=np.asarray(statuses, dtype="<U32"),
        objective_value=objectives,
        solve_time_s=np.ones(point_count),
        diagnostic=np.full(point_count, "", dtype="<U512"),
        complete=np.asarray(True),
    )


def test_hosting_capacity_cli_interrupt_recovers_all_bus_checkpoint_counts(
    tmp_path, monkeypatch, capsys
):
    from ercot_game import hosting_capacity_cli

    buses = tuple(SimpleNamespace(bus_id=f"b{index}") for index in range(4))
    provider = lambda period_index: SimpleNamespace(buses=buses)
    monkeypatch.setattr(hosting_capacity_cli, "provider_for_args", lambda args: (provider, 2))
    checkpoint_dir = tmp_path / "checkpoints"

    def interrupted_compute(_provider, **kwargs):
        _write_interrupt_checkpoint(
            checkpoint_dir / "period_000000.npz",
            0,
            ["optimal", "optimal", "solver_error", "optimal"],
        )
        _write_interrupt_checkpoint(
            checkpoint_dir / "period_000001.npz",
            1,
            ["optimal", "infeasible_base_case", "optimal", "optimal"],
        )
        raise KeyboardInterrupt

    monkeypatch.setattr(hosting_capacity_cli, "compute_hosting_capacity_matrix", interrupted_compute)
    output_dir = tmp_path / "output"

    return_code = hosting_capacity_cli.main(
        [
            "--artifact-dir",
            str(tmp_path / "artifact"),
            "--output-dir",
            str(output_dir),
            "--checkpoint-dir",
            str(checkpoint_dir),
        ]
    )

    assert return_code == 130
    summary = json.loads(capsys.readouterr().out)
    assert summary["bus_count"] == 4
    assert summary["completed_point_count"] == 8
    assert summary["failed_point_count"] == 2
    assert summary["resume_status"] == "interrupted"
    assert (checkpoint_dir / "period_000001.npz").is_file()


def test_hosting_capacity_cli_interrupt_counts_unique_selected_buses(
    tmp_path, monkeypatch, capsys
):
    from ercot_game import hosting_capacity_cli

    buses = tuple(SimpleNamespace(bus_id=f"b{index}") for index in range(4))
    provider = lambda period_index: SimpleNamespace(buses=buses)
    monkeypatch.setattr(hosting_capacity_cli, "provider_for_args", lambda args: (provider, 1))
    checkpoint_dir = tmp_path / "checkpoints"

    def interrupted_compute(_provider, **kwargs):
        _write_interrupt_checkpoint(
            checkpoint_dir / "period_000000.npz", 0, ["optimal", "solver_error"]
        )
        raise KeyboardInterrupt

    monkeypatch.setattr(hosting_capacity_cli, "compute_hosting_capacity_matrix", interrupted_compute)

    return_code = hosting_capacity_cli.main(
        [
            "--artifact-dir",
            str(tmp_path / "artifact"),
            "--bus-id",
            "b0",
            "--bus-id",
            "b0",
            "--bus-id",
            "b1",
            "--output-dir",
            str(tmp_path / "output"),
            "--checkpoint-dir",
            str(checkpoint_dir),
        ]
    )

    assert return_code == 130
    summary = json.loads(capsys.readouterr().out)
    assert summary["bus_count"] == 2
    assert summary["completed_point_count"] == 2
    assert summary["failed_point_count"] == 1


def test_benchmark_stages_do_not_include_full_horizon_without_flag():
    from tools.benchmarks.benchmark_hosting_capacity import stage_specs

    assert [stage.name for stage in stage_specs(has_artifact=True, full_horizon=False)] == [
        "ieee14-1",
        "activsg2000-1",
        "activsg2000-24",
        "activsg2000-168",
    ]
    assert [stage.name for stage in stage_specs(has_artifact=True, full_horizon=True)] == [
        "ieee14-1",
        "activsg2000-1",
        "activsg2000-24",
        "activsg2000-168",
        "activsg2000-full",
    ]
    assert [stage.name for stage in stage_specs(has_artifact=False, full_horizon=True)] == [
        "ieee14-1",
    ]


def test_artifact_benchmark_emits_complete_staged_sequence(tmp_path, monkeypatch):
    from tools.benchmarks import benchmark_hosting_capacity

    period_counts = []
    (tmp_path / "artifact").mkdir()
    provider = lambda period_index: SimpleNamespace()
    monkeypatch.setattr(
        benchmark_hosting_capacity,
        "prepared_artifact_period_provider",
        lambda _artifact_dir: provider,
    )
    monkeypatch.setattr(benchmark_hosting_capacity, "ieee14_provider", lambda: provider)
    monkeypatch.setattr(benchmark_hosting_capacity, "artifact_horizon", lambda _artifact_dir: 365)

    def fake_compute(_provider, **kwargs):
        period_count = len(kwargs["period_indices"])
        period_counts.append(period_count)
        return HostingCapacityMatrix(
            period_indices=tuple(range(period_count)),
            bus_ids=("b1",),
            capacity_mw=np.ones((period_count, 1)),
            status=np.full((period_count, 1), "optimal"),
            objective_value=np.ones((period_count, 1)),
            solve_time_s=np.ones((period_count, 1)),
            metadata={"resolved_solver_backend": "highs"},
        )

    monkeypatch.setattr(
        benchmark_hosting_capacity,
        "compute_hosting_capacity_matrix",
        fake_compute,
    )

    return_code = benchmark_hosting_capacity.main(
        [
            "--artifact-dir",
            str(tmp_path / "artifact"),
            "--output-dir",
            str(tmp_path / "output"),
            "--full-horizon",
        ]
    )

    assert return_code == 0
    rows = [
        json.loads(line)
        for line in (tmp_path / "output" / "benchmark_hosting_capacity.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [row["stage"] for row in rows] == [
        "ieee14-1",
        "activsg2000-1",
        "activsg2000-24",
        "activsg2000-168",
        "activsg2000-full",
    ]
    assert period_counts == [1, 1, 24, 168, 365]

