from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import tomllib

import numpy as np
import pytest

from rl_gym import ConnectionPlanningEnv, make_release_manifest


BUSES = ("bus-a", "bus-b", "bus-c")


def make_env(**kwargs):
    settings = dict(
        team_id="team-a",
        bus_catalog=BUSES,
        scenario_year=2029,
        scenario_block="jan_jun",
        max_sites=2,
        max_planning_steps=3,
    )
    settings.update(kwargs)
    return ConnectionPlanningEnv(**settings)


def add_action(bus_index=0, requested=10.0, generator=4.0, power=2.0, energy=8.0, revise=0.0):
    return np.asarray([bus_index, requested, generator, power, energy, 0.0, revise], dtype=np.float32)


def finish_action():
    return np.asarray([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0], dtype=np.float32)


def action_dict(
    bus_index=0,
    requested=10.0,
    generator=4.0,
    power=2.0,
    energy=8.0,
    finish=0,
    revise=0,
):
    return {
        "bus_index": bus_index,
        "requested_capacity_mw": requested,
        "generator_capacity_mw": generator,
        "storage_power_mw": power,
        "storage_energy_mwh": energy,
        "finish": finish,
        "revise": revise,
    }


def test_api_spaces_and_fixed_observation_shape():
    env = make_env()
    observation, info = env.reset(seed=7)

    assert set(env.action_space.spaces) == {
        "bus_index",
        "requested_capacity_mw",
        "generator_capacity_mw",
        "storage_power_mw",
        "storage_energy_mwh",
        "finish",
        "revise",
    }
    env.action_space.seed(7)
    sampled_action = env.action_space.sample()
    assert env.action_space.contains(sampled_action)
    assert sampled_action["generator_capacity_mw"] <= sampled_action["requested_capacity_mw"]
    assert sampled_action["storage_energy_mwh"] == 4 * sampled_action["storage_power_mw"]
    assert set(observation) == {"metadata", "bus_catalog", "own_plan", "own_plan_mask"}
    assert env.observation_space.contains(observation)
    assert observation["metadata"].shape == (6,)
    assert observation["own_plan"].shape == (2, 5)
    assert observation["own_plan_mask"].shape == (2,)
    assert info["environment_version"] == "phase2-single-agent-v1"


def test_valid_action_adds_site_and_storage_energy_is_four_times_power():
    env = make_env()
    env.reset(seed=1)

    observation, reward, terminated, truncated, info = env.step(add_action())

    assert reward == 0.0
    assert not terminated and not truncated
    assert info["valid_action"] is True
    assert info["plan"][0]["bus_id"] == "bus-a"
    assert info["plan"][0]["storage_energy_mwh"] == 8.0
    assert observation["own_plan_mask"].tolist() == [1, 0]


def test_structured_action_is_accepted_and_legacy_vector_remains_supported():
    env = make_env()
    env.reset(seed=1)

    _, reward, terminated, truncated, info = env.step(action_dict())

    assert reward == 0.0
    assert not terminated and not truncated
    assert info["valid_action"] is True


@pytest.mark.parametrize("action,code", [
    (add_action(bus_index=99), "unknown_bus"),
    (add_action(requested=-1), "negative_capacity"),
    (add_action(power=2, energy=7), "storage_duration_mismatch"),
    (np.asarray([np.nan, 1, 0, 0, 0, 0, 0], dtype=np.float32), "nonfinite_action"),
])
def test_invalid_action_is_deterministic_penalized_noop(action, code):
    env = make_env(invalid_action_policy="penalize", invalid_action_penalty=-3.0)
    env.reset(seed=1)

    observation, reward, terminated, truncated, info = env.step(action)

    assert reward == -3.0
    assert not terminated and not truncated
    assert info["valid_action"] is False
    assert info["invalid_action_code"] == code
    assert observation["metadata"][4] == 0


def test_generator_capacity_cannot_exceed_requested_capacity():
    calls = []

    def evaluator(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("invalid actions must not reach the evaluator")

    env = make_env(
        evaluator_callback=evaluator,
        invalid_action_penalty=-3.0,
    )
    env.reset(seed=1)

    _, reward, terminated, truncated, info = env.step(
        action_dict(requested=3.0, generator=4.0, power=0.0, energy=0.0)
    )

    assert reward == -3.0
    assert not terminated and not truncated
    assert info["valid_action"] is False
    assert info["invalid_action_code"] == "generator_exceeds_connection"
    assert info["diagnostic"] == "generator capacity cannot exceed connection capacity"
    assert calls == []


def test_invalid_final_planning_step_evaluates_existing_plan_and_preserves_penalty(tmp_path):
    calls = []

    def evaluator(csv_paths, **kwargs):
        calls.append((tuple(csv_paths), kwargs))
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-a", score=9.0),))

    env = make_env(
        max_planning_steps=2,
        invalid_action_penalty=-3.0,
        evaluator_callback=evaluator,
        evaluator_artifact_dir=tmp_path,
        evaluator_rules=object(),
        submission_dir=tmp_path,
    )
    env.reset(seed=1)
    env.step(add_action())

    _, reward, terminated, truncated, info = env.step(
        action_dict(requested=3.0, generator=4.0, power=0.0, energy=0.0)
    )

    assert reward == -3.0
    assert terminated and not truncated
    assert info["valid_action"] is False
    assert info["invalid_action_code"] == "generator_exceeds_connection"
    assert info["terminated_reason"] == "max_planning_steps_after_invalid_action"
    assert info["dispatch_status"] == "evaluated"
    assert info["score"] == 9.0
    assert len(calls) == 1


def test_finish_terminates_and_without_evaluator_returns_explicit_zero_score():
    env = make_env()
    env.reset(seed=1)
    env.step(add_action())

    _, reward, terminated, truncated, info = env.step(finish_action())

    assert reward == 0.0
    assert terminated and not truncated
    assert info["terminated_reason"] == "finish"
    assert info["score"] == 0.0
    assert info["dispatch_status"] == "not_evaluated"


def test_empty_finish_is_invalid_terminal_and_never_calls_evaluator():
    calls = []

    def evaluator(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("empty plans must not be evaluated")

    env = make_env(evaluator_callback=evaluator)
    env.reset(seed=1)

    _, reward, terminated, truncated, info = env.step(finish_action())

    assert reward == -1.0
    assert terminated and not truncated
    assert info["valid_action"] is False
    assert info["invalid_action_code"] == "empty_plan"
    assert info["terminated_reason"] == "empty_plan"
    assert info["dispatch_status"] == "not_evaluated"
    assert calls == []


def test_planning_limit_terminates_and_episode_limit_truncates():
    env = make_env(max_planning_steps=1)
    env.reset(seed=1)
    _, _, terminated, truncated, info = env.step(add_action())
    assert terminated and not truncated
    assert info["terminated_reason"] == "max_planning_steps"

    env = make_env(max_planning_steps=5, max_episode_steps=1)
    env.reset(seed=1)
    _, _, terminated, truncated, info = env.step(add_action())
    assert not terminated and truncated
    assert info["truncated_reason"] == "max_episode_steps"


def test_planning_limit_evaluates_nonempty_plan_once(tmp_path):
    calls = []

    def evaluator(csv_paths, **kwargs):
        calls.append((tuple(csv_paths), kwargs))
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-a", score=9.0),))

    env = make_env(
        max_planning_steps=1,
        evaluator_callback=evaluator,
        evaluator_artifact_dir=tmp_path,
        evaluator_rules=object(),
        submission_dir=tmp_path,
    )
    env.reset(seed=1)
    _, reward, terminated, truncated, info = env.step(add_action())

    assert reward == 9.0
    assert terminated and not truncated
    assert info["terminated_reason"] == "max_planning_steps"
    assert info["dispatch_status"] == "evaluated"
    assert info["score"] == 9.0
    assert len(calls) == 1


def test_same_seed_and_action_sequence_replays_identically():
    actions = [add_action(), add_action(bus_index=1, requested=5, generator=0, power=0, energy=0), finish_action()]
    first = make_env(reward_mode="dense_surrogate")
    second = make_env(reward_mode="dense_surrogate")
    first.reset(seed=42)
    second.reset(seed=42)

    for action in actions:
        left = first.step(action)
        right = second.step(action)
        assert np.array_equal(left[0]["metadata"], right[0]["metadata"])
        assert np.array_equal(left[0]["own_plan"], right[0]["own_plan"])
        assert left[1:] == right[1:]


def test_observation_does_not_contain_other_team_or_private_evaluator_state():
    env = make_env()
    observation, _ = env.reset(seed=1)
    assert "other_team" not in observation
    assert "dispatch" not in observation
    assert "score" not in observation
    assert "bus-a" not in repr(observation)


def test_finish_delegates_through_phase1_adapter_callback(tmp_path):
    calls = []

    def evaluator(csv_paths, **kwargs):
        calls.append((tuple(csv_paths), kwargs))
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-a", score=12.5),))

    env = make_env(
        evaluator_callback=evaluator,
        evaluator_artifact_dir=tmp_path,
        evaluator_rules=object(),
        submission_dir=tmp_path,
    )
    env.reset(seed=1)
    env.step(add_action())
    _, reward, terminated, truncated, info = env.step(finish_action())

    assert terminated and not truncated
    assert reward == 12.5
    assert info["score"] == 12.5
    assert info["dispatch_status"] == "evaluated"
    assert len(calls) == 1
    assert calls[0][0][0].is_file()


def test_evaluator_submission_paths_are_unique_across_env_instances(tmp_path):
    paths = []

    def evaluator(csv_paths, **kwargs):
        paths.append(Path(csv_paths[0]))
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-a", score=12.5),))

    first = make_env(
        evaluator_callback=evaluator,
        evaluator_artifact_dir=tmp_path,
        evaluator_rules=object(),
        submission_dir=tmp_path,
    )
    second = make_env(
        evaluator_callback=evaluator,
        evaluator_artifact_dir=tmp_path,
        evaluator_rules=object(),
        submission_dir=tmp_path,
    )

    for env in (first, second):
        env.reset(seed=1)
        env.step(add_action())
        env.step(finish_action())

    assert len(paths) == 2
    assert paths[0] != paths[1]
    assert all(path.parent == tmp_path and path.is_file() for path in paths)


def test_optional_replay_identity_fields_are_propagated_without_invention():
    env = make_env(
        scenario_manifest_hash="manifest-123",
        rules_version="rules-v7",
        rules_hash="rules-hash-456",
        evaluator_version="evaluator-v9",
    )
    _, reset_info = env.reset(seed=1)

    expected = {
        "scenario_manifest_hash": "manifest-123",
        "rules_version": "rules-v7",
        "rules_hash": "rules-hash-456",
        "evaluator_version": "evaluator-v9",
    }
    assert {key: reset_info[key] for key in expected} == expected

    _, _, _, _, step_info = env.step(action_dict())
    assert {key: step_info[key] for key in expected} == expected

    _, empty_info = make_env().reset(seed=1)
    assert empty_info["scenario_manifest_hash"] is None
    assert empty_info["rules_version"] is None
    assert empty_info["rules_hash"] is None
    assert empty_info["evaluator_version"] is None


def test_revise_updates_existing_bus_but_duplicate_add_is_invalid():
    env = make_env()
    env.reset(seed=1)
    env.step(add_action())
    _, _, _, _, duplicate = env.step(add_action(requested=20))
    assert duplicate["valid_action"] is False
    assert duplicate["invalid_action_code"] == "duplicate_bus_without_revise"

    observation, _, _, _, revised = env.step(add_action(requested=20, revise=1))
    assert revised["valid_action"] is True
    assert observation["own_plan"][0, 1] == 20


@pytest.mark.parametrize("year", [2026, 2030, 2029.5, True])
def test_constructor_rejects_unsupported_or_non_integer_scenario_year(year):
    with pytest.raises(ValueError):
        make_env(scenario_year=year)


def test_constructor_accepts_release_manifest_matching_configured_scenario():
    manifest = make_release_manifest(
        scenario_year=2029,
        scenario_block="jan_jun",
        rules={},
        config={},
    )

    env = make_env(release_manifest=manifest)

    assert env.release_manifest == manifest


@pytest.mark.parametrize(
    ("scenario_year", "scenario_block"),
    [(2028, "jan_jun"), (2029, "jul_dec")],
)
def test_constructor_rejects_release_manifest_with_mismatched_scenario(
    scenario_year, scenario_block
):
    manifest = make_release_manifest(
        scenario_year=scenario_year,
        scenario_block=scenario_block,
        rules={},
        config={},
    )

    with pytest.raises(ValueError, match="release_manifest"):
        make_env(release_manifest=manifest)


def test_rl_optional_dependency_declares_gymnasium_and_numpy():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    dependencies = project["project"]["optional-dependencies"]["rl"]
    assert any(item.startswith("gymnasium") for item in dependencies)
    assert any(item.startswith("numpy") for item in dependencies)
