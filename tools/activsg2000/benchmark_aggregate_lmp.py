"""Generate baseline LMPs with one reusable six-hour DCOPF model.

This benchmark is intentionally separate from the release builder while the
six-hour package is being validated.  It exercises the same public-network
DCOPF used by the evaluator, but avoids rebuilding the 2,000-bus LP for every
operating period.  The model is safe to reuse only when generator limits and
branch topology are fixed across the block; the script checks that contract
before solving the horizon.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np

from .build_economic_profiles import (
    _read_bus_ids,
    _sha256_file,
    _write_deterministic_npz,
    build_it_value_profile,
    it_value_metadata,
)
from .dcopf import (
    _prepared_period_inputs,
    extract_lmp_by_bus,
    load_prepared_static_artifact,
    validate_balance_dual_sign,
    _reusable_dcopf_model,
    resolve_solver_backend,
)


PROFILE_VERSION = "activsg2000-baseline-dcopf-lmp-v2-reusable-six-hour"


def _topology_signature(generators: tuple[Any, ...], branches: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return the fields that must remain fixed for model reuse."""

    generator_signature = tuple(
        (
            generator.generator_id,
            generator.bus_id,
            float(generator.p_min_mw),
            float(generator.p_max_mw),
            float(generator.marginal_cost_per_mwh),
        )
        for generator in generators
    )
    branch_signature = tuple(
        (
            branch.branch_id,
            branch.from_bus_id,
            branch.to_bus_id,
            float(branch.susceptance_pu),
            float(branch.thermal_limit_mw),
        )
        for branch in branches
    )
    return generator_signature, branch_signature


def _load_block_metadata(artifact_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = artifact_root / "manifest.json"
    profile_path = artifact_root / "scenario_profile.json"
    if not manifest_path.is_file():
        raise ValueError(f"scenario block manifest is missing: {manifest_path}")
    if not profile_path.is_file():
        raise ValueError(f"scenario profile is missing: {profile_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    horizon = manifest.get("horizon")
    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon <= 0:
        raise ValueError("scenario block manifest must contain a positive integer horizon")
    time_step_hours = profile.get("time_step_hours", 1)
    if (
        not isinstance(time_step_hours, (int, float))
        or isinstance(time_step_hours, bool)
        or not math.isfinite(float(time_step_hours))
        or float(time_step_hours) <= 0
    ):
        raise ValueError("scenario_profile.json time_step_hours must be positive")
    return manifest, profile


def build_reusable_lmp_profile(
    artifact_dir: str | Path,
    output_dir: str | Path,
    *,
    solver_backend: str,
    solver_threads: int = 1,
    # A 1 MW probe is still negligible relative to this network's scale but
    # avoids false failures from rounded duals and degenerate active bases.
    epsilon_mw: float = 1.0,
    progress_every: int = 25,
) -> dict[str, Any]:
    """Solve every period in one six-hour block and write raw/scoring LMPs."""

    artifact_root = Path(artifact_dir).resolve()
    output_root = Path(output_dir).resolve()
    requested_backend = solver_backend
    resolved_backend = resolve_solver_backend(solver_backend)
    if not isinstance(solver_threads, int) or isinstance(solver_threads, bool) or solver_threads <= 0:
        raise ValueError("solver_threads must be a positive integer")
    if not math.isfinite(epsilon_mw) or epsilon_mw <= 0:
        raise ValueError("epsilon_mw must be finite and positive")
    if not isinstance(progress_every, int) or isinstance(progress_every, bool) or progress_every <= 0:
        raise ValueError("progress_every must be a positive integer")

    manifest, profile = _load_block_metadata(artifact_root)
    horizon = int(manifest["horizon"])
    bus_ids = _read_bus_ids(artifact_root)
    artifact = load_prepared_static_artifact(artifact_root)

    started = time.perf_counter()
    first_buses, first_generators, first_branches, first_fixed = _prepared_period_inputs(
        artifact_root,
        artifact,
        period_index=0,
        validate_artifact=False,
    )
    static_signature = _topology_signature(first_generators, first_branches)
    static_buses = tuple(
        type(bus)(bus.bus_id, 0.0, reference=bus.reference) for bus in first_buses
    )
    model = _reusable_dcopf_model(
        solver_backend=resolved_backend,
        buses=static_buses,
        generators=first_generators,
        branches=first_branches,
        base_mva=100.0,
        solver_threads=solver_threads,
    )

    def solve_period(period_index: int):
        buses, generators, branches, fixed = _prepared_period_inputs(
            artifact_root,
            artifact,
            period_index=period_index,
            validate_artifact=False,
        )
        if _topology_signature(generators, branches) != static_signature:
            raise ValueError(
                "reusable baseline-LMP model requires fixed generator limits and branch topology; "
                f"period {period_index} differs from period 0"
            )
        if tuple(bus.bus_id for bus in buses) != tuple(bus.bus_id for bus in first_buses):
            raise ValueError(f"period {period_index} bus order differs from period 0")
        result = model.solve(buses, fixed)
        return result

    first_result = solve_period(0)
    perturb_bus = next((bus_id for bus_id in bus_ids if bus_id in first_result.balance_dual_by_bus), None)
    if perturb_bus is None:
        raise ValueError("baseline result has no balance dual for a bus in bus.csv")
    demand_by_bus = {bus.bus_id: float(bus.demand_mw) for bus in first_buses}
    demand_by_bus[perturb_bus] += epsilon_mw
    perturbed_result = model.solve(first_buses, first_fixed, demand_mw_by_bus=demand_by_bus)
    dual_sign_validation = validate_balance_dual_sign(
        first_result,
        perturbed_result,
        bus_id=perturb_bus,
        epsilon=epsilon_mw,
    )
    if dual_sign_validation["sign"] != "positive":
        raise ValueError("unexpected negative balance-dual sign; refusing to generate LMPs")

    raw_duals = np.empty((horizon, len(bus_ids)), dtype=np.float64)
    scoring_lmps = np.empty_like(raw_duals)
    solve_started = time.perf_counter()
    for period_index in range(horizon):
        result = first_result if period_index == 0 else solve_period(period_index)
        raw_by_bus = result.balance_dual_by_bus
        if set(raw_by_bus) != set(bus_ids):
            raise ValueError(f"period {period_index} balance-dual bus set does not match bus.csv")
        lmp_by_bus = extract_lmp_by_bus(result, clip_negative=True)
        raw_duals[period_index, :] = [float(raw_by_bus[bus_id]) for bus_id in bus_ids]
        scoring_lmps[period_index, :] = [float(lmp_by_bus[bus_id]) for bus_id in bus_ids]
        if (period_index + 1) % progress_every == 0 or period_index + 1 == horizon:
            elapsed = time.perf_counter() - solve_started
            print(
                f"[{resolved_backend}] completed {period_index + 1}/{horizon} periods; "
                f"solve_elapsed_seconds={elapsed:.3f}",
                flush=True,
            )
    if not np.isfinite(raw_duals).all() or not np.isfinite(scoring_lmps).all():
        raise ValueError("baseline LMP artifact contains non-finite values")
    if (scoring_lmps < 0).any():
        raise ValueError("baseline scoring LMP artifact contains negative values")

    output_root.mkdir(parents=True, exist_ok=True)
    artifact_path = output_root / "economic_profiles.npz"
    _write_deterministic_npz(
        artifact_path,
        {"raw_balance_dual": raw_duals, "lmp_per_mwh": scoring_lmps},
    )
    it_profile_path = output_root / "economic_profile.json"
    source_horizon_hours = profile.get("source_horizon_hours", horizon * float(profile.get("time_step_hours", 1)))
    it_provenance = it_value_metadata(horizon)
    it_provenance.update(
        {
            "horizon_periods": horizon,
            "time_step_hours": float(profile.get("time_step_hours", 1)),
            "source_horizon_hours": source_horizon_hours,
        }
    )
    it_profile_path.write_text(
        json.dumps(
            {
                "it_value_per_mwh": build_it_value_profile(horizon),
                "it_value_provenance": it_provenance,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    total_seconds = time.perf_counter() - started
    solve_seconds = time.perf_counter() - solve_started
    metadata: dict[str, Any] = {
        "profile_version": PROFILE_VERSION,
        "block": manifest.get("block"),
        "horizon_periods": horizon,
        "time_step_hours": float(profile.get("time_step_hours", 1)),
        "source_horizon_hours": source_horizon_hours,
        "bus_ids": bus_ids,
        "bus_count": len(bus_ids),
        "generator_count": len(first_generators),
        "branch_count": len(first_branches),
        "price_source": "baseline ACTIVSg2000 DCOPF balance duals",
        "price_interpretation": "instructional model-derived nodal marginal energy prices; not official ERCOT market prices",
        "raw_dual_units": "objective currency per MWh",
        "scoring_lmp_units": "USD/MWh",
        "normalization": "raw balance duals clipped at zero for scoring; raw values retained in artifact",
        "dual_sign_validation": dual_sign_validation,
        "solver_backend_requested": requested_backend,
        "solver_backend_resolved": resolved_backend,
        "solver_threads": solver_threads,
        "reusable_model": True,
        "student_plan_included": False,
        "timing": {
            "total_seconds": total_seconds,
            "solve_seconds": solve_seconds,
            "periods_per_second": horizon / solve_seconds if solve_seconds > 0 else None,
        },
        "artifact_sha256": _sha256_file(artifact_path),
        "economic_profile_json_sha256": _sha256_file(it_profile_path),
        "artifact_files": {
            "lmp_npz": artifact_path.name,
            "economic_profile_json": it_profile_path.name,
            "manifest": "economic_profile_manifest.json",
        },
        "it_value_provenance": it_provenance,
    }
    (output_root / "economic_profile_manifest.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark reusable six-hour baseline DCOPF LMP generation")
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--solver-backend", choices=("auto", "highs", "gurobi"), required=True)
    parser.add_argument("--solver-threads", type=int, default=1)
    parser.add_argument("--epsilon-mw", type=float, default=1.0)
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()
    metadata = build_reusable_lmp_profile(
        args.artifact_dir,
        args.output_dir,
        solver_backend=args.solver_backend,
        solver_threads=args.solver_threads,
        epsilon_mw=args.epsilon_mw,
        progress_every=args.progress_every,
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
