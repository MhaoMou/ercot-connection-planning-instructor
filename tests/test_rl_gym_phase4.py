from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from rl_gym import ConnectionPlanningEnv, ParallelConnectionPlanningEnv, make_release_manifest
from ercot_game import batch_evaluation


BUSES = ("bus-a", "bus-b", "bus-c")


def settings(**overrides):
    value = {
        "bus_catalog": BUSES,
        "scenario_year": 2029,
        "scenario_block": "jan_jun",
        "max_sites": 2,
        "max_planning_steps": 4,
        "max_capacity_mw": 20.0,
    }
    value.update(overrides)
    return value


def add_action(bus_index=0, requested=10.0, generator=4.0, power=2.0, energy=8.0):
    return np.asarray([bus_index, requested, generator, power, energy, 0.0, 0.0], dtype=np.float32)


def finish_action():
    return np.asarray([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0], dtype=np.float32)


def make_env(**overrides):
    return ParallelConnectionPlanningEnv(
        team_ids=("team-b", "team-a"),
        env_kwargs=settings(**overrides),
    )


def finish_both(env):
    env.step({"team-a": add_action(0), "team-b": add_action(1)})
    return env.step({"team-a": finish_action(), "team-b": finish_action()})


def test_parallel_api_sorts_team_ids_and_keeps_observations_private():
    env = make_env()

    observations, infos = env.reset(seed=7)

    assert list(observations) == ["team-a", "team-b"]
    assert list(infos) == ["team-a", "team-b"]
    for key in observations["team-a"]:
        assert np.array_equal(observations["team-a"][key], observations["team-b"][key])
    assert "team-b" not in repr(observations["team-a"])
    assert "plan" not in infos["team-a"]

    observations, rewards, terminated, truncated, infos = env.step(
        {"team-a": add_action(0), "team-b": add_action(1)}
    )
    assert list(rewards) == ["team-a", "team-b"]
    assert not any(terminated.values())
    assert not any(truncated.values())
    assert observations["team-a"]["own_plan_mask"].tolist() == [1, 0]
    assert observations["team-b"]["own_plan_mask"].tolist() == [1, 0]
    assert observations["team-a"]["own_plan"][0, 0] != observations["team-b"]["own_plan"][0, 0]


def test_parallel_observations_are_defensive_copies_at_all_nesting_levels():
    env = ParallelConnectionPlanningEnv(
        team_ids=("team-a",),
        env_kwargs=settings(),
    )
    observations, _ = env.reset(seed=7)
    observations["team-a"]["metadata"][0] = 99.0
    observations["team-a"]["own_plan"][0, 0] = 99.0

    observations, _, _, _, _ = env.step({"team-a": add_action()})
    observations["team-a"]["metadata"][0] = 99.0
    observations["team-a"]["own_plan"][0, 0] = 99.0

    repeated, _, _, _, _ = env.step({"team-a": finish_action()})
    assert repeated["team-a"]["metadata"][0] == 1.0
    assert repeated["team-a"]["own_plan"][0, 0] == 0.0


def test_current_plan_state_is_public_and_defensive():
    child = ConnectionPlanningEnv(team_id="team-a", **settings())
    child.reset(seed=1)
    child.step(add_action())

    state = child.current_plan_state()

    assert state.team_id == "team-a"
    assert len(state.sites) == 1
    assert state.sites[0].bus_id == "bus-a"


def test_joint_evaluator_called_once_and_scores_only_valid_teams(tmp_path):
    calls = []

    def evaluator(csv_paths, **kwargs):
        calls.append((tuple(csv_paths), kwargs))
        return SimpleNamespace(
            team_scores=(SimpleNamespace(team_id="team-a", score=11.0),
                         SimpleNamespace(team_id="team-b", score=22.0)),
            projection_diagnostics=SimpleNamespace(
                service_fraction_by_team={"team-a": 0.5, "team-b": 1.0},
                projected_capacity_mw_by_team={"team-a": 5.0, "team-b": 10.0},
            ),
        )

    env = ParallelConnectionPlanningEnv(
        team_ids=("team-a", "team-b"),
        env_kwargs=settings(),
        joint_evaluator=evaluator,
        evaluator_artifact_dir=tmp_path,
        evaluator_rules=object(),
        submission_dir=tmp_path,
    )
    env.reset(seed=3)
    observations, rewards, terminated, truncated, infos = finish_both(env)

    assert len(calls) == 1
    assert len(calls[0][0]) == 2
    assert all(path.exists() for path in calls[0][0])
    assert rewards == {"team-a": 11.0, "team-b": 22.0}
    assert terminated == {"team-a": True, "team-b": True}
    assert not any(truncated.values())
    assert infos["team-a"]["score"] == 11.0
    assert infos["team-b"]["score"] == 22.0
    assert infos["team-a"]["team_count"] == 2
    assert infos["team-a"]["valid_team_count"] == 2
    assert infos["team-a"]["projection_applied"] is True
    assert infos["team-a"]["projected_team_count"] == 1
    assert "service_fraction_by_team" not in infos["team-a"]


def test_invalid_missing_and_unknown_teams_are_isolated_and_do_not_block_valid_evaluation(tmp_path):
    calls = []

    def evaluator(csv_paths, **kwargs):
        calls.append(tuple(csv_paths))
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-a", score=9.0),))

    env = ParallelConnectionPlanningEnv(
        team_ids=("team-a", "team-b", "team-c"),
        env_kwargs=settings(),
        joint_evaluator=evaluator,
        evaluator_artifact_dir=tmp_path,
        evaluator_rules=object(),
        submission_dir=tmp_path,
        invalid_action_penalty=-3.0,
    )
    env.reset(seed=4)
    env.step({"team-a": add_action(0), "team-b": add_action(1), "unexpected": add_action(2)})
    _, rewards, terminated, _, infos = env.step({"team-a": finish_action()})

    assert len(calls) == 1
    assert len(calls[0]) == 1
    assert rewards["team-a"] == 9.0
    assert rewards["team-b"] == -3.0
    assert rewards["team-c"] == -3.0
    assert terminated == {"team-a": True, "team-b": True, "team-c": True}
    assert infos["team-b"]["invalid_action_code"] == "missing_action"
    assert infos["team-c"]["invalid_action_code"] == "missing_action"
    assert infos["team-a"]["valid_team_count"] == 1
    assert infos["team-a"]["team_evaluated"] is True
    assert infos["team-b"]["team_evaluated"] is False
    assert infos["team-a"]["joint_evaluated"] is True


def test_empty_team_is_not_submitted_and_receives_deterministic_empty_reward(tmp_path):
    calls = []

    def evaluator(csv_paths, **kwargs):
        calls.append(tuple(csv_paths))
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-a", score=5.0),))

    env = ParallelConnectionPlanningEnv(
        team_ids=("team-a", "team-b"),
        env_kwargs=settings(),
        joint_evaluator=evaluator,
        evaluator_artifact_dir=tmp_path,
        evaluator_rules=object(),
        submission_dir=tmp_path,
    )
    env.reset(seed=5)
    env.step(
        {"team-a": add_action(), "team-b": finish_action()}
    )
    _, rewards, terminated, _, infos = env.step({"team-a": finish_action()})

    assert len(calls) == 1
    assert len(calls[0]) == 1
    assert rewards == {"team-a": 5.0, "team-b": -1.0}
    assert terminated == {"team-a": True, "team-b": True}
    assert infos["team-b"]["invalid_action_code"] == "empty_plan"


def test_team_input_order_does_not_change_rewards_or_terminal_results(tmp_path):
    def evaluator(csv_paths, **kwargs):
        return SimpleNamespace(team_scores=(
            SimpleNamespace(team_id="team-a", score=11.0),
            SimpleNamespace(team_id="team-b", score=22.0),
        ))

    def run(team_ids):
        env = ParallelConnectionPlanningEnv(
            team_ids=team_ids,
            env_kwargs=settings(),
            joint_evaluator=evaluator,
            evaluator_artifact_dir=tmp_path,
            evaluator_rules=object(),
            submission_dir=tmp_path,
        )
        env.reset(seed=8)
        env.step({"team-a": add_action(0), "team-b": add_action(1)})
        return env.step({"team-b": finish_action(), "team-a": finish_action()})

    first = run(("team-a", "team-b"))
    second = run(("team-b", "team-a"))
    assert first[1:] == second[1:]


def test_aggregate_info_is_sanitized_and_reset_step_are_deterministic():
    first = make_env()
    second = make_env()
    first_reset = first.reset(seed=12)
    second_reset = second.reset(seed=12)
    for team_id in first_reset[0]:
        for key in first_reset[0][team_id]:
            assert np.array_equal(first_reset[0][team_id][key], second_reset[0][team_id][key])
    assert first_reset[1] == second_reset[1]

    result = first.step({"team-a": add_action(), "team-b": add_action(1)})
    for info in result[-1].values():
        assert set(info) >= {"team_count", "finished_team_count", "valid_team_count", "evaluated"}
        assert "team_scores" not in info
        assert "projection_fractions" not in info
        assert "plan" not in info
        assert "projected_capacity_mw_by_team" not in info


def test_parallel_release_manifest_metadata_is_sanitized_in_reset_and_terminal_info():
    manifest = make_release_manifest(
        scenario_year=2029,
        scenario_block="jan_jun",
        rules={"private_rule": "secret"},
        config={"private_plan": {"capacity": 999}},
    )
    env = make_env(release_manifest=manifest)

    _, reset_infos = env.reset(seed=20)
    for info in reset_infos.values():
        assert info["release_version"] == manifest.release_version
        assert info["release_environment_version"] == manifest.environment_version
        assert info["release_plan_schema_version"] == manifest.plan_schema_version
        assert info["release_manifest_digest"] == manifest.digest()
        assert "release_manifest" not in info
        assert "private_rule" not in repr(info)
        assert "private_plan" not in repr(info)

    _, _, _, _, terminal_infos = finish_both(env)
    for info in terminal_infos.values():
        assert info["release_manifest_digest"] == manifest.digest()
        assert "release_manifest" not in info


def test_parallel_constructor_accepts_release_manifest_matching_configured_scenario():
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
def test_parallel_constructor_rejects_release_manifest_with_mismatched_scenario(
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


def test_configured_default_evaluator_is_called_once(monkeypatch, tmp_path):
    calls = []

    def evaluate_csv_batch(csv_paths, **kwargs):
        calls.append((tuple(csv_paths), kwargs))
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-a", score=3.0),))

    monkeypatch.setattr(batch_evaluation, "evaluate_csv_batch", evaluate_csv_batch)
    env = ParallelConnectionPlanningEnv(
        team_ids=("team-a",),
        env_kwargs=settings(),
        evaluator_artifact_dir=tmp_path,
        evaluator_rules=object(),
        submission_dir=tmp_path,
    )
    env.reset(seed=14)
    env.step({"team-a": add_action()})
    _, rewards, terminated, _, infos = env.step({"team-a": finish_action()})

    assert len(calls) == 1
    assert rewards["team-a"] == 3.0
    assert terminated["team-a"] is True
    assert infos["team-a"]["evaluated"] is True


def test_post_terminal_step_does_not_repeat_joint_evaluation(tmp_path):
    calls = []

    def evaluator(csv_paths, **kwargs):
        calls.append(tuple(csv_paths))
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-a", score=3.0),))

    env = ParallelConnectionPlanningEnv(
        team_ids=("team-a",),
        env_kwargs=settings(),
        joint_evaluator=evaluator,
        evaluator_artifact_dir=tmp_path,
        evaluator_rules=object(),
        submission_dir=tmp_path,
    )
    env.reset(seed=15)
    env.step({"team-a": add_action()})
    env.step({"team-a": finish_action()})
    env.step({"team-a": finish_action()})

    assert len(calls) == 1


def test_missing_evaluator_does_not_claim_evaluation():
    env = make_env()
    env.reset(seed=16)
    _, _, _, _, infos = finish_both(env)

    assert infos["team-a"]["evaluated"] is False
    assert infos["team-b"]["evaluated"] is False


def test_evaluator_failure_is_terminal_and_does_not_retry(tmp_path):
    calls = []

    def evaluator(csv_paths, **kwargs):
        calls.append(tuple(csv_paths))
        raise RuntimeError("solver failure details stay private")

    env = ParallelConnectionPlanningEnv(
        team_ids=("team-a",),
        env_kwargs=settings(),
        joint_evaluator=evaluator,
        evaluator_artifact_dir=tmp_path,
        evaluator_rules=object(),
        submission_dir=tmp_path,
    )
    env.reset(seed=17)
    env.step({"team-a": add_action()})
    _, rewards, _, _, infos = env.step({"team-a": finish_action()})
    _, repeated_rewards, _, _, repeated_infos = env.step({"team-a": finish_action()})

    assert len(calls) == 1
    assert rewards == repeated_rewards == {"team-a": 0.0}
    assert infos["team-a"]["dispatch_status"] == "evaluation_failed"
    assert repeated_infos["team-a"]["dispatch_status"] == "evaluation_failed"
    assert infos["team-a"]["evaluation_error"] is True
    assert "solver failure" not in repr(infos)


def test_unknown_action_keys_are_counted_without_blocking_valid_teams():
    env = make_env()
    env.reset(seed=18)
    _, _, _, _, infos = env.step(
        {"team-a": add_action(), "team-b": add_action(1), "unexpected": add_action(2)}
    )

    assert infos["team-a"]["unknown_action_count"] == 1
    assert infos["team-b"]["unknown_action_count"] == 1


def test_malformed_evaluator_result_is_normalized_without_crashing(tmp_path):
    def evaluator(csv_paths, **kwargs):
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-a", score=float("nan")),))

    env = ParallelConnectionPlanningEnv(
        team_ids=("team-a",),
        env_kwargs=settings(),
        joint_evaluator=evaluator,
        evaluator_artifact_dir=tmp_path,
        evaluator_rules=object(),
        submission_dir=tmp_path,
    )
    env.reset(seed=19)
    env.step({"team-a": add_action()})
    _, rewards, _, _, infos = env.step({"team-a": finish_action()})

    assert rewards == {"team-a": 0.0}
    assert infos["team-a"]["dispatch_status"] == "evaluation_failed"
    assert infos["team-a"]["evaluation_error"] is True
