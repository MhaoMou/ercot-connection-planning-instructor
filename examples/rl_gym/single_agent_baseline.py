"""Run the deterministic single-agent greedy RL baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rl_gym import ConnectionPlanningEnv, GreedyPolicy, make_release_manifest, run_episode, save_replay


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_ROOT = REPOSITORY_ROOT / "data" / "processed" / "activsg2000_scenarios_v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--block", choices=("jan_jun", "jul_dec"), required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--replay-out", type=Path)
    return parser


def _release(year: int, block: str):
    artifact_dir = SCENARIO_ROOT / str(year) / block
    profile_manifest = json.loads(
        (artifact_dir / "economic_profile_manifest.json").read_text(encoding="utf-8")
    )
    return make_release_manifest(
        scenario_year=year,
        scenario_block=block,
        rules={"source": "activsg2000-scenario-package"},
        config={"artifact_sha256": profile_manifest.get("artifact_sha256")},
    ), tuple(profile_manifest["bus_ids"])


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    release_manifest, bus_catalog = _release(args.year, args.block)
    env = ConnectionPlanningEnv(
        team_id="team-a",
        bus_catalog=bus_catalog,
        scenario_year=args.year,
        scenario_block=args.block,
        max_sites=2,
        max_planning_steps=3,
        max_capacity_mw=100.0,
        release_manifest=release_manifest,
    )
    trace = run_episode(env, GreedyPolicy(), seed=args.seed)
    replay_out: str | None = None
    if args.replay_out is not None:
        args.replay_out.parent.mkdir(parents=True, exist_ok=True)
        replay_out = str(save_replay(args.replay_out, trace))
    terminal_info: dict[str, Any] = trace.terminal_info
    summary = {
        "scenario_year": args.year,
        "scenario_block": args.block,
        "seed": args.seed,
        "steps": len(trace.steps),
        "reward": float(trace.steps[-1].reward),
        "status": terminal_info.get("dispatch_status", "not_evaluated"),
        "replay_out": replay_out,
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
