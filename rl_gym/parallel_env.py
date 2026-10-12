"""Dependency-light competitive parallel wrapper for Phase 4."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import math
from pathlib import Path
from typing import Any, Callable
import uuid

from ercot_game import batch_evaluation

from .env import ConnectionPlanningEnv
from .release import ReleaseManifest


class ParallelConnectionPlanningEnv:
    """Run independent :class:`ConnectionPlanningEnv` instances jointly.

    The wrapper owns synchronization and joint evaluation.  Child environments
    never receive an evaluator, so no team can be evaluated separately or see
    another team's plan through an observation or info mapping.
    """

    metadata = {"name": "parallel-connection-planning-v1"}

    def __init__(
        self,
        *,
        team_ids: Sequence[str],
        env_kwargs: Mapping[str, Any],
        joint_evaluator: Callable[..., Any] | None = None,
        evaluator_artifact_dir: str | Path | None = None,
        evaluator_rules: Any | None = None,
        submission_dir: str | Path | None = None,
        evaluator_kwargs: Mapping[str, Any] | None = None,
        invalid_action_penalty: float | None = None,
        release_manifest: ReleaseManifest | None = None,
    ) -> None:
        ids = tuple(sorted(team_ids))
        if not ids or any(not isinstance(team_id, str) or not team_id for team_id in ids):
            raise ValueError("team_ids must contain non-empty strings")
        if len(set(ids)) != len(ids):
            raise ValueError("team_ids must be unique")
        if not isinstance(env_kwargs, Mapping):
            raise TypeError("env_kwargs must be a mapping")
        if evaluator_kwargs is not None and not isinstance(evaluator_kwargs, Mapping):
            raise TypeError("evaluator_kwargs must be a mapping")
        self.team_ids = ids
        self.possible_agents = ids
        self.joint_evaluator = joint_evaluator
        self.evaluator_artifact_dir = evaluator_artifact_dir
        self.evaluator_rules = evaluator_rules
        self.submission_dir = Path(submission_dir or ".")
        self.evaluator_kwargs = dict(evaluator_kwargs or {})
        child_kwargs = dict(env_kwargs)
        env_manifest = child_kwargs.pop("release_manifest", None)
        if release_manifest is None:
            release_manifest = env_manifest
        elif env_manifest is not None:
            raise ValueError("release_manifest must be provided either directly or in env_kwargs, not both")
        if release_manifest is not None and not isinstance(release_manifest, ReleaseManifest):
            raise TypeError("release_manifest must be a ReleaseManifest or None")
        if (
            release_manifest is not None
            and "scenario_year" in child_kwargs
            and "scenario_block" in child_kwargs
            and (
                release_manifest.scenario_year != child_kwargs["scenario_year"]
                or release_manifest.scenario_block != child_kwargs["scenario_block"]
            )
        ):
            raise ValueError("release_manifest scenario does not match environment scenario")
        self.release_manifest = (
            None if release_manifest is None else ReleaseManifest.from_dict(release_manifest.to_dict())
        )
        child_kwargs["release_manifest"] = self.release_manifest
        child_kwargs.pop("team_id", None)
        child_kwargs.pop("evaluator_callback", None)
        child_kwargs.pop("evaluator_artifact_dir", None)
        child_kwargs.pop("evaluator_rules", None)
        child_kwargs.pop("submission_dir", None)
        if invalid_action_penalty is not None:
            child_kwargs["invalid_action_penalty"] = float(invalid_action_penalty)
        child_kwargs["invalid_action_policy"] = "penalize"
        self.envs = {
            team_id: ConnectionPlanningEnv(team_id=team_id, **child_kwargs)
            for team_id in ids
        }
        self.action_spaces = {team_id: env.action_space for team_id, env in self.envs.items()}
        self.observation_spaces = {team_id: env.observation_space for team_id, env in self.envs.items()}
        self._observations: dict[str, Any] = {}
        self._done: dict[str, bool] = {}
        self._truncated: dict[str, bool] = {}
        self._eligible: dict[str, bool] = {}
        self._terminal_infos: dict[str, dict[str, Any]] = {}
        self._terminal_rewards: dict[str, float] = {}
        self._evaluated = False
        self._evaluation_attempted = False
        self._evaluation_error = False
        self._projection_applied = False
        self._projected_team_count = 0
        self._unknown_action_count = 0

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        self._observations = {}
        self._done = {team_id: False for team_id in self.team_ids}
        self._truncated = {team_id: False for team_id in self.team_ids}
        self._eligible = {team_id: False for team_id in self.team_ids}
        self._terminal_infos = {team_id: {} for team_id in self.team_ids}
        self._terminal_rewards = {team_id: 0.0 for team_id in self.team_ids}
        self._evaluated = False
        self._evaluation_attempted = False
        self._evaluation_error = False
        self._projection_applied = False
        self._projected_team_count = 0
        self._unknown_action_count = 0
        infos = {}
        for team_id in self.team_ids:
            observation, info = self.envs[team_id].reset(seed=seed, options=options)
            self._observations[team_id] = copy.deepcopy(observation)
            infos[team_id] = self._sanitize_info(info)
        return copy.deepcopy(self._observations), self._with_aggregate(infos)

    def step(self, actions: Mapping[str, Any]):
        if not isinstance(actions, Mapping):
            actions = {}
        rewards: dict[str, float] = {}
        terminated: dict[str, bool] = {}
        truncated: dict[str, bool] = {}
        infos: dict[str, dict[str, Any]] = {}
        self._unknown_action_count = len(set(actions) - set(self.team_ids))

        for team_id in self.team_ids:
            if self._done[team_id] or self._truncated[team_id]:
                rewards[team_id] = self._terminal_rewards[team_id]
                terminated[team_id] = self._done[team_id]
                truncated[team_id] = self._truncated[team_id]
                infos[team_id] = dict(self._terminal_infos[team_id])
                continue
            if team_id not in actions:
                self._isolate_invalid(team_id, "missing_action", "team action is missing")
                rewards[team_id] = self.envs[team_id].invalid_action_penalty
                self._terminal_rewards[team_id] = rewards[team_id]
                terminated[team_id] = True
                truncated[team_id] = False
                infos[team_id] = dict(self._terminal_infos[team_id])
                continue
            observation, reward, is_terminated, is_truncated, info = self.envs[team_id].step(actions[team_id])
            self._observations[team_id] = copy.deepcopy(observation)
            invalid = info.get("valid_action") is False
            if invalid and not (is_terminated or is_truncated):
                self._isolate_invalid(team_id, info.get("invalid_action_code", "invalid_action"), info.get("diagnostic", "invalid team action"))
                is_terminated = True
                reward = self.envs[team_id].invalid_action_penalty
                self._terminal_rewards[team_id] = float(reward)
                info = self._terminal_infos[team_id]
            else:
                self._eligible[team_id] = not invalid
                if is_terminated or is_truncated:
                    self._done[team_id] = bool(is_terminated)
                    self._truncated[team_id] = bool(is_truncated)
                    self._terminal_infos[team_id] = self._sanitize_info(info)
                    self._terminal_rewards[team_id] = float(reward)
            rewards[team_id] = float(reward)
            terminated[team_id] = bool(is_terminated)
            truncated[team_id] = bool(is_truncated)
            infos[team_id] = self._sanitize_info(info)

        if (
            not self._evaluation_attempted
            and all(terminated[team_id] or truncated[team_id] for team_id in self.team_ids)
        ):
            rewards, infos = self._evaluate_joint_set(rewards, infos)
        return copy.deepcopy(self._observations), rewards, terminated, truncated, self._with_aggregate(infos)

    def _isolate_invalid(self, team_id: str, code: str, diagnostic: str) -> None:
        self._done[team_id] = True
        self._eligible[team_id] = False
        self._terminal_infos[team_id] = {
            "valid_action": False,
            "invalid_action_code": code,
            "diagnostic": diagnostic,
            "dispatch_status": "not_evaluated",
            "team_evaluated": False,
        }

    @staticmethod
    def _sanitize_info(info: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in info.items() if key not in {"plan", "team_scores", "projection_diagnostics"}}

    def _evaluate_joint_set(self, rewards, infos):
        self._evaluation_attempted = True
        valid_states = []
        paths = []
        evaluator = self.joint_evaluator
        if evaluator is None and self.evaluator_rules is not None:
            evaluator = batch_evaluation.evaluate_csv_batch
        result = None
        scores = {}
        try:
            for team_id in self.team_ids:
                if not self._eligible[team_id]:
                    continue
                state = self.envs[team_id].current_plan_state()
                if state is None:
                    continue
                valid_states.append((team_id, state))
                if evaluator is None:
                    continue
                self.submission_dir.mkdir(parents=True, exist_ok=True)
                paths.append(state.write_csv(self.submission_dir / f"submission-{team_id}-{uuid.uuid4().hex}.csv"))

            if paths and evaluator is not None:
                kwargs = {
                    "artifact_dir": self.evaluator_artifact_dir or self.submission_dir,
                    "horizon": self.envs[self.team_ids[0]].horizon.horizon,
                    "rules": self.evaluator_rules,
                    **self.evaluator_kwargs,
                }
                result = evaluator(tuple(paths), **kwargs)

            if result is not None:
                projection = getattr(result, "projection_diagnostics", None)
                fractions = getattr(projection, "service_fraction_by_team", None)
                if fractions:
                    self._projection_applied = True
                    self._projected_team_count = sum(float(fraction) < 1.0 for fraction in fractions.values())
                records = getattr(result, "team_scores", result)
                if isinstance(records, Mapping):
                    scores = {team_id: _score_value(record) for team_id, record in records.items()}
                else:
                    scores = {_team_id_value(record): _score_value(record) for record in records}
        except Exception:
            # Normalize solver, artifact, and malformed-result failures into a
            # deterministic terminal outcome without exposing internal details.
            self._evaluation_error = True
            result = None
            scores = {}
        for team_id, _state in valid_states:
            if team_id in scores:
                rewards[team_id] = scores[team_id]
                self._terminal_rewards[team_id] = scores[team_id]
                infos[team_id]["score"] = scores[team_id]
                infos[team_id]["dispatch_status"] = "evaluated"
                infos[team_id]["team_evaluated"] = True
                self._terminal_infos[team_id] = dict(infos[team_id])
            else:
                rewards[team_id] = 0.0
                self._terminal_rewards[team_id] = 0.0
                infos[team_id]["score"] = 0.0
                infos[team_id]["dispatch_status"] = (
                    "evaluation_failed" if self._evaluation_error else "not_evaluated"
                )
                infos[team_id]["team_evaluated"] = False
                self._terminal_infos[team_id] = dict(infos[team_id])
        self._evaluated = result is not None and bool(paths)
        return rewards, infos

    def _with_aggregate(self, infos):
        valid_count = sum(self._eligible.values())
        aggregate = {
            "team_count": len(self.team_ids),
            "finished_team_count": sum(self._done.values()) + sum(self._truncated.values()),
            "valid_team_count": valid_count,
            "evaluated": self._evaluated,
            "joint_evaluated": self._evaluated,
            "evaluation_error": self._evaluation_error,
            "unknown_action_count": self._unknown_action_count,
            "projection_applied": self._projection_applied,
            "projected_team_count": self._projected_team_count,
        }
        return {team_id: {**infos.get(team_id, {}), **aggregate} for team_id in self.team_ids}


def _score_value(record: Any) -> float:
    score = getattr(record, "score", record["score"] if isinstance(record, Mapping) else None)
    if score is None or not math.isfinite(float(score)):
        raise ValueError("evaluator result must contain a finite team score")
    return float(score)


def _team_id_value(record: Any) -> str:
    team_id = getattr(record, "team_id", None)
    if team_id is None and isinstance(record, Mapping):
        team_id = record.get("team_id")
    if not isinstance(team_id, str) or not team_id:
        raise ValueError("evaluator result must contain a team ID")
    return team_id
