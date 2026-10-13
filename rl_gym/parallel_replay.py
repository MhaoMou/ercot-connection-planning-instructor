"""JSON-safe recording and replay for :class:`ParallelConnectionPlanningEnv`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .release import ReleaseManifest
from .replay import _PUBLIC_INFO_KEYS


_PARALLEL_PUBLIC_INFO_KEYS = _PUBLIC_INFO_KEYS - {"plan"}
_PRIVATE_OBSERVATION_KEYS = {
    "own_plan", "own_plan_mask", "plan", "plans", "private_plan", "private_plans",
}
_AGGREGATE_KEYS = {
    "team_count", "finished_team_count", "valid_team_count", "evaluated",
    "joint_evaluated", "evaluation_error", "unknown_action_count",
    "projection_applied", "projected_team_count", "terminated_team_count",
    "truncated_team_count",
}
_ACTION_FIELDS = {
    "bus_index", "requested_capacity_mw", "generator_capacity_mw",
    "storage_power_mw", "storage_energy_mwh", "finish", "revise",
}


def _json_value(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return [_json_value(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("parallel replay mapping keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("parallel replay values must contain finite numbers")
        return value
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")


def _public_observation(value: Any) -> Any:
    """Remove private per-team plan fields from an observation tree."""
    if isinstance(value, Mapping):
        return {
            key: _public_observation(item)
            for key, item in value.items()
            if key not in _PRIVATE_OBSERVATION_KEYS
            and not (isinstance(key, str) and key.startswith("private_") and "plan" in key)
        }
    if isinstance(value, (list, tuple)):
        return [_public_observation(item) for item in value]
    return value


def _public_info(info: Mapping[str, Any]) -> dict[str, Any]:
    return _json_value({
        key: value
        for key, value in info.items()
        if key in _PARALLEL_PUBLIC_INFO_KEYS and key not in _AGGREGATE_KEYS
    })


def _aggregate_info(infos: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if not infos:
        return {}
    first = next(iter(infos.values()))
    aggregate = {key: first[key] for key in _AGGREGATE_KEYS if key in first}
    for info in infos.values():
        actual = {key: info[key] for key in _AGGREGATE_KEYS if key in info}
        if actual != aggregate:
            raise ValueError("parallel replay aggregate metadata differs between teams")
    return _json_value(aggregate)


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _same(left: Any, right: Any, tolerance: float) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(_same(left[key], right[key], tolerance) for key in left)
    if isinstance(left, Sequence) and not isinstance(left, (str, bytes)) and isinstance(right, Sequence):
        return len(left) == len(right) and all(_same(a, b, tolerance) for a, b in zip(left, right))
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=tolerance)
    return left == right


def _is_legacy_vector_action(action: Any) -> bool:
    if isinstance(action, Mapping) or isinstance(action, (str, bytes)):
        return False
    try:
        values = np.asarray(action)
    except (TypeError, ValueError):
        return False
    return values.shape == (7,) and np.issubdtype(values.dtype, np.number) and np.all(np.isfinite(values))


def _validate_recorded_actions(env: Any, actions: Mapping[str, Any], index: int) -> None:
    child_envs = getattr(env, "envs", {})
    action_spaces = getattr(env, "action_spaces", {})
    for team_id in env.team_ids:
        action = actions[team_id]
        action_space = action_spaces.get(team_id) or getattr(child_envs.get(team_id), "action_space", None)
        structured = False
        if action_space is not None:
            try:
                structured = bool(action_space.contains(action))
            except Exception:
                structured = False
        if structured or _is_legacy_vector_action(action):
            continue
        raise ValueError(f"replay mismatch: invalid action at step {index} for team {team_id}")


def _team_map(value: Mapping[str, Any], team_ids: tuple[str, ...], name: str) -> dict[str, Any]:
    value = _mapping(value, name)
    if set(value) != set(team_ids):
        raise ValueError(f"parallel replay {name} must contain exactly the sorted team IDs")
    return {team_id: _copy(value[team_id]) for team_id in team_ids}


def _team_info_map(value: Mapping[str, Mapping[str, Any]], team_ids: tuple[str, ...], name: str) -> dict[str, dict[str, Any]]:
    normalized = _team_map(value, team_ids, name)
    for team_id, info in normalized.items():
        _mapping(info, f"{name}[{team_id!r}]")
    return {team_id: _public_info(normalized[team_id]) for team_id in team_ids}


def _sorted_map(value: Mapping[str, Any]) -> dict[str, Any]:
    value = _mapping(value, "team map")
    return {team_id: _copy(value[team_id]) for team_id in sorted(value)}


def _safe_aggregate_metadata(value: Mapping[str, Any] | None, name: str = "aggregate_metadata") -> dict[str, Any]:
    if value is None:
        return {}
    value = _mapping(value, name)
    try:
        return _json_value({key: value[key] for key in _AGGREGATE_KEYS if key in value})
    except (TypeError, ValueError) as exc:
        raise ValueError(f"parallel replay {name} must contain only JSON-safe values") from exc


def _normalize_action(action: Any, name: str) -> Any:
    if isinstance(action, Mapping):
        action = _mapping(action, name)
        if set(action) != _ACTION_FIELDS:
            raise ValueError(f"parallel replay {name} must contain exactly the allowed action fields")
        try:
            return _json_value(action)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"parallel replay {name} must contain only JSON-safe values") from exc
    if _is_legacy_vector_action(action):
        return _json_value(action)
    raise ValueError(f"parallel replay {name} must be a structured action mapping or finite 7-value vector")


def _optional_type(value: Any, expected: type, name: str) -> None:
    if value is not None and (not isinstance(value, expected) or isinstance(value, bool) and expected is int):
        raise ValueError(f"parallel replay {name} must be {expected.__name__} or null")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"parallel replay {name} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"parallel replay {name} keys must be strings")
    return value


def _required(value: Mapping[str, Any], fields: tuple[str, ...], name: str) -> None:
    missing = [field for field in fields if field not in value]
    if missing:
        raise ValueError(f"parallel replay {name} missing required field(s): {', '.join(missing)}")


def _reject_unknown(value: Mapping[str, Any], fields: tuple[str, ...], name: str) -> None:
    unknown = sorted(set(value) - set(fields))
    if unknown:
        raise ValueError(f"parallel replay {name} contains unexpected field(s): {', '.join(unknown)}")


def _same_keys(mappings: tuple[tuple[str, Mapping[str, Any]], ...], name: str) -> None:
    expected = set(mappings[0][1])
    if not expected or any(set(mapping) != expected for _, mapping in mappings[1:]):
        raise ValueError(f"parallel replay {name} must contain the same non-empty team IDs")


def _release_identity(manifest: ReleaseManifest | None) -> dict[str, Any]:
    if manifest is None:
        return {
            "release_version": None,
            "release_environment_version": None,
            "release_plan_schema_version": None,
            "release_scenario_year": None,
            "release_scenario_block": None,
            "release_manifest_digest": None,
        }
    return {
        "release_version": manifest.release_version,
        "release_environment_version": manifest.environment_version,
        "release_plan_schema_version": manifest.plan_schema_version,
        "release_scenario_year": manifest.scenario_year,
        "release_scenario_block": manifest.scenario_block,
        "release_manifest_digest": manifest.digest(),
    }


def _finite_number(value: Any, name: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"parallel replay {name} must be a finite number")
    return value


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"parallel replay {name} must be a bool")
    return value


@dataclass(frozen=True)
class ParallelEpisodeManifest:
    environment_version: str
    scenario_year: int | None
    scenario_block: str | None
    seed: int | None
    team_ids: tuple[str, ...]
    scenario_manifest_hash: str | None = None
    rules_version: str | None = None
    rules_hash: str | None = None
    evaluator_version: str | None = None
    aggregate_metadata: dict[str, Any] | None = None
    release_version: str | None = None
    release_environment_version: str | None = None
    release_plan_schema_version: str | None = None
    release_scenario_year: int | None = None
    release_scenario_block: str | None = None
    release_manifest_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.team_ids, (list, tuple)):
            raise ValueError("team_ids must be a list or tuple")
        ids = tuple(self.team_ids)
        if not ids or any(not isinstance(i, str) or not i for i in ids):
            raise ValueError("team_ids must contain non-empty strings")
        if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
            raise ValueError("team_ids must be non-empty, unique, and sorted")
        if not isinstance(self.environment_version, str):
            raise ValueError("environment_version must be a string")
        _optional_type(self.scenario_year, int, "scenario_year")
        _optional_type(self.scenario_block, str, "scenario_block")
        _optional_type(self.seed, int, "seed")
        for name in ("scenario_manifest_hash", "rules_version", "rules_hash", "evaluator_version"):
            _optional_type(getattr(self, name), str, name)
        for name in (
            "release_version", "release_environment_version", "release_plan_schema_version",
            "release_scenario_block", "release_manifest_digest",
        ):
            _optional_type(getattr(self, name), str, name)
        _optional_type(self.release_scenario_year, int, "release_scenario_year")
        object.__setattr__(self, "team_ids", ids)
        object.__setattr__(self, "aggregate_metadata", _safe_aggregate_metadata(self.aggregate_metadata))

    def to_dict(self) -> dict[str, Any]:
        return _json_value({
            "environment_version": self.environment_version,
            "scenario_year": self.scenario_year,
            "scenario_block": self.scenario_block,
            "seed": self.seed,
            "team_ids": list(self.team_ids),
            "scenario_manifest_hash": self.scenario_manifest_hash,
            "rules_version": self.rules_version,
            "rules_hash": self.rules_hash,
            "evaluator_version": self.evaluator_version,
            "aggregate_metadata": self.aggregate_metadata,
            "release_version": self.release_version,
            "release_environment_version": self.release_environment_version,
            "release_plan_schema_version": self.release_plan_schema_version,
            "release_scenario_year": self.release_scenario_year,
            "release_scenario_block": self.release_scenario_block,
            "release_manifest_digest": self.release_manifest_digest,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ParallelEpisodeManifest":
        value = _mapping(value, "manifest")
        _required(value, (
            "environment_version", "scenario_year", "scenario_block", "seed", "team_ids",
            "scenario_manifest_hash", "rules_version", "rules_hash", "evaluator_version",
            "aggregate_metadata", "release_version", "release_environment_version",
            "release_plan_schema_version", "release_scenario_year", "release_scenario_block",
            "release_manifest_digest",
        ), "manifest")
        _reject_unknown(value, (
            "environment_version", "scenario_year", "scenario_block", "seed", "team_ids",
            "scenario_manifest_hash", "rules_version", "rules_hash", "evaluator_version",
            "aggregate_metadata", "release_version", "release_environment_version",
            "release_plan_schema_version", "release_scenario_year", "release_scenario_block",
            "release_manifest_digest",
        ), "manifest")
        team_ids = value.get("team_ids")
        if not isinstance(team_ids, list):
            raise ValueError("parallel replay team_ids must be a list")
        return cls(
            environment_version=value["environment_version"],
            scenario_year=value.get("scenario_year"),
            scenario_block=value.get("scenario_block"),
            seed=value.get("seed"),
            team_ids=team_ids,
            scenario_manifest_hash=value.get("scenario_manifest_hash"),
            rules_version=value.get("rules_version"),
            rules_hash=value.get("rules_hash"),
            evaluator_version=value.get("evaluator_version"),
            aggregate_metadata=_mapping(value.get("aggregate_metadata", {}), "aggregate_metadata"),
            release_version=value.get("release_version"),
            release_environment_version=value.get("release_environment_version"),
            release_plan_schema_version=value.get("release_plan_schema_version"),
            release_scenario_year=value.get("release_scenario_year"),
            release_scenario_block=value.get("release_scenario_block"),
            release_manifest_digest=value.get("release_manifest_digest"),
        )


@dataclass(frozen=True)
class ParallelEpisodeStep:
    observations: dict[str, Any]
    actions: dict[str, Any] | None
    rewards: dict[str, float]
    terminated: dict[str, bool]
    truncated: dict[str, bool]
    infos: dict[str, dict[str, Any]]
    aggregate_metadata: dict[str, Any]

    def __post_init__(self) -> None:
        observations = _mapping(self.observations, "observations")
        actions = None if self.actions is None else _mapping(self.actions, "actions")
        rewards = _mapping(self.rewards, "rewards")
        terminated = _mapping(self.terminated, "terminated")
        truncated = _mapping(self.truncated, "truncated")
        infos = _mapping(self.infos, "infos")
        mappings = [
            ("observations", observations), ("rewards", rewards),
            ("terminated", terminated), ("truncated", truncated), ("infos", infos),
        ]
        if actions is not None:
            mappings.insert(1, ("actions", actions))
        _same_keys(tuple(mappings), "step")
        normalized_observations = {
            team_id: _json_value(_public_observation(observation))
            for team_id, observation in observations.items()
        }
        normalized_actions = None if actions is None else {
            team_id: _normalize_action(action, f"actions[{team_id!r}]")
            for team_id, action in actions.items()
        }
        normalized_rewards = {
            team_id: _finite_number(reward, f"rewards[{team_id!r}]")
            for team_id, reward in rewards.items()
        }
        normalized_terminated = {
            team_id: _boolean(flag, f"terminated[{team_id!r}]")
            for team_id, flag in terminated.items()
        }
        normalized_truncated = {
            team_id: _boolean(flag, f"truncated[{team_id!r}]")
            for team_id, flag in truncated.items()
        }
        normalized_infos = {
            team_id: _public_info(_mapping(info, f"infos[{team_id!r}]"))
            for team_id, info in infos.items()
        }
        object.__setattr__(self, "observations", _sorted_map(normalized_observations))
        object.__setattr__(self, "actions", None if normalized_actions is None else _sorted_map(normalized_actions))
        object.__setattr__(self, "rewards", _sorted_map(normalized_rewards))
        object.__setattr__(self, "terminated", _sorted_map(normalized_terminated))
        object.__setattr__(self, "truncated", _sorted_map(normalized_truncated))
        object.__setattr__(self, "infos", {
            team_id: normalized_infos[team_id]
            for team_id in sorted(normalized_infos)
        })
        object.__setattr__(self, "aggregate_metadata", _safe_aggregate_metadata(self.aggregate_metadata))

    def to_dict(self) -> dict[str, Any]:
        value = {
            "observations": self.observations,
            "rewards": self.rewards,
            "terminated": self.terminated,
            "truncated": self.truncated,
            "infos": self.infos,
            "aggregate_metadata": self.aggregate_metadata,
        }
        if self.actions is not None:
            value["actions"] = self.actions
        return _json_value(value)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, allow_redacted: bool = False) -> "ParallelEpisodeStep":
        value = _mapping(value, "step")
        required = ("observations", "rewards", "terminated", "truncated", "infos", "aggregate_metadata")
        if not allow_redacted:
            required = ("observations", "actions", *required[1:])
        _required(value, required, "step")
        _reject_unknown(value, ("observations", "actions", "rewards", "terminated", "truncated", "infos", "aggregate_metadata"), "step")
        observations = _mapping(value["observations"], "observations")
        actions = None if "actions" not in value else _mapping(value["actions"], "actions")
        rewards = _mapping(value["rewards"], "rewards")
        terminated = _mapping(value["terminated"], "terminated")
        truncated = _mapping(value["truncated"], "truncated")
        infos = _mapping(value["infos"], "infos")
        aggregate_metadata = _mapping(value.get("aggregate_metadata", {}), "aggregate_metadata")
        for key, item in rewards.items():
            _finite_number(item, f"rewards[{key!r}]")
        for field, mapping in (("terminated", terminated), ("truncated", truncated)):
            for key, item in mapping.items():
                _boolean(item, f"{field}[{key!r}]")
        for key, item in infos.items():
            _mapping(item, f"infos[{key!r}]")
        mappings = [
            ("observations", observations), ("rewards", rewards),
            ("terminated", terminated), ("truncated", truncated), ("infos", infos),
        ]
        if actions is not None:
            mappings.insert(1, ("actions", actions))
        _same_keys(tuple(mappings), "step")
        return cls(
            observations=dict(observations), actions=None if actions is None else dict(actions),
            rewards=dict(rewards), terminated=dict(terminated), truncated=dict(truncated),
            infos={key: dict(item) for key, item in infos.items()},
            aggregate_metadata=dict(aggregate_metadata),
        )


@dataclass(frozen=True)
class ParallelEpisodeTrace:
    manifest: ParallelEpisodeManifest
    initial_observations: dict[str, Any]
    initial_infos: dict[str, dict[str, Any]]
    steps: tuple[ParallelEpisodeStep, ...]
    terminal_infos: dict[str, dict[str, Any]]
    terminal_rewards: dict[str, float]
    replayable: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, ParallelEpisodeManifest):
            raise ValueError("parallel episode trace manifest must be a ParallelEpisodeManifest")
        if not isinstance(self.replayable, bool):
            raise ValueError("parallel episode trace replayable must be a bool")
        ids = self.manifest.team_ids
        public_initial_observations = _public_observation(self.initial_observations)
        normalized_initial_observations = {
            team_id: _json_value(value)
            for team_id, value in _team_map(public_initial_observations, ids, "initial_observations").items()
        }
        normalized_initial_infos = _team_info_map(self.initial_infos, ids, "initial_infos")
        normalized_terminal_infos = _team_info_map(self.terminal_infos, ids, "terminal_infos")
        normalized_terminal_rewards = _team_map(self.terminal_rewards, ids, "terminal_rewards")
        for team_id, reward in normalized_terminal_rewards.items():
            normalized_terminal_rewards[team_id] = _finite_number(reward, f"terminal_rewards[{team_id!r}]")
        if not self.steps:
            raise ValueError("parallel episode trace must contain at least one step")
        normalized_steps = []
        for index, step in enumerate(self.steps):
            if not isinstance(step, ParallelEpisodeStep):
                raise ValueError(f"parallel replay step {index} must be a ParallelEpisodeStep")
            normalized = ParallelEpisodeStep(
                observations=_team_map(step.observations, ids, f"step {index} observations"),
                actions=None if step.actions is None else _team_map(step.actions, ids, f"step {index} actions"),
                rewards=_team_map(step.rewards, ids, f"step {index} rewards"),
                terminated=_team_map(step.terminated, ids, f"step {index} terminated"),
                truncated=_team_map(step.truncated, ids, f"step {index} truncated"),
                infos=_team_info_map(step.infos, ids, f"step {index} infos"),
                aggregate_metadata=step.aggregate_metadata,
            )
            normalized_steps.append(normalized)
            if self.replayable and normalized.actions is None:
                raise ValueError("replayable parallel trace must contain actions")
            if not self.replayable and normalized.actions is not None:
                raise ValueError("public parallel trace must redact actions")
            if any(normalized.terminated[team] and normalized.truncated[team] for team in ids):
                raise ValueError("parallel episode step cannot be both terminated and truncated")
            if index and all(normalized_steps[index - 1].terminated[t] or normalized_steps[index - 1].truncated[t] for t in ids):
                raise ValueError("parallel episode trace contains steps after terminal")
        if not all(normalized_steps[-1].terminated[t] or normalized_steps[-1].truncated[t] for t in ids):
            raise ValueError("parallel episode trace final step must be terminal for every team")
        object.__setattr__(self, "initial_observations", normalized_initial_observations)
        object.__setattr__(self, "initial_infos", normalized_initial_infos)
        object.__setattr__(self, "steps", tuple(normalized_steps))
        object.__setattr__(self, "terminal_infos", normalized_terminal_infos)
        object.__setattr__(self, "terminal_rewards", normalized_terminal_rewards)

    def to_dict(self) -> dict[str, Any]:
        return _json_value({
            "privacy": "private_replayable" if self.replayable else "public_sanitized",
            "manifest": self.manifest.to_dict(),
            "initial_observations": self.initial_observations,
            "initial_infos": self.initial_infos,
            "steps": [step.to_dict() for step in self.steps],
            "terminal_infos": self.terminal_infos,
            "terminal_rewards": self.terminal_rewards,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ParallelEpisodeTrace":
        value = _mapping(value, "trace")
        _required(value, (
            "privacy",
            "manifest", "initial_observations", "initial_infos", "steps",
            "terminal_infos", "terminal_rewards",
        ), "trace")
        _reject_unknown(value, (
            "privacy", "manifest", "initial_observations", "initial_infos", "steps",
            "terminal_infos", "terminal_rewards",
        ), "trace")
        initial_observations = _mapping(value["initial_observations"], "initial_observations")
        initial_infos = _mapping(value["initial_infos"], "initial_infos")
        terminal_infos = _mapping(value["terminal_infos"], "terminal_infos")
        terminal_rewards = _mapping(value["terminal_rewards"], "terminal_rewards")
        for key, item in terminal_infos.items():
            _mapping(item, f"terminal_infos[{key!r}]")
        for key, item in initial_infos.items():
            _mapping(item, f"initial_infos[{key!r}]")
        for key, item in terminal_rewards.items():
            _finite_number(item, f"terminal_rewards[{key!r}]")
        steps = value["steps"]
        if not isinstance(steps, list):
            raise ValueError("parallel replay steps must be a list")
        privacy = value["privacy"]
        if privacy not in {"public_sanitized", "private_replayable"}:
            raise ValueError("parallel replay privacy must be public_sanitized or private_replayable")
        replayable = privacy == "private_replayable"
        return cls(
            manifest=ParallelEpisodeManifest.from_dict(value["manifest"]),
            initial_observations=dict(initial_observations),
            initial_infos={key: dict(item) for key, item in initial_infos.items()},
            steps=tuple(ParallelEpisodeStep.from_dict(item, allow_redacted=not replayable) for item in steps),
            terminal_infos={key: dict(item) for key, item in terminal_infos.items()},
            terminal_rewards=dict(terminal_rewards),
            replayable=replayable,
        )


def run_parallel_episode(env: Any, policies: Mapping[str, Callable[..., Any]], *, seed: int | None = None, max_steps: int = 10000) -> ParallelEpisodeTrace:
    team_ids = tuple(env.team_ids)
    if tuple(team_ids) != tuple(sorted(team_ids)) or not isinstance(policies, Mapping) or set(policies) != set(team_ids):
        raise ValueError("policies must contain exactly the environment's sorted team IDs")
    observations, infos = env.reset(seed=seed)
    initial_observations = _json_value(_public_observation(observations))
    aggregate = _aggregate_info(infos)
    initial_infos = {team: _public_info(infos[team]) for team in team_ids}
    first_info = infos[team_ids[0]]
    manifest = ParallelEpisodeManifest(
        environment_version=str(first_info.get("environment_version", "unknown")),
        scenario_year=first_info.get("scenario_year"), scenario_block=first_info.get("scenario_block"),
        seed=seed, team_ids=team_ids,
        scenario_manifest_hash=first_info.get("scenario_manifest_hash"),
        rules_version=first_info.get("rules_version"), rules_hash=first_info.get("rules_hash"),
        evaluator_version=first_info.get("evaluator_version"), aggregate_metadata=aggregate,
        **_release_identity(getattr(env, "release_manifest", None)),
    )
    steps: list[ParallelEpisodeStep] = []
    for _ in range(max_steps):
        actions = {team: policies[team](observations[team], infos[team]) for team in team_ids}
        observations, rewards, terminated, truncated, infos = env.step(actions)
        steps.append(ParallelEpisodeStep(
            observations=_json_value(_public_observation(observations)), actions=_json_value(actions),
            rewards={team: float(rewards[team]) for team in team_ids},
            terminated={team: bool(terminated[team]) for team in team_ids},
            truncated={team: bool(truncated[team]) for team in team_ids},
            infos={team: _public_info(infos[team]) for team in team_ids},
            aggregate_metadata=_aggregate_info(infos),
        ))
        if all(terminated[team] or truncated[team] for team in team_ids):
            return ParallelEpisodeTrace(
                manifest, initial_observations, initial_infos, tuple(steps),
                {team: _public_info(infos[team]) for team in team_ids},
                {team: float(rewards[team]) for team in team_ids},
            )
    raise RuntimeError("parallel policies did not terminate within max_steps")


def save_parallel_replay(
    path: str | Path,
    trace: ParallelEpisodeTrace,
    *,
    include_private_actions: bool = False,
) -> Path:
    if not isinstance(trace, ParallelEpisodeTrace):
        raise TypeError("trace must be a ParallelEpisodeTrace")
    if not isinstance(include_private_actions, bool):
        raise TypeError("include_private_actions must be a bool")
    if include_private_actions:
        if not trace.replayable:
            raise ValueError("cannot include private actions from a public trace with redacted actions")
        payload = trace.to_dict()
    else:
        payload = trace.to_dict()
        payload["privacy"] = "public_sanitized"
        for step in payload["steps"]:
            step.pop("actions", None)
    destination = Path(path)
    destination.write_text(json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return destination


def load_parallel_replay(path: str | Path) -> ParallelEpisodeTrace:
    return ParallelEpisodeTrace.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _validate_trace_for_env(env: Any, trace: ParallelEpisodeTrace, tolerance: float) -> None:
    if tolerance < 0 or not math.isfinite(tolerance):
        raise ValueError("tolerance must be finite and non-negative")
    env_ids = tuple(getattr(env, "team_ids", ()))
    if env_ids != trace.manifest.team_ids or env_ids != tuple(sorted(env_ids)):
        raise ValueError("replay mismatch: team IDs or team order")
    target = getattr(env, "release_manifest", None)
    target_digest = None if target is None else target.digest()
    trace_digest = trace.manifest.release_manifest_digest
    if target_digest != trace_digest:
        raise ValueError("replay mismatch: release_manifest")
    if target is not None:
        for field, expected in _release_identity(target).items():
            if getattr(trace.manifest, field) != expected:
                raise ValueError(f"replay mismatch: {field}")
    for step in trace.steps:
        for name in ("actions", "observations", "rewards", "terminated", "truncated", "infos"):
            _team_map(getattr(step, name), env_ids, name)
        for team in env_ids:
            if not math.isfinite(float(step.rewards[team])):
                raise ValueError("replay mismatch: non-finite reward")


def replay_parallel_episode(env: Any, trace: ParallelEpisodeTrace, *, tolerance: float = 1e-7) -> ParallelEpisodeTrace:
    if not trace.replayable:
        raise ValueError("parallel replay actions are redacted in this public artifact; use a private replay artifact")
    _validate_trace_for_env(env, trace, tolerance)
    reset_seed = trace.manifest.seed
    observations, infos = env.reset(seed=reset_seed)
    initial_infos = {team: _public_info(infos[team]) for team in env.team_ids}
    first_info = infos[env.team_ids[0]]
    for field in (
        "environment_version", "scenario_year", "scenario_block", "seed",
        "scenario_manifest_hash", "rules_version", "rules_hash", "evaluator_version",
    ):
        if not _same(getattr(trace.manifest, field), first_info.get(field), tolerance):
            raise ValueError(f"replay mismatch: manifest {field}")
    if not _same(_aggregate_info(infos), trace.manifest.aggregate_metadata, tolerance):
        raise ValueError("replay mismatch: manifest aggregate metadata")
    if not _same(_json_value(_public_observation(observations)), _json_value(_public_observation(trace.initial_observations)), tolerance) or not _same(initial_infos, trace.initial_infos, tolerance):
        raise ValueError("replay mismatch: initial state")
    for index, expected in enumerate(trace.steps):
        _validate_recorded_actions(env, expected.actions, index)
        try:
            observations, rewards, terminated, truncated, infos = env.step(expected.actions)
        except Exception as exc:
            raise ValueError(f"replay mismatch: invalid action at step {index}") from exc
        actual_infos = {team: _public_info(infos[team]) for team in env.team_ids}
        actual = ParallelEpisodeStep(
            observations=_json_value(_public_observation(observations)), actions=_json_value(expected.actions),
            rewards={team: _finite_number(rewards[team], f"rewards[{team!r}]") for team in env.team_ids},
            terminated={team: _boolean(terminated[team], f"terminated[{team!r}]") for team in env.team_ids},
            truncated={team: _boolean(truncated[team], f"truncated[{team!r}]") for team in env.team_ids},
            infos=actual_infos, aggregate_metadata=_aggregate_info(infos),
        )
        if not _same(actual.observations, _json_value(_public_observation(expected.observations)), tolerance):
            raise ValueError(f"replay mismatch: observations at step {index}")
        if not _same(actual.rewards, expected.rewards, tolerance):
            raise ValueError(f"replay mismatch: reward at step {index}")
        if actual.terminated != expected.terminated or actual.truncated != expected.truncated:
            raise ValueError(f"replay mismatch: termination at step {index}")
        if not _same(actual.infos, expected.infos, tolerance) or not _same(actual.aggregate_metadata, expected.aggregate_metadata, tolerance):
            raise ValueError(f"replay mismatch: info at step {index}")
    final_infos = {team: _public_info(infos[team]) for team in env.team_ids}
    final_rewards = {team: float(rewards[team]) for team in env.team_ids}
    if not _same(final_infos, trace.terminal_infos, tolerance) or not _same(final_rewards, trace.terminal_rewards, tolerance):
        raise ValueError("replay mismatch: terminal state")
    return trace


__all__ = [
    "ParallelEpisodeManifest", "ParallelEpisodeStep", "ParallelEpisodeTrace",
    "run_parallel_episode", "save_parallel_replay", "load_parallel_replay",
    "replay_parallel_episode",
]
