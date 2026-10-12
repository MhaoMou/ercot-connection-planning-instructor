"""Run two deterministic private teams in the parallel planning environment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rl_gym import GreedyPolicy, ParallelConnectionPlanningEnv, make_release_manifest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_ROOT = REPOSITORY_ROOT / "data" / "processed" / "activsg2000_scenarios_v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--block", choices=("jan_jun", "jul_dec"), required=True)
    parser.add_argument("--seed", type=int, default=0)
    return parser


def _release(year: int, block: str):
    artifact_dir = SCENARIO_ROOT / str(year) / block
    profile_manifest = json.loads(
        (artifact_dir / "economic_profile_manifest.json").read_text(encoding="utf-8")
    )
    release_manifest = make_release_manifest(
        scenario_year=year,
        scenario_block=block,
        rules={"source": "activsg2000-scenario-package"},
        config={"artifact_sha256": profile_manifest.get("artifact_sha256")},
    )
    return release_manifest, tuple(profile_manifest["bus_ids"])


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    release_manifest, bus_catalog = _release(args.year, args.block)
    env = ParallelConnectionPlanningEnv(
        team_ids=("team-a", "team-b"),
        env_kwargs={
            "bus_catalog": bus_catalog,
            "scenario_year": args.year,
            "scenario_block": args.block,
            "max_sites": 2,
            "max_planning_steps": 3,
            "max_capacity_mw": 100.0,
        },
        release_manifest=release_manifest,
    )
    policies = {team_id: GreedyPolicy() for team_id in env.team_ids}
    observations, infos = env.reset(seed=args.seed)
    final_rewards = {team_id: 0.0 for team_id in env.team_ids}
    final_terminated = {team_id: False for team_id in env.team_ids}
    final_truncated = {team_id: False for team_id in env.team_ids}
    for _ in range(3):
        actions = {
            team_id: policies[team_id](observations[team_id], infos[team_id])
            for team_id in env.team_ids
        }
        observations, rewards, terminated, truncated, infos = env.step(actions)
        final_rewards = rewards
        final_terminated = terminated
        final_truncated = truncated
        if all(terminated[team_id] or truncated[team_id] for team_id in env.team_ids):
            break

    teams = {
        team_id: {
            "reward": float(final_rewards[team_id]),
            "status": infos[team_id].get("dispatch_status", "not_evaluated"),
        }
        for team_id in env.team_ids
    }
    aggregate_info = infos[env.team_ids[0]]
    summary = {
        "scenario_year": args.year,
        "scenario_block": args.block,
        "seed": args.seed,
        "teams": teams,
        "aggregate": {
            "team_count": int(aggregate_info["team_count"]),
            "finished_team_count": int(aggregate_info["finished_team_count"]),
            "valid_team_count": int(aggregate_info["valid_team_count"]),
            "evaluated": bool(aggregate_info["evaluated"]),
            "terminated_team_count": sum(final_terminated.values()),
            "truncated_team_count": sum(final_truncated.values()),
        },
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
