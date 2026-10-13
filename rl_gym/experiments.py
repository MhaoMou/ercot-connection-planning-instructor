"""Deterministic, privacy-safe experiment runs for the released RL surface."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from .baselines import GreedyPolicy, RandomPolicy, RuleBasedPolicy
from .parallel_env import ParallelConnectionPlanningEnv
from .release import canonical_json, make_release_manifest


_POLICY_NAMES = ("greedy", "random", "rule_based")
_BLOCKS = ("jan_jun", "jul_dec")
_DEFAULT_SCENARIO_ROOT = (
    Path(__file__).resolve().parents[1] / "data" / "processed" / "activsg2000_scenarios_v1"
)


@dataclass(frozen=True)
class ExperimentConfig:
    """JSON-safe inputs for a deterministic collection of RL episodes."""

    policy_name: str
    scenario_years: tuple[int, ...]
    blocks: tuple[str, ...]
    team_count: int
    episode_count: int
    seed: int
    evaluator_mode: str = "none"

    def __post_init__(self) -> None:
        if self.policy_name not in _POLICY_NAMES:
            raise ValueError(f"policy_name must be one of {_POLICY_NAMES}")
        years = _normalize_years(self.scenario_years)
        blocks = _normalize_blocks(self.blocks)
        object.__setattr__(self, "scenario_years", years)
        object.__setattr__(self, "blocks", blocks)
        if isinstance(self.team_count, bool) or not isinstance(self.team_count, int) or self.team_count <= 0:
            raise ValueError("team_count must be a positive integer")
        if isinstance(self.episode_count, bool) or not isinstance(self.episode_count, int) or self.episode_count <= 0:
            raise ValueError("episode_count must be a positive integer")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        if self.evaluator_mode not in {"none", "terminal"}:
            raise ValueError("evaluator_mode must be 'none' or 'terminal'")

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "scenario_years": list(self.scenario_years),
            "blocks": list(self.blocks),
            "team_count": self.team_count,
            "episode_count": self.episode_count,
            "seed": self.seed,
            "evaluator_mode": self.evaluator_mode,
        }


def run_experiments(
    config: ExperimentConfig,
    *,
    evaluator_callback: Callable[..., Any] | None = None,
    scenario_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Run deterministic baseline episodes and return public JSON records.

    Every record's ``timing`` mapping contains the complete public execution
    metric set: integer ``steps``, ``policy_calls``, and ``action_count``
    counts, plus monotonic elapsed ``reset_latency_seconds``,
    ``step_latency_seconds``, and ``episode_latency_seconds`` values.

    The callback is deliberately an application-level argument. It is never
    stored in :class:`ExperimentConfig` or included in returned records.
    """
    if not isinstance(config, ExperimentConfig):
        raise TypeError("config must be an ExperimentConfig")
    if config.evaluator_mode == "terminal" and evaluator_callback is None:
        raise ValueError("evaluator_callback is required for evaluator_mode='terminal'")
    if config.evaluator_mode == "none" and evaluator_callback is not None:
        raise ValueError("evaluator_callback requires evaluator_mode='terminal'")

    root = Path(scenario_root) if scenario_root is not None else _DEFAULT_SCENARIO_ROOT
    records: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="rl-gym-experiments-") as temporary_directory:
        for scenario_index, (year, block) in enumerate(
            ( (year, block) for year in config.scenario_years for block in config.blocks )
        ):
            manifest, bus_catalog = _scenario_release(root, year, block)
            env_kwargs = {
                "bus_catalog": bus_catalog,
                "scenario_year": year,
                "scenario_block": block,
                "max_sites": 2,
                "max_planning_steps": 3,
                "max_capacity_mw": 100.0,
            }
            env = ParallelConnectionPlanningEnv(
                team_ids=tuple(f"team-{index}" for index in range(1, config.team_count + 1)),
                env_kwargs=env_kwargs,
                joint_evaluator=evaluator_callback if config.evaluator_mode == "terminal" else None,
                evaluator_artifact_dir=temporary_directory,
                submission_dir=temporary_directory,
                release_manifest=manifest,
            )
            for episode_index in range(config.episode_count):
                episode_seed = _episode_seed(config.seed, scenario_index, episode_index)
                episode_started = perf_counter()
                reset_started = perf_counter()
                observations, infos = env.reset(seed=episode_seed)
                reset_latency_seconds = perf_counter() - reset_started
                policies = _policies(config.policy_name, env.team_ids, episode_seed)
                final_rewards = {team_id: 0.0 for team_id in env.team_ids}
                final_terminated = {team_id: False for team_id in env.team_ids}
                final_truncated = {team_id: False for team_id in env.team_ids}
                final_infos = infos
                step_count = 0
                action_count = 0
                step_latency_seconds = 0.0
                while not all(final_terminated[team_id] or final_truncated[team_id] for team_id in env.team_ids):
                    actions = {
                        team_id: policies[team_id](observations[team_id], infos[team_id])
                        for team_id in env.team_ids
                    }
                    action_count += len(actions)
                    step_started = perf_counter()
                    observations, final_rewards, final_terminated, final_truncated, final_infos = env.step(actions)
                    step_latency_seconds += perf_counter() - step_started
                    infos = final_infos
                    step_count += 1
                    if step_count > env.envs[env.team_ids[0]].max_planning_steps + 1:
                        raise RuntimeError("experiment policy did not terminate deterministically")

                episode_latency_seconds = perf_counter() - episode_started
                aggregate = _aggregate(final_infos[env.team_ids[0]])
                for team_id in env.team_ids:
                    info = final_infos[team_id]
                    records.append({
                        "policy": config.policy_name,
                        "scenario_year": year,
                        "scenario_block": block,
                        "episode_index": episode_index,
                        "episode_seed": episode_seed,
                        "team_id": team_id,
                        "team_count": config.team_count,
                        "release_manifest_digest": manifest.digest(),
                        "timing": {
                            "steps": int(step_count),
                            "policy_calls": int(step_count),
                            "action_count": int(action_count),
                            "reset_latency_seconds": float(reset_latency_seconds),
                            "step_latency_seconds": float(step_latency_seconds),
                            "episode_latency_seconds": float(episode_latency_seconds),
                        },
                        "reward": float(final_rewards[team_id]),
                        "status": str(info.get("dispatch_status", "not_evaluated")),
                        "terminated": bool(final_terminated[team_id]),
                        "truncated": bool(final_truncated[team_id]),
                        "aggregate": aggregate,
                    })
    json.loads(canonical_json(records))
    return records


def _normalize_years(years: Sequence[int]) -> tuple[int, ...]:
    if isinstance(years, (str, bytes)):
        raise ValueError("scenario_years must be a non-empty sequence of integers")
    result = tuple(years)
    if not result or any(isinstance(year, bool) or not isinstance(year, int) for year in result):
        raise ValueError("scenario_years must be a non-empty sequence of integers")
    return result


def _normalize_blocks(blocks: Sequence[str]) -> tuple[str, ...]:
    if isinstance(blocks, (str, bytes)):
        raise ValueError("blocks must be a non-empty sequence")
    result = tuple(blocks)
    if not result or any(block not in _BLOCKS for block in result):
        raise ValueError("blocks must contain only 'jan_jun' or 'jul_dec'")
    return result


def _episode_seed(seed: int, scenario_index: int, episode_index: int) -> int:
    return int(seed + scenario_index * 1_000_003 + episode_index)


def _policies(policy_name: str, team_ids: Sequence[str], seed: int) -> dict[str, Any]:
    if policy_name == "greedy":
        return {team_id: GreedyPolicy() for team_id in team_ids}
    if policy_name == "rule_based":
        return {team_id: RuleBasedPolicy() for team_id in team_ids}
    return {
        team_id: RandomPolicy(seed=seed + index)
        for index, team_id in enumerate(team_ids)
    }


def _scenario_release(root: Path, year: int, block: str):
    artifact_dir = root / str(year) / block
    manifest_path = artifact_dir / "economic_profile_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"scenario artifact manifest not found: {manifest_path}")
    profile_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bus_catalog = tuple(profile_manifest.get("bus_ids", ()))
    if not bus_catalog:
        raise ValueError(f"scenario manifest has no public bus IDs: {manifest_path}")
    release_manifest = make_release_manifest(
        scenario_year=year,
        scenario_block=block,
        rules={"source": "activsg2000-scenario-package"},
        config={"artifact_sha256": profile_manifest.get("artifact_sha256")},
    )
    return release_manifest, bus_catalog


def _aggregate(info: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "team_count", "finished_team_count", "valid_team_count", "evaluated",
        "joint_evaluated", "evaluation_error", "unknown_action_count",
        "projection_applied", "projected_team_count",
    )
    return {key: info[key] for key in keys if key in info}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=_POLICY_NAMES, required=True)
    parser.add_argument("--years", nargs="+", type=int, required=True)
    parser.add_argument("--blocks", nargs="+", choices=_BLOCKS, required=True)
    parser.add_argument("--team-count", type=int, default=1)
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--evaluator-mode",
        choices=("none",),
        default="none",
        help="solver-free CLI mode; terminal evaluation is available through the Python API only",
    )
    parser.add_argument("--scenario-root", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = ExperimentConfig(
        policy_name=args.policy,
        scenario_years=tuple(args.years),
        blocks=tuple(args.blocks),
        team_count=args.team_count,
        episode_count=args.episodes,
        seed=args.seed,
        evaluator_mode=args.evaluator_mode,
    )
    print(json.dumps(run_experiments(config, scenario_root=args.scenario_root), sort_keys=True, separators=(",", ":")))
    return 0


__all__ = ["ExperimentConfig", "run_experiments", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
