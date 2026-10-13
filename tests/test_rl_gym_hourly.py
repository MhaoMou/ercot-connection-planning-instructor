from __future__ import annotations

import json
from collections.abc import Mapping
import math
from pathlib import Path
import re
import time
from typing import Any

import numpy as np
import pytest

from ercot_game.calendars import half_year_calendar
from ercot_game.models import SitePlan
from rl_gym import RLPlanState, ReleaseManifest, make_release_manifest
from rl_gym.hourly_env import HOURLY_ENVIRONMENT_VERSION, HourlyOperationEnv


ACTION_KEYS = {
    "generator_output_mw",
    "storage_charge_mw",
    "storage_discharge_mw",
}
OBSERVATION_KEYS = {"metadata", "asset_state", "exogenous"}
EXOGENOUS_KEYS = {"scenario_year", "scenario_block"}
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
    "evaluator_output",
    "projection_diagnostics",
    "plan",
    "team_scores",
    "resource_level",
}
BENCHMARK_BASE_SEED = 70_000
RELEASED_HORIZONS = {
    (2027, "jan_jun"): 4344,
    (2027, "jul_dec"): 4416,
    (2028, "jan_jun"): 4368,
    (2028, "jul_dec"): 4416,
    (2029, "jan_jun"): 4344,
    (2029, "jul_dec"): 4416,
}
PHASE7_VERIFICATION_PATH = Path(__file__).parents[1] / "rl_gym" / "PHASE7_VERIFICATION.md"
PUBLIC_BENCHMARK_FIELDS = {
    "privacy",
    "environment_version",
    "release_identity",
    "scenario_year",
    "scenario_block",
    "horizon_hours",
    "transition_count",
    "evaluator_call_count",
    "seed",
    "seed_derivation",
    "reset_latency_seconds",
    "step_latency_seconds",
    "episode_latency_seconds",
    "output_size_bytes",
    "output_size_limit_bytes",
}
PUBLIC_RELEASE_IDENTITY_FIELDS = {
    "release_version",
    "release_environment_version",
    "release_plan_schema_version",
    "release_scenario_year",
    "release_scenario_block",
    "release_manifest_digest",
}
PUBLIC_EPISODE_FIELDS = {
    "privacy",
    "environment_version",
    "release_identity",
    "scenario_year",
    "scenario_block",
    "horizon_hours",
    "seed",
    "hour_index",
    "transition_count",
    "initial_observation",
    "initial_info",
    "steps",
    "terminal",
}
PUBLIC_STEP_FIELDS = {"observation", "reward", "terminated", "truncated", "info"}
PUBLIC_INFO_FIELDS = {
    "environment_version",
    "scenario_year",
    "scenario_block",
    "horizon_hours",
    "seed",
    "transition_count",
    "evaluator_call_count",
    "valid_action",
    "local_reward",
    "local_return",
    "terminal_score",
    "episode_return",
    "terminal_evaluation_status",
    "release_manifest_digest",
    "terminal_storage_requirement_mwh",
    "terminal_storage_constraint",
    "invalid_action_code",
    "diagnostic",
}
PUBLIC_TERMINAL_FIELDS = {
    "terminated",
    "truncated",
    "terminal_evaluation_status",
    "terminal_score",
    "local_return",
    "episode_return",
    "terminal_storage_constraint",
    "terminal_storage_requirement_mwh",
}
PUBLIC_RECORD_BASE_BYTES = 64_000
PUBLIC_RECORD_BYTES_PER_HOUR = 2_048


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


def benchmark_seed(year: int, block: str) -> int:
    block_offset = 0 if block == "jan_jun" else 1
    return BENCHMARK_BASE_SEED + (year - 2027) * 2 + block_offset


def parse_verification_evidence_rows() -> dict[tuple[int, str], dict[str, Any]]:
    rows: dict[tuple[int, str], dict[str, Any]] = {}
    for line in PHASE7_VERIFICATION_PATH.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| 20"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) != 13 or not re.fullmatch(r"20\d{2}", cells[0]):
            continue
        year = int(cells[0])
        block = cells[1]
        rows[(year, block)] = {
            "horizon_hours": int(cells[2]),
            "transition_count": int(cells[3]),
            "evaluator_call_count": int(cells[4]),
            "seed": int(cells[5]),
            "output_size_bytes": int(cells[9].replace(",", "")),
            "output_size_limit_bytes": int(cells[10].replace(",", "")),
            "environment_version": cells[11],
            "release_manifest_digest": cells[12],
        }
    return rows


def assert_public_episode_record_contract(public_record: Mapping[str, Any], horizon: int) -> None:
    assert set(public_record) == PUBLIC_EPISODE_FIELDS
    assert set(public_record["release_identity"]) == PUBLIC_RELEASE_IDENTITY_FIELDS
    assert set(public_record["initial_info"]) == PUBLIC_INFO_FIELDS
    assert public_record["initial_info"]["diagnostic"] is None
    assert len(public_record["steps"]) == horizon
    for step in public_record["steps"]:
        assert set(step) == PUBLIC_STEP_FIELDS
        assert set(step["info"]) == PUBLIC_INFO_FIELDS
        assert step["info"]["diagnostic"] is None
    assert set(public_record["terminal"]) == PUBLIC_TERMINAL_FIELDS
    forbidden_public_keys = PRIVATE_OBSERVATION_KEYS | {"raw_diagnostic", "raw_diagnostics"}
    assert not nested_keys(public_record) & forbidden_public_keys


def run_hourly_benchmark_case(year: int, block: str) -> dict[str, Any]:
    env = make_hourly_env(year=year, block=block)
    seed = benchmark_seed(year, block)
    episode_start = time.perf_counter()
    reset_start = time.perf_counter()
    observation, reset_info = env.reset(seed=seed)
    reset_latency = time.perf_counter() - reset_start
    assert_public_output(observation, reset_info)

    step_latency_total = 0.0
    final_info = reset_info
    for hour in range(env.horizon_hours):
        step_start = time.perf_counter()
        observation, reward, terminated, truncated, final_info = env.step(idle_action())
        step_latency_total += time.perf_counter() - step_start
        assert_public_output(observation, final_info)
        assert final_info["transition_count"] == hour + 1
        assert terminated is (hour == env.horizon_hours - 1)
        assert truncated is False
        assert reward == 0.0

    public_record = env.episode_record()
    public_payload = json.dumps(public_record, sort_keys=True, allow_nan=False)
    assert_public_episode_record_contract(public_record, env.horizon_hours)
    output_size_limit = PUBLIC_RECORD_BASE_BYTES + PUBLIC_RECORD_BYTES_PER_HOUR * env.horizon_hours
    output_size = len(public_payload.encode("utf-8"))
    assert output_size <= output_size_limit
    episode_latency = time.perf_counter() - episode_start
    report = {
        "privacy": public_record["privacy"],
        "environment_version": public_record["environment_version"],
        "release_identity": public_record["release_identity"],
        "scenario_year": year,
        "scenario_block": block,
        "horizon_hours": env.horizon_hours,
        "transition_count": public_record["transition_count"],
        "evaluator_call_count": final_info["evaluator_call_count"],
        "seed": seed,
        "seed_derivation": "70000 + (year - 2027) * 2 + block_offset(jan_jun=0, jul_dec=1)",
        "reset_latency_seconds": reset_latency,
        "step_latency_seconds": step_latency_total / env.horizon_hours,
        "episode_latency_seconds": episode_latency,
        "output_size_bytes": output_size,
        "output_size_limit_bytes": output_size_limit,
    }
    return report


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
        assert_public_output(next_observation, next_info)
        assert next_info["transition_count"] == hour + 1
        assert next_observation["metadata"]["hour_index"] == hour
        assert next_observation["metadata"]["terminated"] is terminated
        assert next_observation["metadata"]["truncated"] is truncated
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


def assert_public_output(observation: Mapping[str, Any], info: Mapping[str, Any]) -> None:
    assert set(observation) == OBSERVATION_KEYS
    assert set(observation["metadata"]) == METADATA_KEYS
    assert set(observation["asset_state"]) == ASSET_STATE_KEYS
    assert set(observation["exogenous"]) == EXOGENOUS_KEYS
    assert not nested_keys(observation) & PRIVATE_OBSERVATION_KEYS
    assert not nested_keys(info) & PRIVATE_OBSERVATION_KEYS
    json.dumps(observation, allow_nan=False)
    json.dumps(info, allow_nan=False)

    metadata = observation["metadata"]
    assert type(metadata["scenario_year"]) is int
    assert isinstance(metadata["scenario_block"], str)
    assert type(metadata["hour_index"]) is int
    assert type(metadata["horizon_hours"]) is int
    assert type(metadata["terminated"]) is bool
    assert type(metadata["truncated"]) is bool
    assert 0 <= metadata["hour_index"] < metadata["horizon_hours"]
    assert metadata["horizon_hours"] in {4344, 4368, 4416}

    asset_state = observation["asset_state"]
    for field in ASSET_STATE_KEYS:
        assert type(asset_state[field]) in {int, float}
        assert not isinstance(asset_state[field], bool)
        assert math.isfinite(float(asset_state[field]))
        assert asset_state[field] >= 0.0
    assert asset_state["storage_soc_mwh"] <= asset_state["storage_energy_mwh"]
    assert asset_state["storage_charge_limit_mw"] <= asset_state["storage_power_mw"]
    assert asset_state["storage_discharge_limit_mw"] <= asset_state["storage_power_mw"]

    exogenous = observation["exogenous"]
    assert type(exogenous["scenario_year"]) is int
    assert isinstance(exogenous["scenario_block"], str)


def assert_action_space_contract(env: HourlyOperationEnv) -> None:
    site = env.plan_state.sites[0]
    expected_bounds = {
        "generator_output_mw": (0.0, site.generator_capacity_mw),
        "storage_charge_mw": (0.0, site.storage_power_mw),
        "storage_discharge_mw": (0.0, site.storage_power_mw),
    }
    for field, (expected_low, expected_high) in expected_bounds.items():
        field_space = env.action_space.spaces[field]
        assert field_space.shape == ()
        assert np.dtype(field_space.dtype) == np.dtype(np.float32)
        assert np.asarray(field_space.low).shape == ()
        assert np.asarray(field_space.high).shape == ()
        assert float(field_space.low) == pytest.approx(expected_low)
        assert float(field_space.high) == pytest.approx(expected_high)

        upper_bound_action = idle_action()
        upper_bound_action[field] = np.float32(expected_high)
        assert env.action_space.contains(upper_bound_action)


def test_hourly_env_declares_fixed_action_and_observation_contract():
    env = make_hourly_env()

    assert set(env.action_space.spaces) == ACTION_KEYS
    assert "storage_energy_mwh" not in env.action_space.spaces
    assert "curtailment_mw" not in env.action_space.spaces
    assert set(env.observation_space.spaces) == OBSERVATION_KEYS

    assert_action_space_contract(env)

    observation, info = env.reset(seed=11)
    assert_public_output(observation, info)
    assert env.observation_space.contains(observation)
    assert env.action_space.contains(idle_action())
    step_observation, _, _, _, step_info = env.step(idle_action())
    assert_public_output(step_observation, step_info)


def test_observation_space_has_a_space_compatible_interface():
    env = make_hourly_env()
    try:
        import gymnasium
    except ImportError:
        gymnasium = None

    if gymnasium is not None:
        assert isinstance(env.observation_space, gymnasium.spaces.Space)
    assert env.observation_space.shape is None
    assert env.observation_space.dtype is None
    assert env.observation_space.contains(env.observation_space.sample())
    assert env.observation_space.seed(19)


def test_hourly_observation_nested_contract_is_public_and_json_safe():
    env = make_hourly_env()
    site = env.plan_state.sites[0]
    observation, info = env.reset(seed=12)

    assert_public_output(observation, info)
    assert observation["metadata"]["scenario_year"] == 2028
    assert observation["metadata"]["scenario_block"] == "jan_jun"
    assert observation["exogenous"] == {
        "scenario_year": 2028,
        "scenario_block": "jan_jun",
    }
    assert observation["exogenous"]["scenario_year"] == observation["metadata"]["scenario_year"]
    assert observation["exogenous"]["scenario_block"] == observation["metadata"]["scenario_block"]
    assert observation["asset_state"]["connection_capacity_mw"] == pytest.approx(site.connection_capacity_mw)
    assert observation["asset_state"]["generator_capacity_mw"] == pytest.approx(site.generator_capacity_mw)
    assert observation["asset_state"]["storage_power_mw"] == pytest.approx(site.storage_power_mw)
    assert observation["asset_state"]["storage_energy_mwh"] == pytest.approx(site.storage_energy_mwh)
    assert observation["asset_state"]["storage_soc_mwh"] == pytest.approx(site.storage_energy_mwh / 2.0)
    assert observation["asset_state"]["storage_charge_limit_mw"] == pytest.approx(site.storage_power_mw)
    assert observation["asset_state"]["storage_discharge_limit_mw"] == pytest.approx(site.storage_power_mw)
    assert "team-b" not in repr(observation)
    assert "evaluator_callback" not in repr(observation)
    step_observation, _, _, _, step_info = env.step(idle_action())
    assert_public_output(step_observation, step_info)


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
    assert_action_space_contract(env)
    observation, info = env.reset(seed=32)
    assert_public_output(observation, info)
    assert env.observation_space.contains(observation)

    expected_horizon = {
        (2027, "jan_jun"): 4344,
        (2027, "jul_dec"): 4416,
        (2028, "jan_jun"): 4368,
        (2028, "jul_dec"): 4416,
        (2029, "jan_jun"): 4344,
        (2029, "jul_dec"): 4416,
    }[(year, block)]
    assert env.horizon_hours == expected_horizon
    assert env.horizon_hours == half_year_calendar(year, block).horizon
    assert observation["metadata"]["hour_index"] == 0
    assert observation["metadata"]["scenario_year"] == year
    assert observation["metadata"]["scenario_block"] == block
    assert observation["metadata"]["horizon_hours"] == expected_horizon
    assert observation["exogenous"] == {
        "scenario_year": year,
        "scenario_block": block,
    }
    next_observation, _, terminated, truncated, next_info = env.step(idle_action())
    assert_public_output(next_observation, next_info)
    assert env.observation_space.contains(next_observation)
    assert not terminated
    assert not truncated
    assert next_observation["metadata"]["hour_index"] == 0
    assert next_observation["metadata"]["terminated"] is False
    assert next_observation["metadata"]["truncated"] is False
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


def test_storage_can_reach_soc_zero_but_cannot_discharge_below_zero():
    env = make_hourly_env(invalid_action_policy="raise")
    env.reset(seed=16)
    discharge = {
        "generator_output_mw": 0.0,
        "storage_charge_mw": 0.0,
        "storage_discharge_mw": 2.0,
    }

    observation, _, _, _, _ = env.step(discharge)
    assert observation["asset_state"]["storage_soc_mwh"] == pytest.approx(2.0)
    observation, _, _, _, _ = env.step(discharge)
    assert observation["asset_state"]["storage_soc_mwh"] == pytest.approx(0.0)

    with pytest.raises(ValueError, match="state|SOC|discharge"):
        env.step({**discharge, "storage_discharge_mw": 0.1})
    assert observation["asset_state"]["storage_soc_mwh"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    "action",
    [
        {"generator_output_mw": 7.0, "storage_charge_mw": 0.0, "storage_discharge_mw": 0.0},
        {"generator_output_mw": -0.1, "storage_charge_mw": 0.0, "storage_discharge_mw": 0.0},
        {"generator_output_mw": 0.0, "storage_charge_mw": 3.0, "storage_discharge_mw": 0.0},
        {"generator_output_mw": 0.0, "storage_charge_mw": 0.0, "storage_discharge_mw": 3.0},
        {"generator_output_mw": 0.0, "storage_charge_mw": 0.0, "storage_discharge_mw": -0.1},
        {"generator_output_mw": 0.0, "storage_charge_mw": 1.0, "storage_discharge_mw": 1.0},
    ],
)
def test_hourly_env_rejects_actions_that_violate_declared_limits(action):
    env = make_hourly_env(invalid_action_policy="raise")
    env.reset(seed=17)

    with pytest.raises(ValueError):
        env.step(action)


@pytest.mark.parametrize("field", sorted(ACTION_KEYS))
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_hourly_env_rejects_nonfinite_values_in_every_numeric_action_field(field, value):
    env = make_hourly_env(invalid_action_policy="raise")
    env.reset(seed=19)
    action = idle_action()
    action[field] = value

    with pytest.raises(ValueError):
        env.step(action)


def test_hourly_env_rejects_unknown_actions():
    env = make_hourly_env(invalid_action_policy="raise")
    env.reset(seed=19)

    with pytest.raises(ValueError):
        env.step(
            {
                **idle_action(),
                "arbitrary_unknown_key": 0.0,
            }
        )


@pytest.mark.parametrize("field", sorted(ACTION_KEYS))
@pytest.mark.parametrize(
    "bad_value",
    [True, [0.0], [0.0, 0.0], np.asarray([0.0]), np.asarray([0.0, 0.0])],
)
def test_hourly_env_rejects_bool_list_and_vector_values_for_scalar_actions(field, bad_value):
    env = make_hourly_env(invalid_action_policy="raise")
    env.reset(seed=19)
    action = idle_action()
    action[field] = bad_value

    with pytest.raises(ValueError):
        env.step(action)


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
        initial_observation, _ = env.reset(seed=23)
        observation, reward, terminated, truncated, info = env.step(action)
        return (
            json.dumps(initial_observation["asset_state"], sort_keys=True, allow_nan=False),
            json.dumps(observation["asset_state"], sort_keys=True, allow_nan=False),
            json.dumps(observation, sort_keys=True, allow_nan=False),
            reward,
            terminated,
            truncated,
            json.dumps(info, sort_keys=True, allow_nan=False),
        )

    first = run_once()
    second = run_once()

    assert first == second
    assert first[0] == first[1]
    assert first[3] == -2.5
    assert first[4] is True
    assert first[5] is False


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
    assert final_info["terminal_storage_constraint"] == "satisfied"
    assert final_observation["asset_state"]["storage_soc_mwh"] == pytest.approx(4.0)

    with pytest.raises(RuntimeError, match="reset"):
        env.step(idle_action())


def test_supplied_plan_state_remains_unchanged_after_terminal_episode():
    env = make_hourly_env()
    supplied_state = env.plan_state
    original_team_id = supplied_state.team_id
    original_sites = supplied_state.sites
    original_horizon = supplied_state.horizon

    terminal_transition(env, seed=30)

    assert env.plan_state is supplied_state
    assert supplied_state.team_id == original_team_id
    assert supplied_state.sites == original_sites
    assert supplied_state.horizon == original_horizon
    assert supplied_state.horizon_hours == 4368


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


def test_terminal_storage_violation_raises_in_raise_mode():
    env = make_hourly_env(invalid_action_policy="raise")
    env.reset(seed=30)

    for _ in range(env.horizon_hours - 1):
        env.step(idle_action())

    before_observation = env._observation()
    before_info = env._info(valid_action=None)
    before_state = (
        env._transition_count,
        env._hour_index,
        env._storage_soc_mwh,
        env._terminated,
        env._truncated,
        env._local_return,
    )
    with pytest.raises(ValueError, match="terminal storage"):
        env.step(
            {
                "generator_output_mw": 0.0,
                "storage_charge_mw": 0.0,
                "storage_discharge_mw": 1.0,
            }
        )

    assert env._observation() == before_observation
    assert env._info(valid_action=None) == before_info
    assert (
        env._transition_count,
        env._hour_index,
        env._storage_soc_mwh,
        env._terminated,
        env._truncated,
        env._local_return,
    ) == before_state


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
    calls: list[tuple[RLPlanState, ReleaseManifest]] = []

    def evaluator(plan_state: RLPlanState, release_manifest: ReleaseManifest):
        calls.append((plan_state, release_manifest))
        return {"team-a": {"score": 17.5}}

    env = make_hourly_env(evaluator_callback=evaluator)
    env.reset(seed=40)
    env.step(idle_action())
    assert calls == []
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
    callback_plan, callback_release = calls[0]
    assert callback_plan is env.plan_state
    assert callback_release is env.release_manifest

    calls_before_post_terminal_step = len(calls)
    score_before_post_terminal_step = info["terminal_score"]
    return_before_post_terminal_step = info["episode_return"]
    with pytest.raises(RuntimeError, match="reset"):
        env.step(idle_action())
    assert len(calls) == calls_before_post_terminal_step
    assert info["terminal_score"] == score_before_post_terminal_step
    assert info["episode_return"] == return_before_post_terminal_step


def test_terminal_evaluator_is_called_once_per_episode_and_reset_clears_state():
    calls: list[tuple[RLPlanState, ReleaseManifest]] = []

    def evaluator(plan_state: RLPlanState, release_manifest: ReleaseManifest):
        calls.append((plan_state, release_manifest))
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


def test_phase7_deterministic_benchmark_evidence_covers_all_released_combinations():
    reports = [
        run_hourly_benchmark_case(year, block)
        for year, block in RELEASED_HORIZONS
    ]

    assert {(report["scenario_year"], report["scenario_block"]) for report in reports} == set(RELEASED_HORIZONS)
    assert [report["seed"] for report in reports] == [
        benchmark_seed(year, block) for year, block in RELEASED_HORIZONS
    ]
    for report in reports:
        key = (report["scenario_year"], report["scenario_block"])
        assert set(report) == PUBLIC_BENCHMARK_FIELDS
        assert report["privacy"] == "public_sanitized"
        assert report["environment_version"] == HOURLY_ENVIRONMENT_VERSION
        assert report["release_identity"]["release_scenario_year"] == key[0]
        assert report["release_identity"]["release_scenario_block"] == key[1]
        assert report["release_identity"]["release_manifest_digest"]
        assert report["horizon_hours"] == RELEASED_HORIZONS[key]
        assert report["transition_count"] == RELEASED_HORIZONS[key]
        assert report["evaluator_call_count"] == 0
        assert report["seed_derivation"] == (
            "70000 + (year - 2027) * 2 + block_offset(jan_jun=0, jul_dec=1)"
        )
        for field in (
            "reset_latency_seconds",
            "step_latency_seconds",
            "episode_latency_seconds",
        ):
            assert math.isfinite(report[field])
            assert report[field] >= 0.0
        assert report["output_size_bytes"] > 0
        assert report["output_size_bytes"] <= report["output_size_limit_bytes"]

    payload = json.dumps(reports, sort_keys=True, allow_nan=False)
    private_report_keys = {
        "action",
        "actions",
        "plan",
        "private_plan",
        "raw_diagnostic",
        "raw_diagnostics",
        "diagnostic",
        "evaluator_output",
        "solver",
        "storage_schedule",
        "team_scores",
    }
    assert not nested_keys(json.loads(payload)) & private_report_keys


def test_phase7_checked_in_evidence_matches_fresh_stable_benchmark_fields():
    evidence = parse_verification_evidence_rows()
    assert set(evidence) == set(RELEASED_HORIZONS)

    for year, block in RELEASED_HORIZONS:
        fresh = run_hourly_benchmark_case(year, block)
        checked_in = evidence[(year, block)]
        for field in (
            "horizon_hours",
            "transition_count",
            "evaluator_call_count",
            "seed",
            "output_size_bytes",
            "output_size_limit_bytes",
        ):
            assert checked_in[field] == fresh[field]
        assert checked_in["environment_version"] == fresh["environment_version"]
        assert checked_in["release_manifest_digest"] == fresh["release_identity"]["release_manifest_digest"]


def test_phase7_verification_report_states_scope_and_open_acceptance_items():
    report = PHASE7_VERIFICATION_PATH.read_text(encoding="utf-8")

    for term in (
        "three-field action",
        "generator_output_mw",
        "storage_charge_mw",
        "storage_discharge_mw",
        "scenario_year/scenario_block",
        "one-time optional evaluator callback",
        "DEPENDENCY-LIMITED",
        "no score claim",
        "public_sanitized",
        "output_size_bytes",
        "output_size_limit_bytes",
        "seed derivation",
        "Machine profile and invocation",
        "macOS 27.0",
        "CPython 3.13.9",
        "NumPy",
        "Gymnasium",
        "highspy",
        "Exact benchmark invocation",
        "2026-10-13T02:19:30Z",
        "64,000 + 2,048 * horizon_hours",
        "Evaluator latency",
        "OPEN/NOT MEASURED",
        "zero evaluator callbacks",
        "focused benchmark result",
    ):
        assert term in report
    assert re.search(r"focused benchmark result.*\d+ passed in \d+\.\d+s", report)
    assert "PYTHONPATH=src:. /opt/anaconda3/bin/python -m pytest -p no:cacheprovider" in report
    assert "tests/test_rl_gym_hourly.py::test_phase7_deterministic_benchmark_evidence_covers_all_released_combinations -q" in report
    assert "Solver-backed evaluator parity" in report
    assert "DEPENDENCY-LIMITED" in report
    assert "- [ ] Computational budget" in report
    assert "- [ ] Solver-backed evaluator parity" in report
