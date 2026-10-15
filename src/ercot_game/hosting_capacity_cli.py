"""Command-line entry point for exact hosting-capacity matrices."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path
import sys
import time
from collections.abc import Sequence

import numpy as np

from tools.activsg2000.dcopf import Branch, Bus, Generator
from tools.hosting_capacity import (
    HOSTING_CAPACITY_STATUSES,
    HostingCapacityMatrix,
    HostingCapacityPeriod,
    compute_hosting_capacity_matrix,
    ieee14_period_provider,
    prepared_artifact_period_provider,
    validate_period,
)


def _at_least_one(label: str):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{label} must be an integer") from None
        if parsed < 1:
            raise argparse.ArgumentTypeError(f"{label} must be at least 1")
        return parsed

    return parse


def _nonnegative(label: str):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{label} must be an integer") from None
        if parsed < 0:
            raise argparse.ArgumentTypeError(f"{label} must be non-negative")
        return parsed

    return parse


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute exact one-bus hosting capacity for each requested period."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--artifact-dir", type=Path, help="validated ACTIVSg2000 block")
    source.add_argument("--ieee14", action="store_true", help="use the bundled IEEE-14 smoke case")
    parser.add_argument("--horizon", type=_at_least_one("horizon"), help="available period count")
    parser.add_argument("--start-period", type=_nonnegative("start-period"), default=0)
    parser.add_argument("--period-count", type=_at_least_one("period-count"))
    parser.add_argument("--bus-id", action="append", dest="bus_ids", help="candidate bus (repeatable)")
    parser.add_argument("--solver-backend", choices=("auto", "highs", "gurobi"), default="auto")
    parser.add_argument("--solver-threads", type=_at_least_one("solver-threads"), default=1)
    parser.add_argument("--workers", type=_at_least_one("workers"), default=1)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--write-long-form", action="store_true")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments without loading a solver backend."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.workers > 1 and args.solver_threads > 1:
        parser.error("workers > 1 cannot be combined with solver-threads > 1")
    return args


def _ieee14_period() -> HostingCapacityPeriod:
    """Return the deterministic IEEE-14 DCOPF smoke case.

    The case uses the canonical IEEE-14 demands, topology, and generator
    locations.  Thermal limits are intentionally generous for a smoke test;
    callers needing a dataset-specific case should use ``--artifact-dir``.
    """
    demand = (0.0, 21.7, 94.2, 47.8, 7.6, 11.2, 0.0, 0.0, 29.5, 9.0, 3.5, 6.1, 13.5, 14.9)
    buses = tuple(
        Bus(str(index), value, reference=index == 1)
        for index, value in enumerate(demand, start=1)
    )
    generators = (
        Generator("g1", "1", 0.0, 332.4, 10.0),
        Generator("g2", "2", 0.0, 140.0, 20.0),
        Generator("g3", "3", 0.0, 100.0, 30.0),
        Generator("g6", "6", 0.0, 100.0, 40.0),
        Generator("g8", "8", 0.0, 100.0, 50.0),
    )
    lines = (
        (1, 2, 0.05917), (1, 5, 0.22304), (2, 3, 0.19797), (2, 4, 0.17632),
        (2, 5, 0.17388), (3, 4, 0.17103), (4, 5, 0.04211), (4, 7, 0.20912),
        (4, 9, 0.55618), (5, 6, 0.25202), (6, 11, 0.19890), (6, 12, 0.25581),
        (6, 13, 0.13027), (7, 8, 0.17615), (7, 9, 0.11001), (9, 10, 0.08450),
        (9, 14, 0.27038), (10, 11, 0.19207), (12, 13, 0.19988), (13, 14, 0.34802),
    )
    branches = tuple(
        Branch(f"br{index}", str(source), str(target), 1.0 / reactance, 1000.0)
        for index, (source, target, reactance) in enumerate(lines, start=1)
    )
    return validate_period(
        HostingCapacityPeriod(
            period_index=0,
            buses=buses,
            generators=generators,
            branches=branches,
            fixed_injection_mw_by_bus={},
            source_metadata={"source": "ieee14", "dataset": "IEEE-14"},
        )
    )


def ieee14_provider():
    """Build the pickleable one-period IEEE-14 provider used by the CLI."""
    return ieee14_period_provider((_ieee14_period(),))


def artifact_horizon(artifact_dir: Path) -> int:
    """Read the validated block horizon without materializing demand rows."""
    manifest_path = artifact_dir / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read artifact manifest {manifest_path}: {exc}") from None
    horizon = payload.get("horizon")
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon < 1:
        raise ValueError("artifact manifest must contain a positive integer horizon")
    return horizon


def provider_for_args(args: argparse.Namespace):
    if args.ieee14:
        return ieee14_provider(), 1
    if args.artifact_dir is None:
        raise ValueError("one input mode is required")
    artifact_dir = args.artifact_dir.resolve()
    if not artifact_dir.is_dir():
        raise ValueError(f"artifact directory does not exist or is not a directory: {artifact_dir}")
    return prepared_artifact_period_provider(artifact_dir), artifact_horizon(artifact_dir)


def requested_periods(args: argparse.Namespace, available_horizon: int) -> tuple[int, ...]:
    horizon = args.horizon if args.horizon is not None else available_horizon
    if horizon > available_horizon:
        raise ValueError(f"horizon {horizon} exceeds available horizon {available_horizon}")
    start = args.start_period
    count = args.period_count if args.period_count is not None else horizon - start
    if start >= horizon:
        raise ValueError("start-period must be less than horizon")
    if start + count > horizon:
        raise ValueError("start-period plus period-count exceeds horizon")
    return tuple(range(start, start + count))


def _checkpoint_exists(path: Path) -> bool:
    return path.is_dir() and any(path.glob("period_*.npz"))


def _recover_checkpoint_counts(
    checkpoint_dir: Path,
    *,
    requested_periods: tuple[int, ...],
    bus_count: int,
) -> tuple[int, int]:
    """Count completed and failed points in valid persisted period checkpoints."""
    completed_count = 0
    failed_count = 0
    requested = set(requested_periods)
    if bus_count < 1 or not checkpoint_dir.is_dir():
        return completed_count, failed_count

    for path in sorted(checkpoint_dir.glob("period_*.npz")):
        try:
            period_index = int(path.stem.removeprefix("period_"))
        except ValueError:
            continue
        if period_index not in requested:
            continue
        try:
            with np.load(path, allow_pickle=False) as checkpoint:
                complete = np.asarray(checkpoint["complete"])
                saved_period = int(np.asarray(checkpoint["period_index"]).item())
                statuses = np.asarray(checkpoint["status"], dtype="<U32")
                if (
                    complete.shape != ()
                    or complete.item() is not True
                    or saved_period != period_index
                    or statuses.shape != (bus_count,)
                    or any(status not in HOSTING_CAPACITY_STATUSES for status in statuses)
                ):
                    continue
        except (OSError, KeyError, TypeError, ValueError):
            continue
        completed = int(np.count_nonzero(statuses != ""))
        completed_count += completed
        failed_count += int(np.count_nonzero((statuses != "") & (statuses != "optimal")))
    return completed_count, failed_count


def _write_long_form(output_dir: Path, result: HostingCapacityMatrix) -> None:
    path = output_dir / "hosting_capacity_long.csv.gz"
    with gzip.open(path, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("period_index", "bus_id", "hosting_capacity_mw", "status", "solve_time_s"),
        )
        writer.writeheader()
        for row_index, period_index in enumerate(result.period_indices):
            for bus_index, bus_id in enumerate(result.bus_ids):
                status = str(result.status[row_index, bus_index])
                value = result.capacity_mw[row_index, bus_index]
                solve_time = result.solve_time_s[row_index, bus_index]
                writer.writerow(
                    {
                        "period_index": period_index,
                        "bus_id": bus_id,
                        "hosting_capacity_mw": value if status == "optimal" else "",
                        "status": status,
                        "solve_time_s": solve_time if status else "",
                    }
                )


def _summary(
    result: HostingCapacityMatrix,
    *,
    output_dir: Path,
    requested: tuple[int, ...],
    backend: str,
    wall_time_s: float,
    resumed: bool,
) -> dict[str, object]:
    counts = result.metadata.get("counts", {})
    return {
        "output_dir": str(output_dir.resolve()),
        "backend": result.metadata.get("resolved_solver_backend", backend),
        "requested_periods": list(requested),
        "bus_count": len(result.bus_ids),
        "completed_point_count": int(counts.get("completed_count", np_count(result.status != ""))),
        "failed_point_count": int(counts.get("failed_count", np_count((result.status != "") & (result.status != "optimal")))),
        "wall_time_s": round(wall_time_s, 6),
        "resume_status": "resumed" if resumed else "fresh",
        "complete": bool(result.metadata.get("complete", False)),
    }


def np_count(values) -> int:
    return sum(bool(value) for value in values.flat)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_dir = args.output_dir.resolve()
    checkpoint_dir = (args.checkpoint_dir or output_dir / "checkpoints").resolve()
    resumed = _checkpoint_exists(checkpoint_dir)
    started = time.perf_counter()
    requested: tuple[int, ...] = ()
    requested_bus_count: int | None = len(set(args.bus_ids)) if args.bus_ids else None
    try:
        provider, available_horizon = provider_for_args(args)
        requested = requested_periods(args, available_horizon)
        # Materialize one period before creating a process pool.  This catches
        # invalid ranges, artifacts, and candidate buses in the parent process.
        first_period = provider(requested[0])
        available_bus_ids = {bus.bus_id for bus in first_period.buses}
        if not args.bus_ids:
            requested_bus_count = len(available_bus_ids)
        if args.bus_ids and any(bus_id not in available_bus_ids for bus_id in args.bus_ids):
            missing = next(bus_id for bus_id in args.bus_ids if bus_id not in available_bus_ids)
            raise ValueError(f"requested bus does not exist: {missing}")
        result = compute_hosting_capacity_matrix(
            provider,
            bus_ids=args.bus_ids,
            period_indices=requested,
            solver_backend=args.solver_backend,
            solver_threads=args.solver_threads,
            workers=args.workers,
            checkpoint_dir=checkpoint_dir,
            output_dir=output_dir,
        )
        if args.write_long_form:
            _write_long_form(output_dir, result)
        summary = _summary(
            result,
            output_dir=output_dir,
            requested=requested,
            backend=args.solver_backend,
            wall_time_s=time.perf_counter() - started,
            resumed=resumed,
        )
        print(json.dumps(summary, sort_keys=True))
        return 1 if summary["complete"] and summary["failed_point_count"] else 0
    except KeyboardInterrupt:
        # compute_hosting_capacity_matrix publishes each completed period and
        # writes atomic checkpoints before the next period starts.  Do not
        # remove either output so the operator can resume the run.
        completed_count, failed_count = _recover_checkpoint_counts(
            checkpoint_dir,
            requested_periods=requested,
            bus_count=requested_bus_count or 0,
        )
        partial = {
            "output_dir": str(output_dir),
            "backend": args.solver_backend,
            "requested_periods": list(requested),
            "bus_count": requested_bus_count or 0,
            "completed_point_count": completed_count,
            "failed_point_count": failed_count,
            "wall_time_s": round(time.perf_counter() - started, 6),
            "resume_status": "interrupted",
            "complete": False,
        }
        print(json.dumps(partial, sort_keys=True))
        return 130
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
