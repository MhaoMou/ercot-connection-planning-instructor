"""Run the accelerated long-horizon DCOPF benchmark in Colab.

This path is deliberately separate from the exploratory benchmark matrix.  It
uses one reusable HiGHS LP per fixed network topology and solves each hour in
status-only mode, avoiding full dispatch/dual reconstruction and Python memory
tracing in the timing loop.  The official 4,368- and 4,416-hour horizons are
unchanged.
"""

from __future__ import annotations

import argparse
import csv
from functools import lru_cache
import json
import os
from pathlib import Path
import platform
import sys
import time

try:
    import psutil
except ImportError:  # pragma: no cover - Colab installs this dependency
    psutil = None

from ercot_game.scenario_profiles import block_offset, demand_multiplier, load_profile_inputs
from tools.activsg2000 import dcopf as dcopf_module
from tools.activsg2000.dcopf import (
    Branch,
    Bus,
    Generator,
    ReusableDCOPFModel,
    ReusableDCOPFHorizonModel,
    StorageDispatchAsset,
    solve_dcopf_horizon_benders,
    load_prepared_artifact,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_ROOT = ROOT / "data/processed/activsg2000_scenarios_v1"
HORIZON_ARTIFACTS = {
    24: DEFAULT_SCENARIO_ROOT / "2028/jan_jun",
    48: DEFAULT_SCENARIO_ROOT / "2028/jan_jun",
    168: DEFAULT_SCENARIO_ROOT / "2028/jan_jun",
    720: DEFAULT_SCENARIO_ROOT / "2028/jan_jun",
    4368: DEFAULT_SCENARIO_ROOT / "2028/jan_jun",
    4416: DEFAULT_SCENARIO_ROOT / "2027/jul_dec",
}


@lru_cache(maxsize=4)
def _static_inputs(root: Path):
    artifact = load_prepared_artifact(root)
    base_load, _profiles, _catalog = load_profile_inputs(root)
    generators_raw = tuple(
        row for row in artifact.generators_raw if row.get("status", "1") == "1"
    )
    branches_raw = tuple(
        row
        for row in artifact.branches_raw
        if row.get("status", "1") == "1" and float(row["reactance_pu"]) != 0.0
    )
    injections = {}
    for row in artifact.dcline_raw:
        if row.get("status", "1") == "1":
            injections[row["to_bus_id"]] = injections.get(row["to_bus_id"], 0.0) + float(row["Pt"])
    return artifact, base_load, generators_raw, branches_raw, injections


@lru_cache(maxsize=4)
def _static_topology(root: Path):
    """Build immutable generator/branch objects once per prepared artifact."""
    artifact, _base_load, generators_raw, branches_raw, _injections = _static_inputs(root)
    generators = tuple(
        Generator(
            row["generator_id"],
            row["bus_id"],
            float(row["p_min_mw"]),
            float(row["p_max_mw"]),
            float(row["marginal_cost_c1_per_mwh"]),
        )
        for row in generators_raw
    )
    branches = tuple(
        Branch(
            row["branch_id"],
            row["from_bus_id"],
            row["to_bus_id"],
            1.0 / float(row["reactance_pu"]),
            float(row["thermal_limit_mva"]),
        )
        for row in branches_raw
    )
    bus_metadata = tuple(
        (row["bus_id"], row.get("type") == "3")
        for row in artifact.buses_raw
    )
    return bus_metadata, generators, branches


@lru_cache(maxsize=64)
def _period_inputs(root: Path, period_index: int):
    artifact, base_load, _generators_raw, _branches_raw, injections = _static_inputs(root)
    bus_metadata, generators, branches = _static_topology(root)
    absolute_hour = block_offset(artifact.scenario_year, artifact.scenario_block) + period_index
    multiplier = demand_multiplier(absolute_hour, year=artifact.scenario_year)
    demand = {bus_id: round(value * multiplier, 8) for bus_id, value in base_load.items()}
    buses = tuple(
        Bus(bus_id, demand[bus_id], reference=reference)
        for bus_id, reference in bus_metadata
    )
    return buses, generators, branches, injections


def _clear_caches() -> None:
    _static_inputs.cache_clear()
    _static_topology.cache_clear()
    _period_inputs.cache_clear()
    dcopf_module.clear_prepared_artifact_cache()
    dcopf_module._cached_network_index.cache_clear()


def _rss_mb() -> float | None:
    if psutil is None:
        return None
    return psutil.Process().memory_info().rss / (1024 * 1024)


def solve_model_reuse(root: Path, horizon: int) -> dict:
    """Solve all hourly states with one fixed-topology HiGHS model."""
    _clear_caches()
    first_buses, generators, branches, _first_injections = _period_inputs(root, 0)
    model = ReusableDCOPFModel(
        buses=first_buses,
        generators=generators,
        branches=branches,
        base_mva=100.0,
    )
    hourly_times = []
    statuses = []
    for index in range(horizon):
        buses, _generators, _branches, injections = _period_inputs(root, index)
        started = time.perf_counter()
        statuses.append(model.solve(buses, injections, return_result=False))
        hourly_times.append(time.perf_counter() - started)
    return {
        "hourly_times_s": hourly_times,
        "statuses": statuses,
    }


def solve_storage_lp(
    root: Path,
    horizon: int,
    *,
    solver: str = "coupled",
    parallel_workers: int = 1,
    solver_threads: int = 1,
    wall_time_limit_seconds: float | None = None,
    tighten_feasible_bounds: bool = False,
) -> dict:
    """Solve a representative one-team plan with the storage LP enabled.

    The plan uses one 1 MW / 4 MWh team-owned storage asset at the first
    positive-load bus.  This is a computational smoke test for the official
    storage formulation, not a student strategy or a scoring result.
    """
    _clear_caches()
    periods = []
    injections = []
    owner_load = []
    storage_bus = None
    shared_generators = None
    shared_branches = None
    for index in range(horizon):
        buses, generators, branches, fixed = _period_inputs(root, index)
        if storage_bus is None:
            storage_bus = next((bus.bus_id for bus in buses if bus.demand_mw > 0.0), buses[0].bus_id)
            shared_generators = generators
            shared_branches = branches
        else:
            # The ACTIVSg2000 scenario has fixed topology and generator limits;
            # only hourly bus demand changes. Reuse these large immutable
            # tuples rather than retaining a new generator/branch object graph
            # for every hour of a half-year horizon.
            generators = shared_generators
            branches = shared_branches
        periods.append((buses, generators, branches))
        injections.append(fixed)
        owner_load.append({storage_bus: max(next(bus.demand_mw for bus in buses if bus.bus_id == storage_bus), 0.0)})
    storage = StorageDispatchAsset(
        storage_id="benchmark-storage",
        team_id="benchmark-team",
        bus_id=storage_bus,
        power_mw=1.0,
        energy_mwh=4.0,
        initial_soc_mwh=2.0,
    )
    owner_loads = {"benchmark-team": tuple(owner_load)}
    if solver == "benders":
        build_started = time.perf_counter()
        result = solve_dcopf_horizon_benders(
            periods=tuple(periods), storage_assets=(storage,),
            fixed_injection_mw_by_period=tuple(injections),
            owner_load_mw_by_team=owner_loads,
            time_step_hours=1.0, base_mva=100.0, return_result=False,
            parallel_workers=parallel_workers,
            solver_threads=solver_threads,
            wall_time_limit_seconds=wall_time_limit_seconds,
            tighten_feasible_bounds=tighten_feasible_bounds,
        )
        total_time = time.perf_counter() - build_started
        status = result if isinstance(result, str) else result.status
        build_time = None
        solve_time = total_time
        timing = {}
    else:
        build_started = time.perf_counter()
        model = ReusableDCOPFHorizonModel(
            periods=tuple(periods), storage_assets=(storage,),
            fixed_injection_mw_by_period=tuple(injections),
            owner_load_mw_by_team=owner_loads,
            time_step_hours=1.0, base_mva=100.0,
        )
        build_time = time.perf_counter() - build_started
        solve_started = time.perf_counter()
        result = model.solve(
            fixed_injection_mw_by_period=tuple(injections),
            owner_load_mw_by_team=owner_loads, return_result=False,
            wall_time_limit_seconds=wall_time_limit_seconds,
        )
        solve_time = time.perf_counter() - solve_started
        status = result if isinstance(result, str) else result.status
        timing = model.last_solve_timing
    return {
        "hourly_times_s": None,
        "statuses": [status],
        "model_build_time_s": build_time,
        "model_reuse_solve_time_s": solve_time,
        "input_update_time_s": timing.get("input_update_s"),
        "solver_run_time_s": timing.get("solver_run_s"),
        "result_extraction_time_s": timing.get("result_extraction_s"),
        "storage_solver": solver,
    }


def run_case(
    root: Path,
    horizon: int,
    *,
    storage: bool = False,
    storage_solver: str = "coupled",
    parallel_workers: int = 1,
    solver_threads: int = 1,
    wall_time_limit_seconds: float | None = None,
    tighten_feasible_bounds: bool = False,
) -> dict:
    started = time.perf_counter()
    before_mb = _rss_mb()
    try:
        payload = (
            solve_storage_lp(
                root, horizon, solver=storage_solver,
                parallel_workers=parallel_workers,
                solver_threads=solver_threads,
                wall_time_limit_seconds=wall_time_limit_seconds,
                tighten_feasible_bounds=tighten_feasible_bounds,
            )
            if storage else solve_model_reuse(root, horizon)
        )
        error = None
    except Exception as exc:  # checkpoint failures for diagnosis and resume
        payload = {"hourly_times_s": [], "statuses": []}
        error = {"type": type(exc).__name__, "message": str(exc)}
    after_mb = _rss_mb()
    hourly = payload["hourly_times_s"]
    status_values = sorted(set(payload["statuses"]))
    return {
        "horizon_hours": horizon,
        "artifact_dir": str(root),
        "variant": f"storage_{storage_solver}_horizon" if storage else "model_reuse_status_only",
        "run_status": (
            "completed" if error is None else
            "timeout" if error["type"] == "DCOPFUnresolved" and (
                "wall-time budget" in error["message"] or "kTimeLimit" in error["message"]
            )
            else "error"
        ),
        "solver_status": status_values[0] if len(status_values) == 1 else "|".join(status_values),
        "wall_time_s": time.perf_counter() - started,
        "median_hourly_solve_time_s": (
            sorted(hourly)[len(hourly) // 2] if hourly else None
        ),
        "hourly_count": len(hourly) if hourly is not None else 0,
        "process_rss_before_mb": before_mb,
        "process_rss_after_mb": after_mb,
        "error_type": error["type"] if error else None,
        "error_message": error["message"] if error else None,
        "model_build_time_s": payload.get("model_build_time_s"),
        "model_reuse_solve_time_s": payload.get("model_reuse_solve_time_s"),
        "input_update_time_s": payload.get("input_update_time_s"),
        "solver_run_time_s": payload.get("solver_run_time_s"),
        "result_extraction_time_s": payload.get("result_extraction_time_s"),
        "storage_solver": payload.get("storage_solver"),
        "parallel_workers": parallel_workers if storage_solver == "benders" else None,
        "solver_threads": solver_threads if storage else None,
        "wall_time_limit_seconds": wall_time_limit_seconds if storage else None,
        "tighten_feasible_bounds": tighten_feasible_bounds if storage else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-root", type=Path, default=DEFAULT_SCENARIO_ROOT)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "experiments/colab/results")
    parser.add_argument(
        "--horizons", nargs="+", type=int, default=(720, 4368, 4416),
        choices=(24, 48, 168, 720, 4368, 4416),
    )
    parser.add_argument("--storage", action="store_true", help="also run the storage-coupled LP")
    parser.add_argument(
        "--storage-solver", choices=("coupled", "benders"), default="coupled",
        help="storage solve implementation; benders is the exact one-storage decomposition",
    )
    parser.add_argument(
        "--parallel-workers", type=int, default=1,
        help="parallel hourly recourse workers for Benders (default: 1)",
    )
    parser.add_argument(
        "--solver-threads", type=int, default=1,
        help="HiGHS threads per recourse/master model (default: 1; avoid oversubscription)",
    )
    parser.add_argument(
        "--wall-time-limit-seconds", type=float, default=1500.0,
        help="storage Benders wall-clock budget; default 1500 seconds (25 minutes)",
    )
    parser.add_argument(
        "--tighten-feasible-bounds", action="store_true",
        help="run the exact two-LP feasible-injection prepass for every hour",
    )
    args = parser.parse_args()

    rows = []
    args.output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = args.output_dir / "accelerated_retry_results.json"
    csv_path = args.output_dir / "accelerated_retry_results.csv"
    for horizon in args.horizons:
        root = args.scenario_root / HORIZON_ARTIFACTS[horizon].relative_to(DEFAULT_SCENARIO_ROOT)
        print(f"running horizon={horizon} artifact={root}", flush=True)
        row = run_case(root, horizon)
        rows.append(row)
        checkpoint.write_text(json.dumps({
            "python": sys.version,
            "platform": platform.platform(),
            "highspy_version": __import__("importlib.metadata", fromlist=["version"]).version("highspy"),
            "rows": rows,
        }, indent=2), encoding="utf-8")
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=sorted(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(json.dumps(row, indent=2), flush=True)
        if args.storage:
            print(f"running storage LP horizon={horizon} artifact={root}", flush=True)
            storage_row = run_case(
                root, horizon, storage=True,
                storage_solver=args.storage_solver,
                parallel_workers=args.parallel_workers,
                solver_threads=args.solver_threads,
                wall_time_limit_seconds=args.wall_time_limit_seconds,
                tighten_feasible_bounds=args.tighten_feasible_bounds,
            )
            rows.append(storage_row)
            checkpoint.write_text(json.dumps({
                "python": sys.version,
                "platform": platform.platform(),
                "highspy_version": __import__("importlib.metadata", fromlist=["version"]).version("highspy"),
                "rows": rows,
            }, indent=2), encoding="utf-8")
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=sorted(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            print(json.dumps(storage_row, indent=2), flush=True)
    print(f"wrote {checkpoint}", flush=True)
    print(f"wrote {csv_path}", flush=True)
    return 0 if all(row["run_status"] == "completed" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
