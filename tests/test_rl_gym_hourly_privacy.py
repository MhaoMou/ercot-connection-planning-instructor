from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from ercot_game.models import SitePlan
from rl_gym import RLPlanState, ReleaseManifest, make_release_manifest
from rl_gym.hourly_env import HOURLY_ENVIRONMENT_VERSION, HourlyOperationEnv


ACTION_FIELDS = {
    "generator_output_mw",
    "storage_charge_mw",
    "storage_discharge_mw",
}
PRIVATE_KEYS = {
    "action",
    "actions",
    "asset_id",
    "asset_ids",
    "bus_id",
    "dispatch",
    "evaluator_callback",
    "evaluator_object",
    "evaluator_output",
    "plan",
    "plans",
    "private_plan",
    "projection_diagnostics",
    "raw_diagnostics",
    "raw_score",
    "resource_level",
    "solver",
    "storage_schedule",
    "team_scores",
}


def make_hourly_env() -> HourlyOperationEnv:
    plan_state = RLPlanState.for_half_year(
        team_id="team-a",
        sites=(
            SitePlan(
                "site-a",
                "b0001",
                connection_capacity_mw=40.0,
                generator_capacity_mw=6.0,
                storage_power_mw=2.0,
                storage_energy_mwh=8.0,
            ),
        ),
        year=2028,
        round_name="jan_jun",
    )
    release_manifest = make_release_manifest(
        scenario_year=2028,
        scenario_block="jan_jun",
        rules={},
        config={},
    )
    return HourlyOperationEnv(plan_state=plan_state, release_manifest=release_manifest)


def idle_action() -> dict[str, float]:
    return {
        "generator_output_mw": 0.0,
        "storage_charge_mw": 0.0,
        "storage_discharge_mw": 0.0,
    }


def nested_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        keys = {key for key in value if isinstance(key, str)}
        for item in value.values():
            keys.update(nested_keys(item))
        return keys
    if isinstance(value, (list, tuple)):
        keys: set[str] = set()
        for item in value:
            keys.update(nested_keys(item))
        return keys
    return set()


def run_short_episode(env: HourlyOperationEnv) -> None:
    env.reset(seed=41)
    env.step(idle_action())
    env.step(idle_action())


def test_public_episode_record_is_sanitized_json_safe_and_bounded():
    env = make_hourly_env()
    run_short_episode(env)

    record = env.episode_record()

    assert record["privacy"] == "public_sanitized"
    assert record["environment_version"] == HOURLY_ENVIRONMENT_VERSION
    assert record["release_identity"] == {
        "release_version": "phase5-release-v1",
        "release_environment_version": "phase5-environment-v1",
        "release_plan_schema_version": "phase5-plan-schema-v1",
        "release_scenario_year": 2028,
        "release_scenario_block": "jan_jun",
        "release_manifest_digest": env.release_manifest.digest(),
    }
    assert record["transition_count"] == 2
    assert record["hour_index"] == 1
    assert len(record["steps"]) == 2
    assert len(record["steps"]) <= env.horizon_hours
    assert all("action" not in step and "actions" not in step for step in record["steps"])
    assert not nested_keys(record) & PRIVATE_KEYS
    json.dumps(record, sort_keys=True, allow_nan=False)


def test_private_episode_record_requires_explicit_opt_in_and_contains_action_trace():
    env = make_hourly_env()
    run_short_episode(env)

    public_record = env.episode_record()
    private_record = env.episode_record(include_private_actions=True)

    assert public_record["privacy"] == "public_sanitized"
    assert private_record["privacy"] == "private_replayable"
    assert all(set(step["action"]) == ACTION_FIELDS for step in private_record["steps"])
    assert all("action" not in step for step in public_record["steps"])
    json.dumps(private_record, sort_keys=True, allow_nan=False)

    with pytest.raises(TypeError, match="include_private_actions"):
        env.episode_record(include_private_actions=1)


def test_episode_record_has_deterministic_shape_and_public_terminal_aggregate_fields():
    def run_terminal() -> dict[str, Any]:
        env = make_hourly_env()
        env.reset(seed=52)
        for _ in range(env.horizon_hours):
            env.step(idle_action())
        record = env.episode_record()
        json.dumps(record, sort_keys=True, allow_nan=False)
        return record

    first = run_terminal()
    second = run_terminal()

    assert first == second
    assert first["terminal"] == {
        "terminated": True,
        "truncated": False,
        "terminal_evaluation_status": "not_evaluated",
        "terminal_score": None,
        "local_return": 0.0,
        "episode_return": 0.0,
        "terminal_storage_constraint": "satisfied",
        "terminal_storage_requirement_mwh": 4.0,
    }
    assert first["steps"][-1]["terminated"] is True
    assert first["steps"][-1]["reward"] == 0.0
