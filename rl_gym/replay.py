"""JSON-safe episode recording and deterministic replay utilities."""

from __future__ import annotations

from dataclasses import dataclass
import copy
import json
import math
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from .baselines import Policy
from .release import ReleaseManifest


def _json_value(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return [_json_value(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")


_PUBLIC_INFO_KEYS = {
    "environment_version",
    "scenario_year",
    "scenario_block",
    "seed",
    "step",
    "valid_action",
    "invalid_action_code",
    "diagnostic",
    "terminated_reason",
    "truncated_reason",
    "score",
    "plan",
    "scenario_manifest_hash",
    "rules_version",
    "rules_hash",
    "evaluator_version",
    "release_version",
    "release_environment_version",
    "release_plan_schema_version",
    "release_manifest_digest",
    "max_capacity_mw",
    "max_sites",
    "max_planning_steps",
}


def _public_info(info: Mapping[str, Any]) -> dict[str, Any]:
    return _json_value({key: info[key] for key in _PUBLIC_INFO_KEYS if key in info})


@dataclass(frozen=True)
class EpisodeManifest:
    environment_version: str
    scenario_year: int | None
    scenario_block: str | None
    seed: int | None
    scenario_manifest_hash: str | None = None
    rules_version: str | None = None
    rules_hash: str | None = None
    evaluator_version: str | None = None
    release_manifest: ReleaseManifest | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_value({
            **{field: getattr(self, field) for field in self.__dataclass_fields__ if field != "release_manifest"},
            "release_manifest": None if self.release_manifest is None else self.release_manifest.to_dict(),
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EpisodeManifest":
        release_manifest = value.get("release_manifest")
        return cls(
            **{
                field: value.get(field)
                for field in cls.__dataclass_fields__
                if field != "release_manifest"
            },
            release_manifest=(
                None if release_manifest is None else ReleaseManifest.from_dict(release_manifest)
            ),
        )


@dataclass(frozen=True)
class EpisodeStep:
    action: Any
    observation: Any
    reward: float
    terminated: bool
    truncated: bool
    info: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", copy.deepcopy(self.action))
        object.__setattr__(self, "observation", copy.deepcopy(self.observation))
        object.__setattr__(self, "info", copy.deepcopy(self.info))

    def to_dict(self) -> dict[str, Any]:
        return _json_value(self.__dict__)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EpisodeStep":
        return cls(
            action=value["action"],
            observation=value["observation"],
            reward=float(value["reward"]),
            terminated=bool(value["terminated"]),
            truncated=bool(value["truncated"]),
            info=dict(value.get("info", {})),
        )


@dataclass(frozen=True)
class EpisodeTrace:
    manifest: EpisodeManifest
    initial_observation: Any
    initial_info: dict[str, Any]
    steps: tuple[EpisodeStep, ...]
    terminal_info: dict[str, Any]
    terminal_score: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "initial_observation", copy.deepcopy(self.initial_observation))
        object.__setattr__(self, "initial_info", copy.deepcopy(self.initial_info))
        object.__setattr__(self, "steps", tuple(copy.deepcopy(step) for step in self.steps))
        object.__setattr__(self, "terminal_info", copy.deepcopy(self.terminal_info))
        if not self.steps:
            raise ValueError("episode trace must contain at least one step")
        if not (self.steps[-1].terminated or self.steps[-1].truncated):
            raise ValueError("episode trace final step must be terminated or truncated")

    def to_dict(self) -> dict[str, Any]:
        return _json_value({
            "manifest": self.manifest.to_dict(),
            "initial_observation": self.initial_observation,
            "initial_info": self.initial_info,
            "steps": [step.to_dict() for step in self.steps],
            "terminal_info": self.terminal_info,
            "terminal_score": self.terminal_score,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EpisodeTrace":
        return cls(
            manifest=EpisodeManifest.from_dict(value["manifest"]),
            initial_observation=value["initial_observation"],
            initial_info=dict(value.get("initial_info", {})),
            steps=tuple(EpisodeStep.from_dict(item) for item in value.get("steps", [])),
            terminal_info=dict(value.get("terminal_info", {})),
            terminal_score=value.get("terminal_score"),
        )


def _same(left: Any, right: Any, tolerance: float) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(_same(left[key], right[key], tolerance) for key in left)
    if isinstance(left, Sequence) and not isinstance(left, (str, bytes)) and isinstance(right, Sequence):
        return len(left) == len(right) and all(_same(a, b, tolerance) for a, b in zip(left, right))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=tolerance)
    return left == right


def _is_legacy_vector_action(action: Any) -> bool:
    if isinstance(action, Mapping) or isinstance(action, (str, bytes)):
        return False
    try:
        values = np.asarray(action)
    except (TypeError, ValueError):
        return False
    return values.shape == (7,) and np.issubdtype(values.dtype, np.number)


def run_episode(env: Any, policy: Policy, *, seed: int | None = None, max_steps: int = 10000) -> EpisodeTrace:
    observation, info = env.reset(seed=seed)
    manifest = EpisodeManifest(
        environment_version=str(info.get("environment_version", "unknown")),
        scenario_year=info.get("scenario_year"),
        scenario_block=info.get("scenario_block"),
        seed=seed,
        scenario_manifest_hash=info.get("scenario_manifest_hash"),
        rules_version=info.get("rules_version"),
        rules_hash=info.get("rules_hash"),
        evaluator_version=info.get("evaluator_version"),
        release_manifest=getattr(env, "release_manifest", None),
    )
    initial_observation = _json_value(observation)
    initial_info = _public_info(info)
    steps: list[EpisodeStep] = []
    for _ in range(max_steps):
        action = _json_value(policy(observation, info))
        observation, reward, terminated, truncated, info = env.step(action)
        steps.append(EpisodeStep(
            action=action,
            observation=_json_value(observation),
            reward=float(reward),
            terminated=bool(terminated),
            truncated=bool(truncated),
            info=_public_info(info),
        ))
        if terminated or truncated:
            terminal_info = _public_info(info)
            return EpisodeTrace(
                manifest, initial_observation, initial_info, tuple(steps),
                terminal_info, terminal_info.get("score"),
            )
    raise RuntimeError("policy did not terminate within max_steps")


def save_replay(path: str | Path, trace: EpisodeTrace) -> Path:
    destination = Path(path)
    destination.write_text(json.dumps(trace.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return destination


def load_replay(path: str | Path) -> EpisodeTrace:
    return EpisodeTrace.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def replay_episode(env: Any, trace: EpisodeTrace, *, tolerance: float = 1e-7) -> EpisodeTrace:
    if tolerance < 0 or not math.isfinite(tolerance):
        raise ValueError("tolerance must be finite and non-negative")
    reset_seed = trace.initial_info.get("seed", trace.manifest.seed)
    observation, info = env.reset(seed=reset_seed)
    target_release_manifest = getattr(env, "release_manifest", None)
    trace_release_manifest = trace.manifest.release_manifest
    target_release_digest = None if target_release_manifest is None else target_release_manifest.digest()
    trace_release_digest = None if trace_release_manifest is None else trace_release_manifest.digest()
    if trace_release_digest != target_release_digest:
        raise ValueError("replay mismatch: release_manifest")
    target_info = _public_info(info)
    manifest_fields = (
        "environment_version",
        "scenario_year",
        "scenario_block",
        "seed",
        "scenario_manifest_hash",
        "rules_version",
        "rules_hash",
        "evaluator_version",
    )
    for field in manifest_fields:
        manifest_value = getattr(trace.manifest, field)
        target_value = target_info.get(field)
        if not _same(manifest_value, target_value, tolerance):
            raise ValueError(f"replay mismatch: manifest {field}")
    if not _same(_json_value(observation), trace.initial_observation, tolerance):
        raise ValueError("replay mismatch: initial observation")
    if not _same(_public_info(info), trace.initial_info, tolerance):
        raise ValueError("replay mismatch: initial info")
    for index, expected in enumerate(trace.steps):
        action = expected.action
        structured_action = env.action_space.contains(action)
        legacy_action = _is_legacy_vector_action(action)
        if not structured_action and not legacy_action:
            raise ValueError(f"replay mismatch: invalid action at step {index}")
        try:
            observation, reward, terminated, truncated, info = env.step(action)
        except Exception as exc:
            if legacy_action:
                raise ValueError(f"replay mismatch: invalid action at step {index}") from exc
            raise
        actual = EpisodeStep(_json_value(action), _json_value(observation), float(reward), bool(terminated), bool(truncated), _public_info(info))
        if not _same(actual.observation, expected.observation, tolerance):
            raise ValueError(f"replay mismatch: observation at step {index}")
        if not _same(actual.reward, expected.reward, tolerance):
            raise ValueError(f"replay mismatch: reward at step {index}")
        if actual.terminated != expected.terminated or actual.truncated != expected.truncated:
            raise ValueError(f"replay mismatch: termination at step {index}")
        if not _same(actual.info, expected.info, tolerance):
            raise ValueError(f"replay mismatch: info at step {index}")
    actual_terminal = _public_info(info)
    actual_score = actual_terminal.get("score")
    if not _same(actual_score, trace.terminal_score, tolerance):
        raise ValueError("replay mismatch: terminal score")
    if not _same(actual_terminal, trace.terminal_info, tolerance):
        raise ValueError("replay mismatch: terminal info")
    return trace


__all__ = [
    "EpisodeManifest", "EpisodeStep", "EpisodeTrace", "run_episode",
    "save_replay", "load_replay", "replay_episode",
]
