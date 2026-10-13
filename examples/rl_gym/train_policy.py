"""Run deterministic dependency-light policy-loop training."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Make the checked-out example runnable without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from rl_gym import ConnectionPlanningEnv, make_release_manifest, train_policy


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_ROOT = REPOSITORY_ROOT / "data" / "processed" / "activsg2000_scenarios_v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--year", type=int, default=2029)
    parser.add_argument("--block", choices=("jan_jun", "jul_dec"), default="jan_jun")
    parser.add_argument("--output", type=Path)
    return parser


def _environment_factory(year: int, block: str):
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
    bus_catalog = tuple(profile_manifest["bus_ids"])

    def factory(*, year: int, block: str):
        return ConnectionPlanningEnv(
            team_id="team-a",
            bus_catalog=bus_catalog,
            scenario_year=year,
            scenario_block=block,
            max_sites=2,
            max_planning_steps=3,
            max_capacity_mw=100.0,
            release_manifest=release_manifest,
        )

    return factory


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = train_policy(
        _environment_factory(args.year, args.block),
        episodes=args.episodes,
        seed=args.seed,
        year=args.year,
        block=args.block,
    )
    rendered = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
