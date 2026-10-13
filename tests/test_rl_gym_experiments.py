from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from rl_gym import ExperimentConfig, run_experiments
from rl_gym.experiments import main


def test_config_rejects_invalid_values():
    with pytest.raises(ValueError, match="policy_name"):
        ExperimentConfig(
            policy_name="unknown",
            scenario_years=(2029,),
            blocks=("jan_jun",),
            team_count=1,
            episode_count=1,
            seed=0,
        )

    with pytest.raises(ValueError, match="blocks"):
        ExperimentConfig(
            policy_name="greedy",
            scenario_years=(2029,),
            blocks=("annual",),
            team_count=1,
            episode_count=1,
            seed=0,
        )

    with pytest.raises(ValueError, match="team_count"):
        ExperimentConfig(
            policy_name="greedy",
            scenario_years=(2029,),
            blocks=("jan_jun",),
            team_count=0,
            episode_count=1,
            seed=0,
        )

    with pytest.raises(ValueError, match="evaluator_mode"):
        ExperimentConfig(
            policy_name="greedy",
            scenario_years=(2029,),
            blocks=("jan_jun",),
            team_count=1,
            episode_count=1,
            seed=0,
            evaluator_mode="solver",
        )


def test_solver_free_run_is_json_safe_private_plan_free_and_reproducible():
    config = ExperimentConfig(
        policy_name="rule_based",
        scenario_years=(2029,),
        blocks=("jan_jun",),
        team_count=2,
        episode_count=2,
        seed=17,
    )

    first = run_experiments(config)
    second = run_experiments(config)

    assert len(first) == 4
    json.dumps(first, sort_keys=True, allow_nan=False)
    json.dumps(second, sort_keys=True, allow_nan=False)
    stable_fields = {
        key: value
        for key, value in first[0].items()
        if key != "timing"
    }
    assert {
        key: value for key, value in second[0].items() if key != "timing"
    } == stable_fields
    assert first[0]["timing"]["steps"] == second[0]["timing"]["steps"]
    assert first[0]["timing"]["policy_calls"] == second[0]["timing"]["policy_calls"]
    assert first[0]["timing"]["action_count"] == second[0]["timing"]["action_count"]
    assert all(record["policy"] == "rule_based" for record in first)
    assert all(record["team_id"] in {"team-1", "team-2"} for record in first)
    assert all(record["release_manifest_digest"] for record in first)
    assert all("plan" not in json.dumps(record) for record in first)
    assert all("private" not in json.dumps(record).lower() for record in first)
    assert all(record["status"] == "not_evaluated" for record in first)
    assert all(record["aggregate"]["evaluated"] is False for record in first)


def test_generated_records_include_execution_metrics():
    records = run_experiments(ExperimentConfig(
        policy_name="greedy",
        scenario_years=(2029,),
        blocks=("jan_jun",),
        team_count=2,
        episode_count=1,
        seed=17,
    ))

    timing = records[0]["timing"]
    assert set(timing) == {
        "steps",
        "policy_calls",
        "action_count",
        "reset_latency_seconds",
        "step_latency_seconds",
        "episode_latency_seconds",
    }
    assert timing["steps"] == timing["policy_calls"]
    assert timing["action_count"] == 2 * timing["policy_calls"]
    assert timing["steps"] > 0
    assert all(
        isinstance(timing[field], (int, float))
        and not isinstance(timing[field], bool)
        and math.isfinite(float(timing[field]))
        and timing[field] >= 0
        for field in (
            "reset_latency_seconds",
            "step_latency_seconds",
            "episode_latency_seconds",
        )
    )


def test_terminal_evaluator_is_injected_and_not_serialized():
    calls = []
    config = ExperimentConfig(
        policy_name="greedy",
        scenario_years=(2029,),
        blocks=("jul_dec",),
        team_count=1,
        episode_count=1,
        seed=3,
        evaluator_mode="terminal",
    )

    def evaluator(csv_paths, **kwargs):
        calls.append((tuple(csv_paths), kwargs))
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-1", score=12.5),))

    records = run_experiments(config, evaluator_callback=evaluator)

    assert len(calls) == 1
    assert records[0]["reward"] == 12.5
    assert records[0]["status"] == "evaluated"
    assert records[0]["aggregate"]["evaluated"] is True
    assert "evaluator_callback" not in json.dumps(records[0])


def test_terminal_mode_requires_callback():
    config = ExperimentConfig(
        policy_name="greedy",
        scenario_years=(2029,),
        blocks=("jan_jun",),
        team_count=1,
        episode_count=1,
        seed=0,
        evaluator_mode="terminal",
    )

    with pytest.raises(ValueError, match="evaluator_callback"):
        run_experiments(config)


def test_none_mode_rejects_callback():
    config = ExperimentConfig(
        policy_name="greedy",
        scenario_years=(2029,),
        blocks=("jan_jun",),
        team_count=1,
        episode_count=1,
        seed=0,
    )

    def evaluator(*args, **kwargs):
        del args, kwargs
        return None

    with pytest.raises(ValueError, match="evaluator_mode='terminal'"):
        run_experiments(config, evaluator_callback=evaluator)


def test_cli_rejects_terminal_mode_with_clean_parser_error(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main([
            "--policy", "greedy",
            "--years", "2029",
            "--blocks", "jan_jun",
            "--evaluator-mode", "terminal",
        ])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err
    assert "terminal" in captured.err
    assert "Traceback" not in captured.err


def test_cli_emits_json_records(capsys):
    assert main([
        "--policy", "greedy",
        "--years", "2029",
        "--blocks", "jan_jun",
        "--team-count", "1",
        "--episodes", "1",
        "--seed", "4",
    ]) == 0

    records = json.loads(capsys.readouterr().out)
    assert len(records) == 1
    assert records[0]["scenario_block"] == "jan_jun"


def test_editable_install_exposes_reporting_cli(tmp_path):
    venv_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(venv_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    python = venv_dir / "bin" / "python"
    command = [
        str(python),
        "-m",
        "pip",
        "install",
        "--no-deps",
        "--no-build-isolation",
        "-e",
        str(Path(__file__).resolve().parents[1]),
    ]
    installed = subprocess.run(command, capture_output=True, text=True)
    if installed.returncode != 0:
        pytest.skip(
            "editable no-deps install unavailable in this environment: "
            f"{installed.stderr.strip() or installed.stdout.strip()}"
        )

    help_result = subprocess.run(
        [str(venv_dir / "bin" / "ercot-game-compare-baselines"), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "usage:" in help_result.stdout.lower()
