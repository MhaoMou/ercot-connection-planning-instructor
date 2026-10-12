"""Small, serializable performance reports for single-agent environments."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

from .baselines import Policy


def benchmark_environment(
    env_factory: Callable[[], Any],
    policy: Policy,
    *,
    episodes: int = 10,
    seed: int | None = None,
) -> dict[str, Any]:
    if episodes <= 0:
        raise ValueError("episodes must be positive")
    reset_times: list[float] = []
    step_times: list[float] = []
    episode_times: list[float] = []
    eval_times: list[float] = []
    action_count = 0
    for episode in range(episodes):
        env = env_factory()
        episode_start = time.perf_counter()
        start = time.perf_counter()
        observation, info = env.reset(seed=None if seed is None else seed + episode)
        reset_times.append(time.perf_counter() - start)
        while True:
            action = policy(observation, info)
            start = time.perf_counter()
            observation, _, terminated, truncated, info = env.step(action)
            elapsed = time.perf_counter() - start
            step_times.append(elapsed)
            action_count += 1
            if info.get("dispatch_status") == "evaluated":
                eval_times.append(elapsed)
            if terminated or truncated:
                break
        episode_times.append(time.perf_counter() - episode_start)

    mean = lambda values: sum(values) / len(values) if values else 0.0
    return {
        "episodes": episodes,
        "action_count": action_count,
        "reset_latency_seconds": mean(reset_times),
        "step_latency_seconds": mean(step_times),
        "episode_latency_seconds": mean(episode_times),
        "eval_latency_seconds": mean(eval_times),
    }


__all__ = ["benchmark_environment"]
