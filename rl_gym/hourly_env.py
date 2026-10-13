"""Deterministic, solver-free hourly operation around a frozen RL plan."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import math
from numbers import Real
from typing import Any

import numpy as np

from .adapter import RLPlanState
from .release import ReleaseManifest

try:  # Gymnasium is optional, matching the existing planning environment.
    import gymnasium as gym
    from gymnasium import spaces
except ImportError:  # pragma: no cover - exercised in minimal installations
    gym = None

    class _Box:
        def __init__(self, low, high, shape=None, dtype=np.float32):
            self.dtype = np.dtype(dtype)
            if shape is None:
                self.low = np.asarray(low, dtype=self.dtype)
                self.high = np.asarray(high, dtype=self.dtype)
            else:
                self.low = np.broadcast_to(np.asarray(low, dtype=self.dtype), shape).copy()
                self.high = np.broadcast_to(np.asarray(high, dtype=self.dtype), shape).copy()
            self.shape = self.low.shape

        def contains(self, value):
            array = np.asarray(value)
            return (
                array.shape == self.shape
                and np.all(np.isfinite(array))
                and np.all(array >= self.low)
                and np.all(array <= self.high)
            )

        def seed(self, seed=None):
            del seed
            return []

    class _Dict:
        def __init__(self, spaces_map):
            self.spaces = dict(spaces_map)

        def contains(self, value):
            return (
                isinstance(value, Mapping)
                and set(value) == set(self.spaces)
                and all(self.spaces[key].contains(value[key]) for key in self.spaces)
            )

        def seed(self, seed=None):
            del seed
            return []

    class _FallbackSpaces:
        Box = _Box
        Dict = _Dict

    spaces = _FallbackSpaces()


HOURLY_ENVIRONMENT_VERSION = "phase7-hourly-v1"
ACTION_FIELDS = (
    "generator_output_mw",
    "storage_charge_mw",
    "storage_discharge_mw",
)
OBSERVATION_FIELDS = ("metadata", "asset_state", "exogenous")
METADATA_FIELDS = (
    "scenario_year",
    "scenario_block",
    "hour_index",
    "horizon_hours",
    "terminated",
    "truncated",
)
ASSET_STATE_FIELDS = (
    "connection_capacity_mw",
    "generator_capacity_mw",
    "storage_power_mw",
    "storage_energy_mwh",
    "storage_soc_mwh",
    "storage_charge_limit_mw",
    "storage_discharge_limit_mw",
)
EXOGENOUS_FIELDS = ("scenario_year", "scenario_block")


class _ObservationGroupSpace:
    """Small JSON mapping space for the structured public observation."""

    def __init__(self, fields: tuple[str, ...], validator: Callable[[Mapping[str, Any]], bool]):
        self.spaces = {field: object() for field in fields}
        self._validator = validator

    def contains(self, value: Any) -> bool:
        return isinstance(value, Mapping) and set(value) == set(self.spaces) and self._validator(value)


class _ObservationSpace:
    def __init__(self, groups: Mapping[str, _ObservationGroupSpace]):
        self.spaces = dict(groups)

    def contains(self, value: Any) -> bool:
        return (
            isinstance(value, Mapping)
            and set(value) == set(self.spaces)
            and all(self.spaces[key].contains(value[key]) for key in self.spaces)
        )


def _finite_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, (bool, np.bool_)) and math.isfinite(float(value))


def _metadata_contains(value: Mapping[str, Any]) -> bool:
    if type(value["scenario_year"]) is not int or not isinstance(value["scenario_block"], str):
        return False
    if type(value["hour_index"]) is not int or type(value["horizon_hours"]) is not int:
        return False
    if type(value["terminated"]) is not bool or type(value["truncated"]) is not bool:
        return False
    return 0 <= value["hour_index"] < value["horizon_hours"]


def _asset_state_contains(value: Mapping[str, Any]) -> bool:
    if any(not _finite_number(value[field]) or float(value[field]) < 0.0 for field in ASSET_STATE_FIELDS):
        return False
    return (
        value["storage_soc_mwh"] <= value["storage_energy_mwh"]
        and value["storage_charge_limit_mw"] <= value["storage_power_mw"]
        and value["storage_discharge_limit_mw"] <= value["storage_power_mw"]
    )


def _exogenous_contains(value: Mapping[str, Any]) -> bool:
    return type(value["scenario_year"]) is int and isinstance(value["scenario_block"], str)


class HourlyOperationEnv(gym.Env if gym is not None else object):
    """A deterministic local transition environment for one frozen plan.

    The environment does not solve dispatch or compute an official score. An
    application-owned callback may receive ``(plan_state, release_manifest)``
    exactly once at a valid terminal boundary.
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        *,
        plan_state: RLPlanState,
        release_manifest: ReleaseManifest | None = None,
        evaluator_callback: Callable[[RLPlanState, ReleaseManifest | None], Any] | None = None,
        invalid_action_policy: str = "penalize",
        invalid_action_penalty: float = -1.0,
    ) -> None:
        if not isinstance(plan_state, RLPlanState):
            raise TypeError("plan_state must be an RLPlanState")
        if release_manifest is not None and not isinstance(release_manifest, ReleaseManifest):
            raise TypeError("release_manifest must be a ReleaseManifest or None")
        if release_manifest is not None and (
            release_manifest.scenario_year != plan_state.horizon.year
            or release_manifest.scenario_block != plan_state.horizon.round_name
        ):
            raise ValueError("release manifest scenario does not match plan state")
        if invalid_action_policy not in {"penalize", "raise"}:
            raise ValueError("invalid_action_policy must be 'penalize' or 'raise'")
        if evaluator_callback is not None and not callable(evaluator_callback):
            raise TypeError("evaluator_callback must be callable or None")
        if not _finite_number(invalid_action_penalty):
            raise ValueError("invalid_action_penalty must be finite")

        self.plan_state = plan_state
        self.release_manifest = release_manifest
        self.evaluator_callback = evaluator_callback
        self.invalid_action_policy = invalid_action_policy
        self.invalid_action_penalty = float(invalid_action_penalty)

        self.scenario_year = int(plan_state.horizon.year)
        self.scenario_block = str(plan_state.horizon.round_name)
        self.horizon_hours = int(plan_state.horizon_hours)
        self._connection_capacity_mw = float(sum(site.connection_capacity_mw for site in plan_state.sites))
        self._generator_capacity_mw = float(sum(site.generator_capacity_mw for site in plan_state.sites))
        self._storage_power_mw = float(sum(site.storage_power_mw for site in plan_state.sites))
        self._storage_energy_mwh = float(sum(site.storage_energy_mwh for site in plan_state.sites))
        for field, value in (
            ("connection capacity", self._connection_capacity_mw),
            ("generator capacity", self._generator_capacity_mw),
            ("storage power", self._storage_power_mw),
            ("storage energy", self._storage_energy_mwh),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{field} must be finite and non-negative")
        self._terminal_storage_requirement_mwh = self._storage_energy_mwh / 2.0

        self.action_space = spaces.Dict({
            "generator_output_mw": spaces.Box(
                0.0, self._generator_capacity_mw, shape=(), dtype=np.float32
            ),
            "storage_charge_mw": spaces.Box(
                0.0, self._storage_power_mw, shape=(), dtype=np.float32
            ),
            "storage_discharge_mw": spaces.Box(
                0.0, self._storage_power_mw, shape=(), dtype=np.float32
            ),
        })
        self.observation_space = _ObservationSpace({
            "metadata": _ObservationGroupSpace(METADATA_FIELDS, _metadata_contains),
            "asset_state": _ObservationGroupSpace(ASSET_STATE_FIELDS, _asset_state_contains),
            "exogenous": _ObservationGroupSpace(EXOGENOUS_FIELDS, _exogenous_contains),
        })

        self._seed: int | None = None
        self._hour_index = 0
        self._transition_count = 0
        self._storage_soc_mwh = self._terminal_storage_requirement_mwh
        self._terminated = False
        self._truncated = False
        self._local_return = 0.0
        self._terminal_score: float | None = None
        self._terminal_evaluation_status = "not_evaluated"
        self._evaluator_call_count = 0

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        del options
        if gym is not None:
            super().reset(seed=seed)
        self._seed = None if seed is None else int(seed)
        self._hour_index = 0
        self._transition_count = 0
        self._storage_soc_mwh = self._terminal_storage_requirement_mwh
        self._terminated = False
        self._truncated = False
        self._local_return = 0.0
        self._terminal_score = None
        self._terminal_evaluation_status = "not_evaluated"
        self._evaluator_call_count = 0
        return self._observation(), self._info()

    def step(self, action: Mapping[str, Any]):
        if self._terminated or self._truncated:
            raise RuntimeError("episode is complete; call reset() before step()")

        values, diagnostic = self._validate_action(action)
        if diagnostic is not None:
            return self._invalid_action(diagnostic[0], diagnostic[1])

        charge = values["storage_charge_mw"]
        discharge = values["storage_discharge_mw"]
        next_soc = self._storage_soc_mwh + charge - discharge
        if discharge > self._storage_soc_mwh + 1e-9:
            return self._invalid_action("storage_discharge_exceeds_soc", "storage discharge exceeds current state of charge")
        if next_soc < -1e-9:
            return self._invalid_action("storage_soc_below_zero", "storage state of charge cannot be negative")
        if next_soc > self._storage_energy_mwh + 1e-9:
            return self._invalid_action("storage_energy_exceeded", "storage charge exceeds remaining energy capacity")

        self._storage_soc_mwh = min(self._storage_energy_mwh, max(0.0, next_soc))
        self._transition_count += 1
        self._hour_index = self._transition_count - 1
        reward = 0.0
        self._local_return += reward

        if self._transition_count >= self.horizon_hours:
            self._terminated = True
            if self._storage_soc_mwh + 1e-9 < self._terminal_storage_requirement_mwh:
                if self.invalid_action_policy == "raise":
                    raise ValueError("terminal storage constraint violated: final state of charge is below half full")
                reward = self.invalid_action_penalty
                self._local_return += reward
                info = self._info(
                    valid_action=True,
                    terminal_storage_requirement_mwh=self._terminal_storage_requirement_mwh,
                    terminal_storage_constraint="violated",
                )
                return self._observation(), reward, True, False, info
            reward, terminal_info = self._terminal_result()
            info = self._info(valid_action=True) | terminal_info
            return self._observation(), reward, True, False, info

        return self._observation(), reward, False, False, self._info(valid_action=True)

    def _validate_action(self, action: Any):
        if not isinstance(action, Mapping):
            return None, ("action_not_mapping", "action must be a mapping")
        if set(action) != set(ACTION_FIELDS):
            return None, ("unknown_or_missing_action_field", "action must contain exactly the three hourly fields")
        values: dict[str, float] = {}
        for field in ACTION_FIELDS:
            value = action[field]
            if not _finite_number(value):
                return None, ("nonfinite_or_non_scalar_action", f"{field} must be a finite scalar")
            values[field] = float(value)
        if any(values[field] < 0.0 for field in ACTION_FIELDS):
            return None, ("negative_action", "hourly action values must be non-negative")
        if values["generator_output_mw"] > self._generator_capacity_mw + 1e-9:
            return None, ("generator_output_exceeds_capacity", "generator output exceeds owned capacity")
        if values["storage_charge_mw"] > self._storage_power_mw + 1e-9:
            return None, ("storage_charge_exceeds_power", "storage charge exceeds owned power capacity")
        if values["storage_discharge_mw"] > self._storage_power_mw + 1e-9:
            return None, ("storage_discharge_exceeds_power", "storage discharge exceeds owned power capacity")
        if values["storage_charge_mw"] > 0.0 and values["storage_discharge_mw"] > 0.0:
            return None, ("simultaneous_charge_discharge", "simultaneous storage charge and discharge are not allowed")
        return values, None

    def _invalid_action(self, code: str, diagnostic: str):
        if self.invalid_action_policy == "raise":
            raise ValueError(diagnostic)
        self._transition_count += 1
        self._hour_index = min(self._transition_count - 1, self.horizon_hours - 1)
        self._terminated = True
        self._local_return += self.invalid_action_penalty
        info = self._info(valid_action=False)
        info.update({"invalid_action_code": code, "diagnostic": diagnostic})
        return self._observation(), self.invalid_action_penalty, True, False, info

    def _terminal_result(self) -> tuple[float, dict[str, Any]]:
        self._terminal_evaluation_status = "not_evaluated"
        terminal_info: dict[str, Any] = {
            "terminal_storage_requirement_mwh": self._terminal_storage_requirement_mwh,
            "terminal_storage_constraint": "satisfied",
        }
        if self.evaluator_callback is None:
            return 0.0, terminal_info
        if self._evaluator_call_count:
            return 0.0, terminal_info

        self._evaluator_call_count = 1
        try:
            result = self.evaluator_callback(self.plan_state, self.release_manifest)
            score = _extract_score(result, self.plan_state.team_id)
        except Exception:
            self._terminal_evaluation_status = "evaluation_failed"
            return 0.0, terminal_info

        self._terminal_score = score
        self._terminal_evaluation_status = "evaluated"
        terminal_info.update({"terminal_score": score, "episode_return": self._local_return + score})
        return score, terminal_info

    def _observation(self) -> dict[str, Any]:
        return {
            "metadata": {
                "scenario_year": self.scenario_year,
                "scenario_block": self.scenario_block,
                "hour_index": int(self._hour_index),
                "horizon_hours": self.horizon_hours,
                "terminated": bool(self._terminated),
                "truncated": bool(self._truncated),
            },
            "asset_state": {
                "connection_capacity_mw": self._connection_capacity_mw,
                "generator_capacity_mw": self._generator_capacity_mw,
                "storage_power_mw": self._storage_power_mw,
                "storage_energy_mwh": self._storage_energy_mwh,
                "storage_soc_mwh": self._storage_soc_mwh,
                "storage_charge_limit_mw": min(
                    self._storage_power_mw,
                    max(0.0, self._storage_energy_mwh - self._storage_soc_mwh),
                ),
                "storage_discharge_limit_mw": min(self._storage_power_mw, self._storage_soc_mwh),
            },
            "exogenous": {
                "scenario_year": self.scenario_year,
                "scenario_block": self.scenario_block,
            },
        }

    def _info(self, *, valid_action: bool | None = None, **extra: Any) -> dict[str, Any]:
        info: dict[str, Any] = {
            "environment_version": HOURLY_ENVIRONMENT_VERSION,
            "scenario_year": self.scenario_year,
            "scenario_block": self.scenario_block,
            "horizon_hours": self.horizon_hours,
            "seed": self._seed,
            "transition_count": self._transition_count,
            "evaluator_call_count": self._evaluator_call_count,
            "valid_action": valid_action,
            "local_reward": 0.0,
            "local_return": self._local_return,
            "terminal_score": self._terminal_score,
            "episode_return": self._local_return if self._terminal_score is None else self._local_return + self._terminal_score,
            "terminal_evaluation_status": self._terminal_evaluation_status,
            "release_manifest_digest": (
                None if self.release_manifest is None else self.release_manifest.digest()
            ),
        }
        info.update(extra)
        return info


def _extract_score(result: Any, team_id: str) -> float:
    scores = getattr(result, "team_scores", result)
    if isinstance(scores, Mapping):
        record = scores.get(team_id, scores)
    else:
        record = next(item for item in scores if getattr(item, "team_id", None) == team_id)
    if isinstance(record, Mapping):
        score = record.get("score")
    else:
        score = getattr(record, "score", None)
    if score is None or not _finite_number(score):
        raise ValueError("terminal evaluator result must contain a finite team score")
    return float(score)


__all__ = ["HOURLY_ENVIRONMENT_VERSION", "HourlyOperationEnv"]
