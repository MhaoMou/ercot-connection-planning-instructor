from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "examples" / "rl_gym" / "README.md"


def run_example(script: str, *args: str) -> dict:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        ["src", ".", environment.get("PYTHONPATH", "")]
    )
    result = subprocess.run(
        [sys.executable, "examples/rl_gym/" + script, *args],
        cwd=REPO_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stderr == ""
    assert len(result.stdout.splitlines()) == 1
    return json.loads(result.stdout)


def test_readme_commands_include_repository_root_pythonpath():
    readme = README_PATH.read_text(encoding="utf-8")

    assert "PYTHONPATH=src:. python examples/rl_gym/single_agent_baseline.py" in readme
    assert "PYTHONPATH=src:. python examples/rl_gym/competitive_parallel.py" in readme


def test_single_agent_baseline_emits_summary_and_replay(tmp_path: Path):
    replay_path = tmp_path / "single-agent-replay.json"

    summary = run_example(
        "single_agent_baseline.py",
        "--year",
        "2029",
        "--block",
        "jan_jun",
        "--seed",
        "17",
        "--replay-out",
        str(replay_path),
    )

    assert summary["scenario_year"] == 2029
    assert summary["scenario_block"] == "jan_jun"
    assert summary["seed"] == 17
    assert summary["status"] == "not_evaluated"
    assert isinstance(summary["reward"], float)
    assert summary["replay_out"] == str(replay_path)
    assert replay_path.is_file()
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    assert replay["manifest"]["release_manifest"]["scenario_year"] == 2029


def test_competitive_parallel_emits_only_sanitized_team_summary():
    summary = run_example(
        "competitive_parallel.py",
        "--year",
        "2028",
        "--block",
        "jul_dec",
        "--seed",
        "23",
    )

    assert summary["scenario_year"] == 2028
    assert summary["scenario_block"] == "jul_dec"
    assert summary["seed"] == 23
    assert set(summary["teams"]) == {"team-a", "team-b"}
    assert all(set(team) == {"reward", "status"} for team in summary["teams"].values())
    assert all(team["status"] == "not_evaluated" for team in summary["teams"].values())
    assert summary["aggregate"]["team_count"] == 2
    assert summary["aggregate"]["evaluated"] is False
    assert "plan" not in json.dumps(summary)
    assert "team_scores" not in json.dumps(summary)
