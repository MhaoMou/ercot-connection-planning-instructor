"""Benchmark exact instructor-evaluator configurations.

This script is intentionally a subprocess benchmark: each configuration gets
an isolated Python process and solver state.  It records wall time and child
RSS, while the evaluator's compact output records exact scores and Benders
diagnostics.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


def _parse_ints(raw: str) -> tuple[int, ...]:
    values = tuple(int(item) for item in raw.split(",") if item.strip())
    if not values or any(value <= 0 for value in values):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--submissions-dir", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--horizons", type=_parse_ints, default=(24, 168, 720))
    parser.add_argument("--workers", type=_parse_ints, default=(2, 4, 6, 8))
    parser.add_argument("--solver-backends", default="highs")
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    root = args.repo_root.resolve()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for backend in tuple(item.strip() for item in args.solver_backends.split(",") if item.strip()):
        if backend not in {"auto", "highs", "gurobi"}:
            raise ValueError(f"unsupported backend: {backend}")
        for horizon in args.horizons:
            for workers in args.workers:
                output_dir = output_root / f"{backend}_h{horizon}_w{workers}"
                command = [
                    sys.executable,
                    "-m",
                    "ercot_game.batch_cli",
                    "run",
                    "--submissions-dir",
                    str(args.submissions_dir),
                    "--artifact-dir",
                    str(args.artifact_dir),
                    "--rules",
                    str(args.rules),
                    "--horizon",
                    "4368",
                    "--period-indices",
                    ",".join(str(index) for index in range(horizon)),
                    "--workers",
                    str(workers),
                    "--solver-backend",
                    backend,
                    "--output-mode",
                    "compact",
                    "--output-dir",
                    str(output_dir),
                ]
                started = time.perf_counter()
                completed = subprocess.run(
                    command,
                    cwd=root,
                    env={**os.environ, "PYTHONPATH": str(root / "src")},
                    text=True,
                    capture_output=True,
                )
                elapsed = time.perf_counter() - started
                max_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
                row: dict[str, object] = {
                    "backend": backend,
                    "horizon": horizon,
                    "workers": workers,
                    "returncode": completed.returncode,
                    "wall_seconds": round(elapsed, 3),
                    "child_maxrss_kb": max_rss,
                    "output_dir": str(output_dir),
                    "stdout": completed.stdout[-2000:],
                    "stderr": completed.stderr[-2000:],
                }
                evaluation_path = output_dir / "evaluation.json"
                if evaluation_path.is_file():
                    payload = json.loads(evaluation_path.read_text(encoding="utf-8"))
                    row["team_scores"] = payload.get("team_scores", [])
                    row["solver_diagnostics"] = payload.get("evaluation_metadata", {}).get("solver_diagnostics")
                rows.append(row)

    (output_root / "benchmark.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (output_root / "benchmark.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("backend", "horizon", "workers", "returncode", "wall_seconds", "child_maxrss_kb", "output_dir"),
        )
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in writer.fieldnames} for row in rows)
    return 0 if all(row["returncode"] == 0 for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
