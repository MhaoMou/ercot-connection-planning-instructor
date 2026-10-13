"""Dependency-light deterministic policy-loop training utilities."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Any

from ercot_game.calendars import half_year_calendar

from .baselines import GreedyPolicy, Policy
from .release import ReleaseManifest, canonical_json, make_release_manifest


EnvFactory = Callable[..., Any]
LearnerCallback = Callable[[Mapping[str, Any], Mapping[str, Any]], Any]


@dataclass(frozen=True)
class TrainingConfig:
    """Validated configuration for a deterministic policy-loop run."""

    episodes: int = 1
    seed: int | None = 0
    year: int = 2029
    block: str = "jan_jun"
    max_steps: int = 10_000

    def __post_init__(self) -> None:
        if isinstance(self.episodes, bool) or not isinstance(self.episodes, int) or self.episodes <= 0:
            raise ValueError("episodes must be a positive integer")
        if self.seed is not None and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise ValueError("seed must be an integer or None")
        if isinstance(self.year, bool) or not isinstance(self.year, int):
            raise ValueError("year must be an integer")
        try:
            half_year_calendar(self.year, self.block)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid year/block configuration: {exc}") from exc
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int) or self.max_steps <= 0:
            raise ValueError("max_steps must be a positive integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "episodes": self.episodes,
            "seed": self.seed,
            "year": self.year,
            "block": self.block,
        }


def _json_safe(value: Any) -> Any:
    """Validate and detach a value using the release JSON contract."""
    return json.loads(canonical_json(value))


def _episode_seed(seed: int | None, episode_index: int) -> int | None:
    return None if seed is None else seed + episode_index


def _make_environment(factory: EnvFactory, config: TrainingConfig) -> Any:
    try:
        return factory(year=config.year, block=config.block)
    except TypeError as keyword_error:
        try:
            return factory()
        except TypeError:
            raise keyword_error


def _manifest_for(env: Any, release_manifest: ReleaseManifest | None) -> dict[str, Any] | None:
    manifest = release_manifest or getattr(env, "release_manifest", None)
    if manifest is None:
        manifest = make_release_manifest(
            scenario_year=env.scenario_year,
            scenario_block=env.scenario_block,
            rules={"source": "rl_gym.training"},
            config={"training": "policy_loop"},
        )
    if not isinstance(manifest, ReleaseManifest):
        raise TypeError("release_manifest must be a ReleaseManifest or None")
    if manifest.scenario_year != env.scenario_year or manifest.scenario_block != env.scenario_block:
        raise ValueError("release_manifest scenario does not match the environment")
    return manifest.to_dict()


def train_policy(
    env_factory: EnvFactory,
    *,
    episodes: int = 1,
    seed: int | None = 0,
    year: int = 2029,
    block: str = "jan_jun",
    policy: Policy | None = None,
    learner_callback: LearnerCallback | None = None,
    release_manifest: ReleaseManifest | None = None,
    max_steps: int = 10_000,
) -> dict[str, Any]:
    """Run deterministic policy episodes and return public JSON metadata.

    ``learner_callback`` is an in-memory action-selection seam. It receives
    only the environment's observation and info mappings and is never placed
    in the returned result.
    """
    config = TrainingConfig(episodes=episodes, seed=seed, year=year, block=block, max_steps=max_steps)
    if policy is not None and learner_callback is not None:
        raise ValueError("policy and learner_callback are mutually exclusive")
    selected_policy: Callable[[Mapping[str, Any], Mapping[str, Any]], Any] = (
        learner_callback or policy or GreedyPolicy()
    )
    if not callable(selected_policy):
        raise TypeError("policy must be callable")

    summaries: list[dict[str, Any]] = []
    manifest_data: dict[str, Any] | None = None
    for episode_index in range(config.episodes):
        env = _make_environment(env_factory, config)
        episode_seed = _episode_seed(config.seed, episode_index)
        observation, info = env.reset(seed=episode_seed)
        if manifest_data is None:
            manifest_data = _manifest_for(env, release_manifest)
        elif _manifest_for(env, release_manifest) != manifest_data:
            raise ValueError("environment release manifest changed during training")

        total_reward = 0.0
        steps = 0
        terminated = False
        truncated = False
        terminal_info: Mapping[str, Any] = {}
        for _ in range(config.max_steps):
            action = selected_policy(observation, info)
            observation, reward, terminated, truncated, info = env.step(action)
            total_reward += float(reward)
            steps += 1
            terminal_info = info
            if terminated or truncated:
                break
        else:
            raise RuntimeError("policy did not terminate within max_steps")

        summary: dict[str, Any] = {
            "episode": episode_index,
            "seed": episode_seed,
            "steps": steps,
            "reward": total_reward,
            "terminated": bool(terminated),
            "truncated": bool(truncated),
        }
        for key in ("dispatch_status", "score", "terminated_reason", "truncated_reason"):
            if key in terminal_info and terminal_info[key] is not None:
                summary[key] = terminal_info[key]
        summaries.append(_json_safe(summary))

    result = {
        "config": config.to_dict(),
        "release_manifest": manifest_data,
        "episodes": summaries,
        "episode_count": len(summaries),
        "total_reward": sum(item["reward"] for item in summaries),
        "total_steps": sum(item["steps"] for item in summaries),
    }
    return _json_safe(result)


__all__ = ["EnvFactory", "LearnerCallback", "TrainingConfig", "train_policy"]
