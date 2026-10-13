from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from rl_gym import ConnectionPlanningEnv, GreedyPolicy, make_release_manifest
from rl_gym.training import TrainingConfig, train_policy


BUSES = ("bus-a", "bus-b", "bus-c")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def make_env(*, year: int, block: str):
    return ConnectionPlanningEnv(
        team_id="team-a",
        bus_catalog=BUSES,
        scenario_year=year,
        scenario_block=block,
        max_sites=2,
        max_planning_steps=3,
        max_capacity_mw=100.0,
    )


def test_training_config_validates_requested_run_shape():
    assert TrainingConfig(episodes=2, seed=7, year=2029, block="jan_jun").episodes == 2

    with pytest.raises(ValueError, match="episodes"):
        TrainingConfig(episodes=0)
    with pytest.raises(ValueError, match="seed"):
        TrainingConfig(seed=True)
    with pytest.raises(ValueError, match="year"):
        TrainingConfig(year=2026)
    with pytest.raises(ValueError, match="block"):
        TrainingConfig(block="full_year")


def test_training_is_deterministic_and_contains_release_metadata():
    manifest = make_release_manifest(
        scenario_year=2029,
        scenario_block="jan_jun",
        rules={"version": "rules-test"},
        config={"experiment": "training-test"},
    )

    first = train_policy(
        make_env,
        episodes=2,
        seed=11,
        year=2029,
        block="jan_jun",
        release_manifest=manifest,
    )
    second = train_policy(
        make_env,
        episodes=2,
        seed=11,
        year=2029,
        block="jan_jun",
        release_manifest=manifest,
    )

    assert first == second
    assert first["config"] == {"episodes": 2, "seed": 11, "year": 2029, "block": "jan_jun"}
    assert first["release_manifest"] == manifest.to_dict()
    assert [episode["seed"] for episode in first["episodes"]] == [11, 12]
    assert first["episodes"][0]["steps"] == 3


def test_training_supplies_a_default_release_manifest_for_bare_environment():
    result = train_policy(make_env, episodes=1, year=2029, block="jul_dec")

    assert result["release_manifest"]["scenario_year"] == 2029
    assert result["release_manifest"]["scenario_block"] == "jul_dec"


def test_learner_callback_receives_public_transition_and_is_not_serialized():
    seen = []

    def learner(observation, info):
        seen.append((set(observation), set(info)))
        return GreedyPolicy()(observation, info)

    result = train_policy(make_env, episodes=1, seed=4, learner_callback=learner)

    assert seen
    assert seen[0][0] == {"metadata", "bus_catalog", "own_plan", "own_plan_mask"}
    assert "_sites" not in seen[0][1]
    serialized = json.dumps(result, sort_keys=True)
    assert "learner" not in serialized
    assert "_sites" not in serialized
    assert "private" not in serialized


def test_learner_callback_actions_reach_environment_with_numpy_scalars():
    seen_actions = []

    class RecordingEnv(ConnectionPlanningEnv):
        def step(self, action):
            seen_actions.append(action)
            return super().step(action)

    def factory(*, year: int, block: str):
        return RecordingEnv(
            team_id="team-a",
            bus_catalog=BUSES,
            scenario_year=year,
            scenario_block=block,
            max_sites=2,
            max_planning_steps=3,
            max_capacity_mw=100.0,
        )

    def learner(observation, info):
        return {
            "bus_index": np.int64(0),
            "requested_capacity_mw": np.float32(100.0),
            "generator_capacity_mw": np.float32(100.0),
            "storage_power_mw": np.float32(0.0),
            "storage_energy_mwh": np.float32(0.0),
            "finish": np.int64(1),
            "revise": np.int64(0),
        }

    result = train_policy(factory, episodes=1, learner_callback=learner)

    assert result["episode_count"] == 1
    assert len(seen_actions) == 1
    assert isinstance(seen_actions[0]["bus_index"], np.int64)
    assert isinstance(seen_actions[0]["requested_capacity_mw"], np.float32)


def test_training_rejects_callback_and_policy_together():
    with pytest.raises(ValueError, match="policy"):
        train_policy(
            make_env,
            policy=GreedyPolicy(),
            learner_callback=lambda observation, info: GreedyPolicy()(observation, info),
        )


def test_training_cli_emits_json_and_accepts_output_path(tmp_path):
    output = tmp_path / "training.json"
    completed = subprocess.run(
        [
            sys.executable,
            "examples/rl_gym/train_policy.py",
            "--episodes",
            "1",
            "--seed",
            "5",
            "--year",
            "2029",
            "--block",
            "jan_jun",
            "--output",
            str(output),
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["config"] == {"episodes": 1, "seed": 5, "year": 2029, "block": "jan_jun"}
    assert result["release_manifest"]["scenario_year"] == 2029
    assert json.loads(output.read_text(encoding="utf-8")) == result
