"""Run staged hosting-capacity measurements and write JSON Lines results."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import statistics
import sys
import time

from ercot_game.hosting_capacity_cli import (
    artifact_horizon,
    ieee14_provider,
)
from tools.hosting_capacity import compute_hosting_capacity_matrix, prepared_artifact_period_provider

try:
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None


@dataclass(frozen=True)
class BenchmarkStage:
    name: str
    period_count: int
    full_horizon: bool = False


def stage_specs(*, has_artifact: bool, full_horizon: bool) -> tuple[BenchmarkStage, ...]:
    if not has_artifact:
        return (BenchmarkStage("ieee14-1", 1),)
    stages = (
        BenchmarkStage("activsg2000-1", 1),
        BenchmarkStage("activsg2000-24", 24),
        BenchmarkStage("activsg2000-168", 168),
    )
    if full_horizon:
        stages += (BenchmarkStage("activsg2000-full", 0, full_horizon=True),)
    return stages


def _positive_int(label: str):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{label} must be an integer") from None
        if parsed < 1:
            raise argparse.ArgumentTypeError(f"{label} must be at least 1")
        return parsed

    return parse


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--artifact-dir", type=Path)
    source.add_argument("--ieee14", action="store_true")
    parser.add_argument("--period-count", type=_positive_int("period-count"), default=1)
    parser.add_argument("--solver-backend", choices=("auto", "highs", "gurobi"), default="auto")
    parser.add_argument("--solver-threads", type=_positive_int("solver-threads"), default=1)
    parser.add_argument("--workers", type=_positive_int("workers"), default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--full-horizon", action="store_true")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def _peak_rss_mb() -> float | None:
    if resource is None:
        return None
    try:
        usages = [resource.getrusage(resource.RUSAGE_SELF)]
        if hasattr(resource, "RUSAGE_CHILDREN"):
            usages.append(resource.getrusage(resource.RUSAGE_CHILDREN))
        raw = max(int(usage.ru_maxrss) for usage in usages)
    except (AttributeError, OSError, ValueError):
        return None
    if raw <= 0:
        return None
    # macOS reports bytes; Linux and most other Unix platforms report KiB.
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(raw / divisor, 3)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return float(statistics.quantiles(values, n=100, method="inclusive")[int(percentile) - 1])


def _run_stage(
    *,
    stage: BenchmarkStage,
    provider,
    horizon: int,
    output_root: Path,
    backend: str,
    workers: int,
    solver_threads: int,
) -> dict[str, object]:
    period_count = horizon if stage.full_horizon else stage.period_count
    if period_count > horizon:
        raise ValueError(f"stage {stage.name} requires {period_count} periods, artifact has {horizon}")
    stage_dir = output_root / stage.name
    checkpoint_dir = stage_dir / "checkpoints"
    stage_dir.mkdir(parents=True, exist_ok=True)
    model_started = time.perf_counter()
    provider(0)
    model_build_time_s = time.perf_counter() - model_started
    started = time.perf_counter()
    result = compute_hosting_capacity_matrix(
        provider,
        period_indices=tuple(range(period_count)),
        solver_backend=backend,
        solver_threads=solver_threads,
        workers=workers,
        checkpoint_dir=checkpoint_dir,
        output_dir=stage_dir,
    )
    wall_time_s = time.perf_counter() - started
    point_times = [float(value) for value in result.solve_time_s.flat if value == value]
    statuses = [str(value) for value in result.status.flat]
    completed = sum(status != "" for status in statuses)
    failed = sum(status not in {"", "optimal"} for status in statuses)
    return {
        "stage": stage.name,
        "backend": result.metadata.get("resolved_solver_backend", backend),
        "workers": workers,
        "solver_threads": solver_threads,
        "wall_time_s": round(wall_time_s, 6),
        "peak_rss_mb": _peak_rss_mb(),
        "model_build_time_s": round(model_build_time_s, 6),
        "mean_point_time_s": round(statistics.fmean(point_times), 6) if point_times else None,
        "p50_point_time_s": round(_percentile(point_times, 50), 6) if point_times else None,
        "p95_point_time_s": round(_percentile(point_times, 95), 6) if point_times else None,
        "completed_points": completed,
        "failed_points": failed,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.full_horizon and args.ieee14:
        print("error: --full-horizon requires --artifact-dir", file=sys.stderr)
        return 2
    output_root = args.output_dir.resolve()
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        if args.ieee14:
            provider = ieee14_provider()
            horizon = 1
            if args.period_count != 1:
                raise ValueError("IEEE-14 benchmark has exactly one period")
        else:
            artifact_dir = args.artifact_dir.resolve()
            if not artifact_dir.is_dir():
                raise ValueError(f"artifact directory does not exist or is not a directory: {artifact_dir}")
            provider = prepared_artifact_period_provider(artifact_dir)
            horizon = artifact_horizon(artifact_dir)
        stages = stage_specs(has_artifact=not args.ieee14, full_horizon=args.full_horizon)
        results = [
            _run_stage(
                stage=stage,
                provider=provider,
                horizon=horizon,
                output_root=output_root,
                backend=args.solver_backend,
                workers=args.workers,
                solver_threads=args.solver_threads,
            )
            for stage in stages
        ]
        result_path = output_root / "benchmark_hosting_capacity.jsonl"
        result_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in results),
            encoding="utf-8",
        )
        return 1 if any(row["failed_points"] for row in results) else 0
    except KeyboardInterrupt:
        print("benchmark interrupted; completed stage outputs and checkpoints were preserved", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
