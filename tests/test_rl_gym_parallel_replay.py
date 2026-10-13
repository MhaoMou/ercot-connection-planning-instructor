import json

import numpy as np
import pytest

from rl_gym import (
    ParallelConnectionPlanningEnv,
    ParallelEpisodeManifest,
    ParallelEpisodeStep,
    ParallelEpisodeTrace,
    load_parallel_replay,
    make_release_manifest,
    replay_parallel_episode,
    run_parallel_episode,
    save_parallel_replay,
)
from rl_gym.replay import EpisodeManifest, EpisodeStep, EpisodeTrace
from rl_gym.parallel_replay import _json_value, _public_info


def _env(*, release_manifest=None, team_ids=("team-b", "team-a")):
    return ParallelConnectionPlanningEnv(
        team_ids=team_ids,
        env_kwargs={
            "bus_catalog": ("bus-a", "bus-b", "bus-c"),
            "scenario_year": 2029,
            "scenario_block": "jan_jun",
            "max_sites": 2,
            "max_planning_steps": 3,
            "max_capacity_mw": 20.0,
        },
        release_manifest=release_manifest,
    )


def _policies():
    return {
        "team-a": lambda observation, info: np.asarray([0, 0, 0, 0, 0, 1, 0], dtype=np.float32),
        "team-b": lambda observation, info: np.asarray([0, 0, 0, 0, 0, 1, 0], dtype=np.float32),
    }


def _release():
    return make_release_manifest(
        scenario_year=2029,
        scenario_block="jan_jun",
        rules={"version": "rules-v1"},
        config={"experiment": "parallel-replay"},
    )


def test_parallel_episode_default_save_is_public_and_refuses_replay(tmp_path):
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=7)
    path = save_parallel_replay(tmp_path / "parallel.json", trace)
    payload = json.loads(path.read_text())

    assert payload["privacy"] == "public_sanitized"
    assert payload["manifest"]["team_ids"] == ["team-a", "team-b"]
    assert all("actions" not in step for step in payload["steps"])
    serialized = json.dumps(payload)
    for forbidden in (
        "bus_index", "requested_capacity_mw", "generator_capacity_mw",
        "storage_power_mw", "storage_energy_mwh", "private_plan",
    ):
        assert forbidden not in serialized
    assert all("plan" not in key for key in payload["initial_observations"])
    assert all("plan" not in key for step in payload["steps"] for key in step["observations"])

    loaded = load_parallel_replay(path)
    assert loaded.replayable is False
    with pytest.raises(ValueError, match="redacted|private"):
        replay_parallel_episode(_env(release_manifest=_release()), loaded)


def test_parallel_episode_private_opt_in_round_trips_and_replays(tmp_path):
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=7)
    path = save_parallel_replay(tmp_path / "parallel-private.json", trace, include_private_actions=True)
    payload = json.loads(path.read_text())

    assert payload["privacy"] == "private_replayable"
    assert all("actions" in step for step in payload["steps"])
    loaded = load_parallel_replay(path)
    assert loaded.replayable is True
    assert replay_parallel_episode(_env(release_manifest=_release()), loaded) == loaded


def test_parallel_episode_schema_round_trips_and_rejects_unexpected_fields():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=7)
    valid_payloads = {
        "manifest": trace.manifest.to_dict(),
        "step": trace.steps[0].to_dict(),
        "trace": trace.to_dict(),
    }
    parsers = {
        "manifest": ParallelEpisodeManifest.from_dict,
        "step": ParallelEpisodeStep.from_dict,
        "trace": ParallelEpisodeTrace.from_dict,
    }

    assert parsers["manifest"](valid_payloads["manifest"]) == trace.manifest
    assert parsers["step"](valid_payloads["step"]) == trace.steps[0]
    assert parsers["trace"](valid_payloads["trace"]) == trace

    for name, payload in valid_payloads.items():
        payload["unexpected"] = True
        with pytest.raises(ValueError, match="unexpected field"):
            parsers[name](payload)


def test_parallel_episode_accepts_reversed_policy_mapping_order():
    policies = _policies()
    reversed_policies = {team: policies[team] for team in reversed(tuple(policies))}

    trace = run_parallel_episode(_env(release_manifest=_release()), reversed_policies, seed=7)

    assert trace.manifest.team_ids == ("team-a", "team-b")


def test_parallel_episode_constructors_project_aggregate_metadata_to_safe_allowlist():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=7)
    unsafe_metadata = {
        **trace.manifest.aggregate_metadata,
        "plan": {"private": True},
        "rules": {"private": True},
        "config": {"private": True},
        "evaluator": {"private": True},
    }

    manifest = ParallelEpisodeManifest(
        **{**trace.manifest.__dict__, "aggregate_metadata": unsafe_metadata}
    )
    step = ParallelEpisodeStep(
        observations=trace.steps[0].observations,
        actions=trace.steps[0].actions,
        rewards=trace.steps[0].rewards,
        terminated=trace.steps[0].terminated,
        truncated=trace.steps[0].truncated,
        infos=trace.steps[0].infos,
        aggregate_metadata=unsafe_metadata,
    )
    sanitized = ParallelEpisodeTrace(
        manifest=manifest,
        initial_observations=trace.initial_observations,
        initial_infos=trace.initial_infos,
        steps=(step,) + trace.steps[1:],
        terminal_infos=trace.terminal_infos,
        terminal_rewards=trace.terminal_rewards,
    )

    assert "plan" not in manifest.aggregate_metadata
    assert "rules" not in manifest.aggregate_metadata
    assert "config" not in manifest.aggregate_metadata
    assert "evaluator" not in manifest.aggregate_metadata
    assert "plan" not in sanitized.steps[0].aggregate_metadata
    assert "rules" not in sanitized.steps[0].aggregate_metadata


def test_parallel_episode_step_direct_constructor_rejects_malformed_values_and_incomplete_maps():
    with pytest.raises(ValueError, match="same non-empty team IDs"):
        ParallelEpisodeStep(
            observations={"team-a": {}},
            actions={"team-a": [0, 0, 0, 0, 0, 0, 0], "team-b": [0, 0, 0, 0, 0, 0, 0]},
            rewards={"team-a": 0.0, "team-b": 0.0},
            terminated={"team-a": False, "team-b": False},
            truncated={"team-a": False, "team-b": False},
            infos={"team-a": {}, "team-b": {}},
            aggregate_metadata={},
        )

    with pytest.raises(ValueError, match="rewards"):
        ParallelEpisodeStep(
            observations={"team-a": {}, "team-b": {}},
            actions={"team-a": [0, 0, 0, 0, 0, 0, 0], "team-b": [0, 0, 0, 0, 0, 0, 0]},
            rewards={"team-a": True, "team-b": 0.0},
            terminated={"team-a": False, "team-b": False},
            truncated={"team-a": False, "team-b": False},
            infos={"team-a": {}, "team-b": {}},
            aggregate_metadata={},
        )


def test_parallel_episode_direct_constructor_rejects_private_action_fields():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=7)
    actions = dict(trace.steps[0].actions)
    actions["team-a"] = {
        "bus_index": 0,
        "requested_capacity_mw": 0.0,
        "generator_capacity_mw": 0.0,
        "storage_power_mw": 0.0,
        "storage_energy_mwh": 0.0,
        "finish": 0,
        "revise": 0,
        "_private": True,
    }

    with pytest.raises(ValueError, match="action"):
        ParallelEpisodeStep(
            observations=trace.steps[0].observations,
            actions=actions,
            rewards=trace.steps[0].rewards,
            terminated=trace.steps[0].terminated,
            truncated=trace.steps[0].truncated,
            infos=trace.steps[0].infos,
            aggregate_metadata=trace.steps[0].aggregate_metadata,
        )


def test_parallel_episode_trace_direct_constructor_rejects_incomplete_or_bool_terminal_rewards():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=7)

    with pytest.raises(ValueError, match="terminal_rewards"):
        ParallelEpisodeTrace(
            manifest=trace.manifest,
            initial_observations=trace.initial_observations,
            initial_infos=trace.initial_infos,
            steps=trace.steps,
            terminal_infos=trace.terminal_infos,
            terminal_rewards={"team-a": 0.0},
        )

    terminal_rewards = dict(trace.terminal_rewards)
    terminal_rewards["team-a"] = True
    with pytest.raises(ValueError, match="terminal_rewards"):
        ParallelEpisodeTrace(
            manifest=trace.manifest,
            initial_observations=trace.initial_observations,
            initial_infos=trace.initial_infos,
            steps=trace.steps,
            terminal_infos=trace.terminal_infos,
            terminal_rewards=terminal_rewards,
        )


def test_parallel_episode_from_dict_reuses_action_constructor_validation():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=7)
    payload = trace.to_dict()
    payload["steps"][0]["actions"]["team-a"] = {
        "bus_index": 0,
        "requested_capacity_mw": 0.0,
        "generator_capacity_mw": 0.0,
        "storage_power_mw": 0.0,
        "storage_energy_mwh": 0.0,
        "finish": 0,
        "revise": 0,
        "evaluator_private": True,
    }

    with pytest.raises(ValueError, match="action"):
        ParallelEpisodeTrace.from_dict(payload)


def test_parallel_replay_saved_json_excludes_private_observation_fields(tmp_path):
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=8)
    path = save_parallel_replay(tmp_path / "parallel.json", trace)
    payload = json.loads(path.read_text())

    def private_keys(value):
        if isinstance(value, dict):
            found = {key for key in value if key in {"own_plan", "own_plan_mask"}}
            for item in value.values():
                found.update(private_keys(item))
            return found
        if isinstance(value, list):
            found = set()
            for item in value:
                found.update(private_keys(item))
            return found
        return set()

    assert private_keys(payload) == set()
    loaded = load_parallel_replay(path)
    with pytest.raises(ValueError, match="redacted|private"):
        replay_parallel_episode(_env(release_manifest=_release()), loaded)


def test_parallel_trace_does_not_serialize_private_or_evaluator_data():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=8)
    payload = json.dumps(trace.to_dict())

    assert '"plan":' not in payload
    assert "private_plan" not in payload
    assert "projection_diagnostics" not in payload
    assert "team_scores" not in payload
    assert "evaluator_output" not in payload


def test_parallel_episode_constructors_sanitize_private_info_fields():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=8)
    private_info = {
        "score": 1.0,
        "plan": {"private": True},
        "projection_diagnostics": {"private": True},
        "evaluator_output": {"private": True},
    }
    step = trace.steps[0]
    sanitized_step = ParallelEpisodeStep(
        observations=step.observations,
        actions=step.actions,
        rewards=step.rewards,
        terminated=step.terminated,
        truncated=step.truncated,
        infos={team: private_info for team in step.infos},
        aggregate_metadata=step.aggregate_metadata,
    )
    sanitized_trace = ParallelEpisodeTrace(
        manifest=trace.manifest,
        initial_observations=trace.initial_observations,
        initial_infos={team: private_info for team in trace.initial_infos},
        steps=(sanitized_step,) + trace.steps[1:],
        terminal_infos={team: private_info for team in trace.terminal_infos},
        terminal_rewards=trace.terminal_rewards,
    )

    assert sanitized_step.infos == {"team-a": {"score": 1.0}, "team-b": {"score": 1.0}}
    assert sanitized_trace.initial_infos == {"team-a": {"score": 1.0}, "team-b": {"score": 1.0}}
    assert sanitized_trace.terminal_infos == {"team-a": {"score": 1.0}, "team-b": {"score": 1.0}}
    assert sanitized_trace.to_dict()["privacy"] == "private_replayable"

    payload = trace.to_dict()
    payload["initial_infos"]["team-a"] = private_info
    payload["steps"][0]["infos"]["team-a"] = private_info
    payload["terminal_infos"]["team-a"] = private_info
    loaded = ParallelEpisodeTrace.from_dict(payload)
    assert loaded.initial_infos["team-a"] == {"score": 1.0}
    assert loaded.steps[0].infos["team-a"] == {"score": 1.0}
    assert loaded.terminal_infos["team-a"] == {"score": 1.0}
    assert loaded.to_dict()["privacy"] == "private_replayable"


def test_parallel_episode_trace_accepts_reversed_team_mapping_order():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=19)
    reversed_trace = ParallelEpisodeTrace(
        manifest=trace.manifest,
        initial_observations=dict(reversed(list(trace.initial_observations.items()))),
        initial_infos=dict(reversed(list(trace.initial_infos.items()))),
        steps=tuple(
            ParallelEpisodeStep(
                observations=dict(reversed(list(step.observations.items()))),
                actions=dict(reversed(list(step.actions.items()))),
                rewards=dict(reversed(list(step.rewards.items()))),
                terminated=dict(reversed(list(step.terminated.items()))),
                truncated=dict(reversed(list(step.truncated.items()))),
                infos=dict(reversed(list(step.infos.items()))),
                aggregate_metadata=step.aggregate_metadata,
            )
            for step in trace.steps
        ),
        terminal_infos=dict(reversed(list(trace.terminal_infos.items()))),
        terminal_rewards=dict(reversed(list(trace.terminal_rewards.items()))),
    )

    assert list(reversed_trace.initial_infos) == ["team-a", "team-b"]
    assert list(reversed_trace.steps[0].infos) == ["team-a", "team-b"]
    assert list(reversed_trace.terminal_infos) == ["team-a", "team-b"]


def test_parallel_trace_serializes_release_identity_without_rules_or_config():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=8)
    manifest = trace.to_dict()["manifest"]

    assert "release_manifest" not in manifest
    assert manifest["release_version"] == _release().release_version
    assert manifest["release_environment_version"] == _release().environment_version
    assert manifest["release_plan_schema_version"] == _release().plan_schema_version
    assert manifest["release_scenario_year"] == _release().scenario_year
    assert manifest["release_scenario_block"] == _release().scenario_block
    assert manifest["release_manifest_digest"] == _release().digest()
    assert '"rules"' not in json.dumps(manifest)
    assert '"config"' not in json.dumps(manifest)


def test_parallel_replay_rejects_missing_extra_or_reordered_teams_before_reset():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=9)
    for team_ids in (("team-a",), ("team-a", "team-b", "team-c"), ("team-b", "team-a")):
        tampered = trace.to_dict()
        tampered["manifest"]["team_ids"] = list(team_ids)
        with pytest.raises(ValueError, match="team"):
            altered = ParallelEpisodeTrace.from_dict(tampered)
            replay_parallel_episode(_env(release_manifest=_release()), altered)


def test_parallel_replay_rejects_release_mismatch_before_reset():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=10)
    changed = _release().to_dict()
    changed["config"]["experiment"] = "different"
    other = make_release_manifest(**changed)

    with pytest.raises(ValueError, match="release_manifest"):
        replay_parallel_episode(_env(release_manifest=other), trace)


def test_parallel_replay_rejects_tampered_release_digest_before_reset():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=10)
    tampered = trace.to_dict()
    tampered["manifest"]["release_manifest_digest"] = "0" * 64
    altered = ParallelEpisodeTrace.from_dict(tampered)
    calls = []
    env = _env(release_manifest=_release())
    original_reset = env.reset
    env.reset = lambda *args, **kwargs: (calls.append((args, kwargs)), original_reset(*args, **kwargs))[1]

    with pytest.raises(ValueError, match="release_manifest"):
        replay_parallel_episode(env, altered)
    assert calls == []


def test_parallel_replay_rejects_malformed_terminal_trace_before_reset():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=11)
    tampered = trace.to_dict()
    tampered["steps"][-1]["terminated"]["team-a"] = False
    with pytest.raises(ValueError, match="terminal"):
        ParallelEpisodeTrace.from_dict(tampered)


def test_parallel_replay_rejects_tampered_reward():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=12)
    tampered = trace.to_dict()
    tampered["steps"][0]["rewards"]["team-a"] += 1.0
    altered = ParallelEpisodeTrace.from_dict(tampered)

    with pytest.raises(ValueError, match="reward"):
        replay_parallel_episode(_env(release_manifest=_release()), altered)


def test_parallel_manifest_requires_sorted_unique_team_ids():
    with pytest.raises(ValueError, match="sorted"):
        ParallelEpisodeManifest(
            environment_version="env",
            scenario_year=2029,
            scenario_block="jan_jun",
            seed=1,
            team_ids=("team-b", "team-a"),
        )


@pytest.mark.parametrize("value", [{1: "one"}, {1: "one", "1": "string"}])
def test_parallel_json_value_rejects_non_string_mapping_keys(value):
    with pytest.raises(ValueError, match="mapping keys"):
        _json_value(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_parallel_json_value_rejects_non_finite_floats(value):
    with pytest.raises(ValueError, match="finite"):
        _json_value(value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rewards", {"team-a": True, "team-b": 0.0}),
        ("rewards", {"team-a": "0.0", "team-b": 0.0}),
        ("terminated", {"team-a": 1, "team-b": False}),
        ("truncated", {"team-a": "false", "team-b": False}),
    ],
)
def test_parallel_step_from_dict_rejects_coercible_malformed_types(field, value):
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=13)
    tampered = trace.to_dict()
    tampered["steps"][0][field] = value

    with pytest.raises(ValueError, match=field):
        ParallelEpisodeTrace.from_dict(tampered)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("environment_version", 1),
        ("scenario_year", True),
        ("seed", 1.5),
        ("team_ids", ["team-a", 2]),
    ],
)
def test_parallel_manifest_from_dict_rejects_coercible_malformed_types(field, value):
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=14)
    tampered = trace.manifest.to_dict()
    tampered[field] = value

    with pytest.raises(ValueError, match=field):
        ParallelEpisodeManifest.from_dict(tampered)


@pytest.mark.parametrize("payload", [None, [], {}, {"team_ids": ["team-a", "team-b"]}])
def test_parallel_manifest_from_dict_rejects_incomplete_or_non_mapping_payloads(payload):
    with pytest.raises(ValueError):
        ParallelEpisodeManifest.from_dict(payload)


@pytest.mark.parametrize("payload", [None, [], {}, {"observations": {}}])
def test_parallel_step_from_dict_rejects_incomplete_or_non_mapping_payloads(payload):
    with pytest.raises(ValueError):
        from rl_gym import ParallelEpisodeStep

        ParallelEpisodeStep.from_dict(payload)


@pytest.mark.parametrize("payload", [None, [], {}, {"manifest": {}}])
def test_parallel_trace_from_dict_rejects_incomplete_or_non_mapping_payloads(payload):
    with pytest.raises(ValueError):
        ParallelEpisodeTrace.from_dict(payload)


def test_parallel_step_from_dict_rejects_malformed_info_and_missing_team_values():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=16)
    tampered = trace.to_dict()
    tampered["steps"][0]["infos"]["team-a"] = []
    with pytest.raises(ValueError, match="infos"):
        ParallelEpisodeTrace.from_dict(tampered)

    tampered = trace.to_dict()
    tampered["steps"][0]["rewards"].pop("team-b")
    with pytest.raises(ValueError, match="team"):
        ParallelEpisodeTrace.from_dict(tampered)


def test_single_agent_replay_from_dict_rejects_malformed_required_fields():
    with pytest.raises(ValueError):
        EpisodeManifest.from_dict(None)
    with pytest.raises(ValueError):
        EpisodeStep.from_dict({"action": {}, "observation": {}, "reward": True, "terminated": False, "truncated": False})
    with pytest.raises(ValueError):
        EpisodeStep.from_dict({"action": {}, "observation": {}, "reward": 0.0, "terminated": 1, "truncated": False})
    with pytest.raises(ValueError):
        EpisodeTrace.from_dict(None)


def test_parallel_replay_docs_claim_parallel_replay_is_implemented():
    readme = open("rl_gym/README.md", encoding="utf-8").read()
    release = open("rl_gym/RELEASE.md", encoding="utf-8").read()
    combined = (readme + "\n" + release).lower()
    assert "parallel replay" in combined
    assert "not available" not in combined
    assert "experiment-specific wrapper" not in combined


def test_parallel_info_projection_does_not_leak_unknown_private_fields():
    projected = _public_info({
        "score": 1.0,
        "dispatch_status": "evaluated",
        "private_rule": "secret",
        "evaluator_output": {"private": True},
    })

    assert projected == {"score": 1.0}


def test_parallel_replay_rejects_invalid_action_before_child_step():
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=15)
    tampered = trace.to_dict()
    tampered["steps"][0]["actions"]["team-a"] = {
        "bus_index": "not-an-index",
        "requested_capacity_mw": 0.0,
        "generator_capacity_mw": 0.0,
        "storage_power_mw": 0.0,
        "storage_energy_mwh": 0.0,
        "finish": 0,
        "revise": 0,
    }
    altered = ParallelEpisodeTrace.from_dict(tampered)
    env = _env(release_manifest=_release())

    with pytest.raises(ValueError, match="invalid action at step 0"):
        replay_parallel_episode(env, altered)

    assert env.envs["team-a"]._episode_steps == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_parallel_replay_rejects_non_finite_legacy_vectors(value):
    trace = run_parallel_episode(_env(release_manifest=_release()), _policies(), seed=17)
    tampered = trace.to_dict()
    tampered["steps"][0]["actions"]["team-a"][0] = value
    with pytest.raises(ValueError, match="action"):
        ParallelEpisodeTrace.from_dict(tampered)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_single_agent_replay_rejects_non_finite_legacy_vectors(value):
    from rl_gym import ConnectionPlanningEnv, GreedyPolicy, run_episode, replay_episode

    env = ConnectionPlanningEnv(
        team_id="team-a", bus_catalog=("bus-a", "bus-b", "bus-c"),
        scenario_year=2029, scenario_block="jan_jun", max_sites=2,
        max_planning_steps=3, max_capacity_mw=20.0,
    )
    trace = run_episode(env, GreedyPolicy(), seed=18)
    tampered = trace.to_dict()
    tampered["steps"][0]["action"] = [value, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    altered = EpisodeTrace.from_dict(tampered)
    with pytest.raises(ValueError):
        replay_episode(
            ConnectionPlanningEnv(
                team_id="team-a", bus_catalog=("bus-a", "bus-b", "bus-c"),
                scenario_year=2029, scenario_block="jan_jun", max_sites=2,
                max_planning_steps=3, max_capacity_mw=20.0,
            ),
            altered,
        )
