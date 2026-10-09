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
    load_prepared_artifact,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_ROOT = ROOT / "data/processed/activsg2000_scenarios_v1"
HORIZON_ARTIFACTS = {
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


@lru_cache(maxsize=64)
def _period_inputs(root: Path, period_index: int):
    artifact, base_load, generators_raw, branches_raw, injections = _static_inputs(root)
    absolute_hour = block_offset(artifact.scenario_year, artifact.scenario_block) + period_index
    multiplier = demand_multiplier(absolute_hour, year=artifact.scenario_year)
    demand = {bus_id: round(value * multiplier, 8) for bus_id, value in base_load.items()}
    buses = tuple(
        Bus(row["bus_id"], demand[row["bus_id"]], reference=row.get("type") == "3")
        for row in artifact.buses_raw
    )
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
    return buses, generators, branches, injections


def _clear_caches() -> None:
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


def run_case(root: Path, horizon: int) -> dict:
    started = time.perf_counter()
    before_mb = _rss_mb()
    try:
        payload = solve_model_reuse(root, horizon)
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
        "variant": "model_reuse_status_only",
        "run_status": "completed" if error is None else "error",
        "solver_status": status_values[0] if len(status_values) == 1 else "|".join(status_values),
        "wall_time_s": time.perf_counter() - started,
        "median_hourly_solve_time_s": (
            sorted(hourly)[len(hourly) // 2] if hourly else None
        ),
        "hourly_count": len(hourly),
        "process_rss_before_mb": before_mb,
        "process_rss_after_mb": after_mb,
        "error_type": error["type"] if error else None,
        "error_message": error["message"] if error else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-root", type=Path, default=DEFAULT_SCENARIO_ROOT)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "experiments/colab/results")
    parser.add_argument(
        "--horizons", nargs="+", type=int, default=(720, 4368, 4416),
        choices=(720, 4368, 4416),
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
    print(f"wrote {checkpoint}", flush=True)
    print(f"wrote {csv_path}", flush=True)
    return 0 if all(row["run_status"] == "completed" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
