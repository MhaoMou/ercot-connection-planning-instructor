"""Run the exact eight-team instructor evaluator in a portable Colab bundle."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", type=int, default=4368)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--solver-backend",
        choices=("auto", "highs", "gurobi"),
        default="auto",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("results/full_4368h"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.horizon <= 0:
        raise SystemExit("--horizon must be positive")
    if args.workers <= 0:
        raise SystemExit("--workers must be positive")

    root = Path(__file__).resolve().parent
    submissions = root / "example_round" / "submissions"
    artifact = root / "scenario" / "2028" / "jan_jun"
    rules = root / "rules" / "dispatch_scoring_rules_jan_jun_v1.json"
    output_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    period_indices = ",".join(str(index) for index in range(args.horizon))
    command = [
        sys.executable,
        "-m",
        "ercot_game.batch_cli",
        "run",
        "--submissions-dir",
        str(submissions),
        "--artifact-dir",
        str(artifact),
        "--rules",
        str(rules),
        "--horizon",
        str(args.horizon),
        "--period-indices",
        period_indices,
        "--workers",
        str(args.workers),
        "--solver-backend",
        args.solver_backend,
        "--output-mode",
        "compact",
        "--submission-number",
        "2",
        "--output-dir",
        str(output_dir),
    ]
    environment = {**os.environ, "PYTHONPATH": str(root / "src")}

    print("Running exact instructor evaluator:")
    print(" ".join(command))
    print(f"Python: {sys.version.split()[0]} | platform: {platform.platform()}")
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=root, env=environment, check=False)
    elapsed = time.perf_counter() - started

    evaluation_path = output_dir / "evaluation.json"
    summary: dict[str, object] = {
        "return_code": completed.returncode,
        "wall_seconds": round(elapsed, 3),
        "requested_solver_backend": args.solver_backend,
        "horizon_hours": args.horizon,
        "workers": args.workers,
        "output_dir": str(output_dir),
    }
    if evaluation_path.is_file():
        payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
        metadata = payload.get("evaluation_metadata", {})
        competition_scope = payload.get("competition_scope")
        projection_diagnostics = metadata.get("projection_diagnostics")
        summary.update(
            {
                "resolved_solver_backend": metadata.get("solver_diagnostics", {}).get(
                    "solver_backend"
                ),
                "accepted_submission_count": len(payload.get("team_scores", [])),
                "competition_scope": competition_scope,
                "joint_feasible": competition_scope in {"joint", "joint_projection"},
                "projection_applied": projection_diagnostics is not None,
                "team_scores": payload.get("team_scores", []),
                "evaluation_json_bytes": evaluation_path.stat().st_size,
            }
        )
    summary_path = output_dir / "colab_timing_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
