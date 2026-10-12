from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from rl_gym import (
    ConnectionPlanningEnv,
    EpisodeStep,
    EpisodeTrace,
    GreedyPolicy,
    RandomPolicy,
    RuleBasedPolicy,
    benchmark_environment,
    load_replay,
    replay_episode,
    run_episode,
    save_replay,
    make_release_manifest,
)


def make_env(**overrides):
    settings = {
        "team_id": "team-a",
        "bus_catalog": ("bus-a", "bus-b", "bus-c"),
        "scenario_year": 2029,
        "scenario_block": "jan_jun",
        "max_sites": 2,
        "max_planning_steps": 4,
        "max_capacity_mw": 20.0,
    }
    settings.update(overrides)
    return ConnectionPlanningEnv(**settings)


def assert_valid_action(env, action):
    assert set(action) == set(env.action_space.spaces)
    assert env.action_space.contains(action)
    if not action["finish"]:
        assert action["generator_capacity_mw"] <= action["requested_capacity_mw"]
        assert action["storage_energy_mwh"] == pytest.approx(4 * action["storage_power_mw"])


@pytest.mark.parametrize("policy_type", [RandomPolicy, GreedyPolicy, RuleBasedPolicy])
def test_baselines_emit_valid_public_structured_actions(policy_type):
    env = make_env()
    observation, info = env.reset(seed=7)
    policy = policy_type(seed=11) if policy_type is RandomPolicy else policy_type()

    for _ in range(3):
        action = policy(observation, info)
        assert_valid_action(env, action)
        observation, _, terminated, truncated, info = env.step(action)
        if terminated or truncated:
            break


def test_greedy_and_rule_based_policies_are_deterministic():
    env = make_env()
    observation, info = env.reset(seed=3)
    for policy in (GreedyPolicy(), RuleBasedPolicy()):
        first = policy(observation, info)
        second = policy(observation, info)
        assert first == second


def test_random_policy_does_not_repeat_add_site_when_alternatives_exist():
    env = make_env()
    observation, info = env.reset(seed=4)
    policy = RandomPolicy(seed=5)
    action = policy(observation, info)
    observation, _, _, _, info = env.step(action)
    next_action = policy(observation, info)
    assert next_action["finish"] == 0
    assert next_action["revise"] == 0
    assert next_action["bus_index"] != action["bus_index"]


def test_run_episode_round_trips_and_replays_without_private_data(tmp_path: Path):
    env = make_env()
    trace = run_episode(env, GreedyPolicy(), seed=13)
    payload = trace.to_dict()
    assert json.dumps(payload)
    assert "other_team" not in repr(payload)
    assert "evaluator_version" in repr(payload)
    assert "evaluator_callback" not in repr(payload)
    assert "dispatch" not in repr(payload)

    path = save_replay(tmp_path / "episode.json", trace)
    loaded = load_replay(path)
    assert loaded == trace
    assert replay_episode(make_env(), loaded) == loaded


def test_replay_detects_tampered_reward():
    trace = run_episode(make_env(), GreedyPolicy(), seed=17)
    tampered = trace.to_dict()
    tampered["steps"][0]["reward"] += 0.25
    with pytest.raises(ValueError, match="reward"):
        replay_episode(make_env(), EpisodeTrace.from_dict(tampered))


def test_replay_detects_tampered_terminal_score():
    trace = run_episode(make_env(), GreedyPolicy(), seed=19)
    tampered = trace.to_dict()
    tampered["terminal_score"] = 123.0
    tampered["terminal_info"]["score"] = 123.0
    with pytest.raises(ValueError, match="terminal score"):
        replay_episode(make_env(), EpisodeTrace.from_dict(tampered))


@pytest.mark.parametrize("edit", ["empty", "non_terminal"])
def test_replay_rejects_incomplete_trace(edit):
    trace = run_episode(make_env(), GreedyPolicy(), seed=29)
    tampered = trace.to_dict()
    if edit == "empty":
        tampered["steps"] = []
    else:
        tampered["steps"][-1]["terminated"] = False
        tampered["steps"][-1]["truncated"] = False
    with pytest.raises(ValueError, match="final step|at least one step"):
        EpisodeTrace.from_dict(tampered)


@pytest.mark.parametrize("field", [
    "environment_version", "scenario_year", "scenario_block", "seed",
    "scenario_manifest_hash", "rules_version", "rules_hash", "evaluator_version",
])
def test_replay_validates_manifest_against_target_environment(field):
    trace = run_episode(
        make_env(
            scenario_manifest_hash="scenario-hash",
            rules_version="rules-v1",
            rules_hash="rules-hash",
            evaluator_version="evaluator-v1",
        ),
        GreedyPolicy(),
        seed=31,
    )
    tampered = trace.to_dict()
    current = tampered["manifest"][field]
    tampered["manifest"][field] = (current + "-tampered") if isinstance(current, str) else current + 1
    with pytest.raises(ValueError, match="manifest"):
        replay_episode(
            make_env(
                scenario_manifest_hash="scenario-hash",
                rules_version="rules-v1",
                rules_hash="rules-hash",
                evaluator_version="evaluator-v1",
            ),
            EpisodeTrace.from_dict(tampered),
        )


@pytest.mark.parametrize("year", [2027, 2028, 2029])
@pytest.mark.parametrize("block", ["jan_jun", "jul_dec"])
def test_replay_runs_for_all_released_half_year_blocks(year, block):
    env = make_env(scenario_year=year, scenario_block=block)
    trace = run_episode(env, GreedyPolicy(), seed=37)
    assert replay_episode(make_env(scenario_year=year, scenario_block=block), trace) == trace


def make_release():
    return make_release_manifest(
        scenario_year=2029,
        scenario_block="jan_jun",
        rules={"private_rule": "do-not-expose"},
        config={"private_plan": {"capacity": 123}},
    )


def test_release_manifest_is_serialized_and_round_trips_in_replay(tmp_path: Path):
    manifest = make_release()
    trace = run_episode(make_env(release_manifest=manifest), GreedyPolicy(), seed=43)

    assert trace.manifest.release_manifest == manifest
    assert trace.to_dict()["manifest"]["release_manifest"] == manifest.to_dict()
    assert load_replay(save_replay(tmp_path / "release.json", trace)) == trace


def test_replay_rejects_release_manifest_before_consuming_actions():
    trace = run_episode(make_env(release_manifest=make_release()), GreedyPolicy(), seed=47)
    calls = []
    target = make_env(release_manifest=make_release_manifest(
        scenario_year=2029,
        scenario_block="jan_jun",
        rules={"private_rule": "changed"},
        config={"private_plan": {"capacity": 123}},
    ))
    original_step = target.step
    target.step = lambda action: (calls.append(action), original_step(action))[1]

    with pytest.raises(ValueError, match="release_manifest"):
        replay_episode(target, trace)
    assert calls == []


def test_legacy_replay_without_release_manifest_still_loads_and_replays(tmp_path: Path):
    trace = run_episode(make_env(), GreedyPolicy(), seed=53)
    payload = trace.to_dict()
    payload["manifest"].pop("release_manifest", None)

    legacy_path = tmp_path / "legacy-rl-replay.json"
    legacy_path.write_text(json.dumps(payload), encoding="utf-8")
    restored = load_replay(legacy_path)
    assert restored.manifest.release_manifest is None
    assert replay_episode(make_env(), restored) == restored


def test_replay_accepts_documented_legacy_vector_actions():
    actions = iter([
        np.asarray([0.0, 10.0, 4.0, 2.0, 8.0, 0.0, 0.0], dtype=np.float32),
        np.asarray([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0], dtype=np.float32),
    ])

    trace = run_episode(make_env(max_planning_steps=4), lambda _observation, _info: next(actions), seed=59)

    assert all(isinstance(step.action, list) for step in trace.steps)
    assert replay_episode(make_env(max_planning_steps=4), trace) == trace


def test_episode_trace_nested_data_is_defensively_copied():
    action = {"values": np.asarray([1.0, 2.0], dtype=np.float32)}
    observation = {"nested": {"values": np.asarray([3.0, 4.0], dtype=np.float32)}}
    info = {"nested": {"values": [5, 6]}}
    step = EpisodeStep(action, observation, 1.0, True, False, info)
    trace = EpisodeTrace(
        manifest=run_episode(make_env(), GreedyPolicy(), seed=61).manifest,
        initial_observation=observation,
        initial_info=info,
        steps=(step,),
        terminal_info=info,
        terminal_score=1.0,
    )

    action["values"][0] = 99.0
    observation["nested"]["values"][0] = 99.0
    info["nested"]["values"][0] = 99
    payload = trace.to_dict()

    assert payload["steps"][0]["action"]["values"] == [1.0, 2.0]
    assert payload["initial_observation"]["nested"]["values"] == [3.0, 4.0]
    assert payload["terminal_info"]["nested"]["values"] == [5, 6]

    payload["steps"][0]["action"]["values"][0] = -1.0
    assert trace.to_dict()["steps"][0]["action"]["values"] == [1.0, 2.0]


def test_benchmark_reports_positive_evaluator_latency(tmp_path: Path):
    def evaluator(*args, **kwargs):
        return SimpleNamespace(team_scores=(SimpleNamespace(team_id="team-a", score=4.0),))

    report = benchmark_environment(
        lambda: make_env(
            max_planning_steps=1,
            evaluator_callback=evaluator,
            evaluator_artifact_dir=tmp_path,
            evaluator_rules=object(),
            submission_dir=tmp_path,
        ),
        GreedyPolicy(),
        episodes=1,
        seed=41,
    )
    assert report["eval_latency_seconds"] > 0


def test_benchmark_report_is_json_serializable_and_has_timing_fields():
    report = benchmark_environment(make_env, GreedyPolicy(), episodes=2, seed=23)
    assert report["episodes"] == 2
    assert report["action_count"] > 0
    for field in (
        "reset_latency_seconds",
        "step_latency_seconds",
        "episode_latency_seconds",
        "eval_latency_seconds",
    ):
        assert field in report
        assert report[field] >= 0
    json.dumps(report)
