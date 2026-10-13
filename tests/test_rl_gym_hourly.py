from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import numpy as np
import pytest

from ercot_game.calendars import half_year_calendar
from ercot_game.models import SitePlan
from rl_gym import RLPlanState, ReleaseManifest, make_release_manifest
from rl_gym.hourly_env import HourlyOperationEnv


ACTION_KEYS = {
    "generator_output_mw",
    "storage_charge_mw",
    "storage_discharge_mw",
}
OBSERVATION_KEYS = {"metadata", "asset_state", "exogenous"}
METADATA_KEYS = {
    "scenario_year",
    "scenario_block",
    "hour_index",
    "horizon_hours",
    "terminated",
    "truncated",
}
ASSET_STATE_KEYS = {
    "connection_capacity_mw",
    "generator_capacity_mw",
    "storage_power_mw",
    "storage_energy_mwh",
    "storage_soc_mwh",
    "storage_charge_limit_mw",
    "storage_discharge_limit_mw",
}
PRIVATE_OBSERVATION_KEYS = {
    "action",
    "actions",
    "asset_id",
    "asset_ids",
    "bus_id",
    "dispatch",
    "evaluator_callback",
    "other_team",
    "private_plan",
    "raw_score",
    "solver",
    "storage_schedule",
}


def make_hourly_env(
    *,
    year: int = 2028,
    block: str = "jan_jun",
    evaluator_callback=None,
    invalid_action_policy: str = "penalize",
    invalid_action_penalty: float = -1.0,
    release_manifest: ReleaseManifest | None = None,
) -> HourlyOperationEnv:
    state = RLPlanState.for_half_year(
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
        year=year,
        round_name=block,
    )
    release_manifest = release_manifest or make_release_manifest(
        scenario_year=year,
        scenario_block=block,
        rules={},
        config={},
    )
    return HourlyOperationEnv(
        plan_state=state,
        release_manifest=release_manifest,
        evaluator_callback=evaluator_callback,
        invalid_action_policy=invalid_action_policy,
        invalid_action_penalty=invalid_action_penalty,
    )


def idle_action() -> dict[str, float]:
    return {
        "generator_output_mw": 0.0,
        "storage_charge_mw": 0.0,
        "storage_discharge_mw": 0.0,
    }


def terminal_transition(
    env: HourlyOperationEnv,
    *,
    seed: int = 7,
    first_action: Mapping[str, float] | None = None,
):
    observation, info = env.reset(seed=seed)
    transitions = []
    for hour in range(env.horizon_hours):
        result = env.step(first_action if hour == 0 and first_action is not None else idle_action())
        transitions.append(result)
        next_observation, reward, terminated, truncated, next_info = result
        assert next_info["transition_count"] == hour + 1
        if hour < env.horizon_hours - 1:
            assert not terminated
            assert not truncated
        else:
            assert terminated
            assert not truncated
    return observation, info, transitions


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


def test_hourly_env_declares_fixed_action_and_observation_contract():
    env = make_hourly_env()

    assert set(env.action_space.spaces) == ACTION_KEYS
    assert set(env.observation_space.spaces) == OBSERVATION_KEYS

    observation, info = env.reset(seed=11)
    assert set(observation) == OBSERVATION_KEYS
    assert env.action_space.contains(idle_action())
    assert env.observation_space.contains(observation)
    json.dumps(observation, allow_nan=False)
    json.dumps(info, allow_nan=False)


def test_hourly_observation_nested_contract_is_public_and_json_safe():
    env = make_hourly_env()
    observation, info = env.reset(seed=12)

    assert set(observation["metadata"]) == METADATA_KEYS
    assert set(observation["asset_state"]) == ASSET_STATE_KEYS
    assert isinstance(observation["exogenous"], Mapping)
    assert observation["asset_state"]["storage_soc_mwh"] == pytest.approx(4.0)
    assert observation["asset_state"]["storage_charge_limit_mw"] == pytest.approx(2.0)
    assert observation["asset_state"]["storage_discharge_limit_mw"] == pytest.approx(2.0)
    assert not nested_keys(observation) & PRIVATE_OBSERVATION_KEYS
    assert not nested_keys(info) & PRIVATE_OBSERVATION_KEYS
    assert "team-b" not in repr(observation)
    assert "evaluator_callback" not in repr(observation)
    json.dumps(observation, allow_nan=False)
    json.dumps(info, allow_nan=False)


@pytest.mark.parametrize(
    ("block", "expected_horizon"),
    [("jan_jun", 4368), ("jul_dec", 4416)],
)
def test_hourly_env_uses_exact_released_half_year_horizon(block, expected_horizon):
    env = make_hourly_env(block=block)

    assert env.horizon_hours == expected_horizon
    assert env.plan_state.horizon_hours == expected_horizon


@pytest.mark.parametrize("year", [2027, 2028, 2029])
@pytest.mark.parametrize("block", ["jan_jun", "jul_dec"])
def test_hourly_env_supports_every_released_year_and_block(year, block):
    env = make_hourly_env(year=year, block=block)

    assert env.horizon_hours == half_year_calendar(year, block).horizon
    assert env.plan_state.horizon.year == year
    assert env.plan_state.horizon.round_name == block


def test_hourly_constructor_rejects_plan_release_and_callback_mismatches():
    plan_state = make_hourly_env().plan_state
    mismatched_release = make_release_manifest(
        scenario_year=2027,
        scenario_block="jul_dec",
        rules={},
        config={},
    )

    with pytest.raises(TypeError):
        HourlyOperationEnv(plan_state=object())
    with pytest.raises(TypeError):
        HourlyOperationEnv(plan_state=plan_state, release_manifest={})
    with pytest.raises(ValueError, match="release"):
        HourlyOperationEnv(plan_state=plan_state, release_manifest=mismatched_release)
    with pytest.raises(ValueError, match="invalid_action_policy"):
        HourlyOperationEnv(plan_state=plan_state, invalid_action_policy="ignore")
    with pytest.raises(TypeError, match="callback"):
        HourlyOperationEnv(plan_state=plan_state, evaluator_callback=object())


def test_storage_transition_is_no_loss_and_preserves_soc_after_balanced_moves():
    env = make_hourly_env()
    observation, _ = env.reset(seed=13)
    initial_soc = observation["asset_state"]["storage_soc_mwh"]

    charged, _, terminated, truncated, _ = env.step(
        {
            "generator_output_mw": 0.0,
            "storage_charge_mw": 1.0,
            "storage_discharge_mw": 0.0,
        }
    )
    assert not terminated
    assert not truncated
    assert charged["asset_state"]["storage_soc_mwh"] == pytest.approx(initial_soc + 1.0)

    balanced, _, terminated, truncated, _ = env.step(
        {
            "generator_output_mw": 0.0,
            "storage_charge_mw": 0.0,
            "storage_discharge_mw": 1.0,
        }
    )
    assert not terminated
    assert not truncated
    assert balanced["asset_state"]["storage_soc_mwh"] == pytest.approx(initial_soc)
    assert 0.0 <= balanced["asset_state"]["storage_soc_mwh"] <= 8.0


def test_storage_rejects_discharge_beyond_soc_and_charge_beyond_remaining_energy():
    env = make_hourly_env(invalid_action_policy="raise")
    observation, _ = env.reset(seed=14)
    initial_soc = observation["asset_state"]["storage_soc_mwh"]

    with pytest.raises(ValueError, match="state|SOC|discharge"):
        env.step(
            {
                "generator_output_mw": 0.0,
                "storage_charge_mw": 0.0,
                "storage_discharge_mw": initial_soc + 0.1,
            }
        )

    charged, _, terminated, truncated, _ = env.step(
        {
            "generator_output_mw": 0.0,
            "storage_charge_mw": 2.0,
            "storage_discharge_mw": 0.0,
        }
    )
    assert not terminated and not truncated
    assert charged["asset_state"]["storage_soc_mwh"] == pytest.approx(6.0)

    full, _, terminated, truncated, _ = env.step(
        {
            "generator_output_mw": 0.0,
            "storage_charge_mw": 2.0,
            "storage_discharge_mw": 0.0,
        }
    )
    assert not terminated and not truncated
    assert full["asset_state"]["storage_soc_mwh"] == pytest.approx(8.0)

    with pytest.raises(ValueError, match="energy|SOC|charge"):
        env.step(
            {
                "generator_output_mw": 0.0,
                "storage_charge_mw": 0.1,
                "storage_discharge_mw": 0.0,
            }
        )


def test_storage_soc_remains_within_zero_to_capacity_bounds():
    env = make_hourly_env()
    observation, _ = env.reset(seed=15)
    assert 0.0 <= observation["asset_state"]["storage_soc_mwh"] <= 8.0

    for action in (
        {
            "generator_output_mw": 0.0,
            "storage_charge_mw": 1.0,
            "storage_discharge_mw": 0.0,
        },
        {
            "generator_output_mw": 0.0,
            "storage_charge_mw": 0.0,
            "storage_discharge_mw": 1.0,
        },
    ):
        observation, _, _, _, _ = env.step(action)
        soc = observation["asset_state"]["storage_soc_mwh"]
        assert 0.0 <= soc <= 8.0


@pytest.mark.parametrize(
    "action",
    [
        {"generator_output_mw": 7.0, "storage_charge_mw": 0.0, "storage_discharge_mw": 0.0},
        {"generator_output_mw": 0.0, "storage_charge_mw": 3.0, "storage_discharge_mw": 0.0},
        {"generator_output_mw": 0.0, "storage_charge_mw": 0.0, "storage_discharge_mw": 3.0},
        {"generator_output_mw": 0.0, "storage_charge_mw": 1.0, "storage_discharge_mw": 1.0},
    ],
)
def test_hourly_env_rejects_actions_that_violate_declared_limits(action):
    env = make_hourly_env(invalid_action_policy="raise")
    env.reset(seed=17)

    with pytest.raises(ValueError):
        env.step(action)


def test_hourly_env_rejects_nonfinite_and_unknown_actions():
    env = make_hourly_env(invalid_action_policy="raise")
    env.reset(seed=19)

    with pytest.raises(ValueError):
        env.step(
            {
                "generator_output_mw": np.nan,
                "storage_charge_mw": 0.0,
                "storage_discharge_mw": 0.0,
            }
        )

    with pytest.raises(ValueError):
        env.step(
            {
                "generator_output_mw": 0.0,
                "storage_charge_mw": 0.0,
                "storage_discharge_mw": 0.0,
                "curtailment_mw": 0.0,
            }
        )


@pytest.mark.parametrize(
    "action",
    [
        {},
        {"generator_output_mw": 0.0, "storage_charge_mw": 0.0},
        None,
        [],
        {"generator_output_mw": "not-a-number", "storage_charge_mw": 0.0, "storage_discharge_mw": 0.0},
        {"generator_output_mw": float("inf"), "storage_charge_mw": 0.0, "storage_discharge_mw": 0.0},
        {"generator_output_mw": 0.0, "storage_charge_mw": -0.1, "storage_discharge_mw": 0.0},
    ],
)
def test_hourly_env_rejects_missing_negative_nonnumeric_nonmapping_and_infinite_actions(action):
    env = make_hourly_env(invalid_action_policy="raise")
    env.reset(seed=20)

    with pytest.raises(ValueError):
        env.step(action)


@pytest.mark.parametrize(
    "action",
    [
        {},
        {"generator_output_mw": 0.0, "storage_charge_mw": -1.0, "storage_discharge_mw": 0.0},
        None,
        {"generator_output_mw": float("inf"), "storage_charge_mw": 0.0, "storage_discharge_mw": 0.0},
    ],
)
def test_default_invalid_penalty_and_terminal_behavior_is_deterministic(action):
    def run_once():
        env = make_hourly_env(invalid_action_penalty=-2.5)
        env.reset(seed=23)
        observation, reward, terminated, truncated, info = env.step(action)
        return (
            json.dumps(observation, sort_keys=True, allow_nan=False),
            reward,
            terminated,
            truncated,
            json.dumps(info, sort_keys=True, allow_nan=False),
        )

    first = run_once()
    second = run_once()

    assert first == second
    assert first[1] == -2.5
    assert first[2] is True
    assert first[3] is False


def test_hourly_lifecycle_has_one_transition_per_hour_and_terminal_metadata():
    env = make_hourly_env()
    _, _, transitions = terminal_transition(env, seed=29)

    final_observation, final_reward, terminated, truncated, final_info = transitions[-1]
    assert len(transitions) == 4368
    assert final_observation["metadata"]["hour_index"] == 4367
    assert final_observation["metadata"]["horizon_hours"] == 4368
    assert final_observation["metadata"]["terminated"] is True
    assert final_reward == 0.0
    assert terminated
    assert not truncated
    assert final_info["terminal_storage_requirement_mwh"] == pytest.approx(4.0)
    assert final_observation["asset_state"]["storage_soc_mwh"] >= 4.0

    with pytest.raises(RuntimeError, match="reset"):
        env.step(idle_action())


def test_terminal_transition_fails_when_storage_soc_is_below_half_full():
    env = make_hourly_env()
    _, _, transitions = terminal_transition(
        env,
        seed=30,
        first_action={
            "generator_output_mw": 0.0,
            "storage_charge_mw": 0.0,
            "storage_discharge_mw": 1.0,
        },
    )

    final_observation, final_reward, terminated, truncated, final_info = transitions[-1]
    assert terminated
    assert not truncated
    assert final_observation["asset_state"]["storage_soc_mwh"] == pytest.approx(3.0)
    assert final_info["terminal_storage_requirement_mwh"] == pytest.approx(4.0)
    assert final_info["terminal_storage_constraint"] == "violated"
    assert final_reward < 0.0


def test_seeded_idle_traces_are_deterministic_for_both_released_horizons():
    for block in ("jan_jun", "jul_dec"):
        first = terminal_transition(make_hourly_env(block=block), seed=31)[2]
        second = terminal_transition(make_hourly_env(block=block), seed=31)[2]
        assert first == second


def test_default_reward_is_zero_local_reward_without_terminal_evaluator():
    env = make_hourly_env()
    env.reset(seed=37)
    _, reward, _, _, info = env.step(idle_action())

    assert reward == 0.0
    assert info["local_reward"] == 0.0
    assert info["local_return"] == 0.0


def test_terminal_evaluator_is_called_once_and_terminal_score_is_not_double_counted():
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def evaluator(*args, **kwargs):
        calls.append((args, kwargs))
        return {"team-a": {"score": 17.5}}

    env = make_hourly_env(evaluator_callback=evaluator)
    _, _, transitions = terminal_transition(env, seed=41)
    _, reward, terminated, truncated, info = transitions[-1]

    assert len(calls) == 1
    assert terminated
    assert not truncated
    assert reward == pytest.approx(17.5)
    assert info["local_return"] == 0.0
    assert info["terminal_score"] == pytest.approx(17.5)
    assert info["episode_return"] == pytest.approx(17.5)
    assert info["terminal_evaluation_status"] == "evaluated"
    assert len(calls[0][0]) >= 2 or {"plan_state", "release_manifest"} <= set(calls[0][1])
    callback_plan = calls[0][1].get("plan_state")
    if callback_plan is None:
        callback_plan = calls[0][0][0]
    callback_release = calls[0][1].get("release_manifest")
    if callback_release is None:
        callback_release = calls[0][0][1]
    assert isinstance(callback_plan, RLPlanState)
    assert isinstance(callback_release, ReleaseManifest)
    assert callback_plan == env.plan_state
    assert callback_release == env.release_manifest


def test_terminal_evaluator_is_called_once_per_episode_and_reset_clears_state():
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def evaluator(*args, **kwargs):
        calls.append((args, kwargs))
        return {"team-a": {"score": 3.0}}

    env = make_hourly_env(evaluator_callback=evaluator)
    initial_observation, _ = env.reset(seed=45)
    env.step(
        {
            "generator_output_mw": 0.0,
            "storage_charge_mw": 1.0,
            "storage_discharge_mw": 0.0,
        }
    )
    for _ in range(env.horizon_hours - 1):
        env.step(idle_action())
    assert len(calls) == 1

    reset_observation, reset_info = env.reset(seed=46)
    assert reset_observation["asset_state"]["storage_soc_mwh"] == pytest.approx(
        initial_observation["asset_state"]["storage_soc_mwh"]
    )
    assert reset_observation["metadata"]["hour_index"] == 0
    assert reset_observation["metadata"]["terminated"] is False
    assert reset_observation["metadata"]["truncated"] is False
    assert reset_info["transition_count"] == 0
    assert reset_info["evaluator_call_count"] == 0

    for _ in range(env.horizon_hours):
        env.step(idle_action())
    assert len(calls) == 2


def test_no_terminal_evaluator_reports_not_evaluated():
    env = make_hourly_env(evaluator_callback=None)
    _, _, transitions = terminal_transition(env, seed=43)
    _, reward, terminated, truncated, info = transitions[-1]

    assert terminated
    assert not truncated
    assert reward == 0.0
    assert info["terminal_score"] is None
    assert info["episode_return"] == 0.0
    assert info["terminal_evaluation_status"] == "not_evaluated"


def test_terminal_evaluator_failure_is_publicly_status_only():
    def evaluator(*args, **kwargs):
        raise RuntimeError("private evaluator diagnostic")

    env = make_hourly_env(evaluator_callback=evaluator)
    _, _, transitions = terminal_transition(env, seed=47)
    _, reward, terminated, truncated, info = transitions[-1]

    assert terminated
    assert not truncated
    assert reward == 0.0
    assert info["terminal_score"] is None
    assert info["terminal_evaluation_status"] == "evaluation_failed"
    assert "private evaluator diagnostic" not in repr(info)
