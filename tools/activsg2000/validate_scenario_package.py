"""Validate every generated ACTIVSg2000 scenario hour with DCOPF."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from ercot_game.scenario_profiles import hourly_state, load_profile_inputs
from ercot_game.scenario_package import EXPECTED_HORIZONS
from tools.activsg2000.dcopf import Branch, Bus, Generator, DCOPFInfeasible, solve_dcopf


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _network(root: Path) -> tuple[tuple[Bus, ...], tuple[Generator, ...], tuple[Branch, ...]]:
    buses_raw = _read(root / "network" / "bus.csv")
    generators_raw = _read(root / "network" / "generator_catalog.csv")
    branches_raw = _read(root / "network" / "branch.csv")
    buses = tuple(
        Bus(row["BUS_I"], 0.0, reference=row["BUS_TYPE"] == "3")
        for row in buses_raw
    )
    generators = tuple(
        Generator(
            row["generator_id"],
            row["bus_id"],
            0.0 if row["profile_type"] in {"wind", "solar"} else float(row["p_min_mw"]),
            float(row["p_max_mw"]),
            float(row["marginal_cost_c1_per_mwh"]),
        )
        for row in generators_raw
        if int(row["status"]) == 1 and float(row["p_max_mw"]) > 0
    )
    branches = tuple(
        Branch(
            f"b{index:05d}",
            row["F_BUS"],
            row["T_BUS"],
            1.0 / float(row["BR_X"]),
            float(row["RATE_A"]),
        )
        for index, row in enumerate(branches_raw, start=1)
        if row["BR_STATUS"] == "1" and float(row["BR_X"]) != 0 and float(row["RATE_A"]) > 0
    )
    return buses, generators, branches


def _state_hash(demand: dict[str, float], availability: dict[str, float]) -> str:
    payload = json.dumps({"demand": demand, "availability": availability}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_block(package: Path, block: str, horizon: int, limit: int | None = None) -> dict[str, Any]:
    buses, generators, branches = _network(package)
    load_profile_inputs(package)
    year_text, calendar_block = block.split("-", 1)
    year = int(year_text)
    digest = hashlib.sha256()
    passed = 0
    failures: list[dict[str, Any]] = []
    hour_count = horizon if limit is None else min(horizon, limit)
    for period_index in range(hour_count):
        demand, availability = hourly_state(
            package, year=year, calendar_block=calendar_block, period_index=period_index
        )
        digest.update(_state_hash(demand, availability).encode("ascii"))
        hourly_generators = tuple(
            Generator(
                generator.generator_id,
                generator.bus_id,
                generator.p_min_mw,
                min(generator.p_max_mw, availability.get(generator.generator_id, generator.p_max_mw)),
                generator.marginal_cost_per_mwh,
            )
            for generator in generators
        )
        try:
            solve_dcopf(
                buses=tuple(Bus(bus.bus_id, demand[bus.bus_id], bus.reference) for bus in buses),
                generators=hourly_generators,
                branches=branches,
                base_mva=100.0,
            )
        except (DCOPFInfeasible, ValueError, RuntimeError) as exc:
            failures.append({"block": block, "period_index": period_index, "reason": str(exc)})
        else:
            passed += 1
    return {
        "block": block,
        "digest": digest.hexdigest(),
        "passed": passed,
        "failed": len(failures),
        "hour_count": hour_count,
        "complete": hour_count == horizon,
        "failures": failures,
    }


def _validate_block_item(args: tuple[Path, str, int]) -> dict[str, Any]:
    return _validate_block(*args)


def validate_package(
    root: str | Path,
    *,
    max_hours: int | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    package = Path(root)
    total_expected = sum(EXPECTED_HORIZONS.values())
    if max_hours is not None and max_hours <= 0:
        raise ValueError("max_hours must be positive")
    if not isinstance(workers, int) or isinstance(workers, bool) or workers <= 0:
        raise ValueError("workers must be a positive integer")
    blocks = list(EXPECTED_HORIZONS.items())
    if max_hours is None and workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            results = list(
                executor.map(
                    _validate_block_item,
                    [(package, block, horizon) for block, horizon in blocks],
                )
            )
    else:
        remaining = max_hours
        results = []
        for block, horizon in blocks:
            if remaining is not None and remaining <= 0:
                break
            limit = None if remaining is None else min(horizon, remaining)
            result = _validate_block(package, block, horizon, limit)
            results.append(result)
            if remaining is not None:
                remaining -= result["hour_count"]
    passed = 0
    failures: list[dict[str, Any]] = []
    block_hashes: dict[str, str] = {}
    for result in results:
        block = result["block"]
        block_hashes[block] = result["digest"]
        passed += result["passed"]
        failures.extend(result["failures"])
        if result["complete"] and result["failed"] == 0:
            year_text, calendar_block = block.split("-", 1)
            block_manifest_path = package / year_text / calendar_block / "manifest.json"
            if block_manifest_path.is_file():
                block_manifest = json.loads(block_manifest_path.read_text(encoding="utf-8"))
                block_manifest["validation_status"] = "passed"
                block_manifest["validated_state_hash"] = result["digest"]
                block_manifest_path.write_text(json.dumps(block_manifest, indent=2) + "\n", encoding="utf-8")

    status = "passed" if passed == total_expected and not failures else "failed" if failures else "partial"
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["validation_status"] = status
    manifest["validation"] = {
        "hour_count": passed + len(failures),
        "expected_hour_count": total_expected,
        "passed_hour_count": passed,
        "failed_hour_count": len(failures),
        "failures": failures[:100],
    }
    for block, block_hash in block_hashes.items():
        manifest["blocks"][block]["validated_state_hash"] = block_hash
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--max-hours", type=int)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    manifest = validate_package(args.package, max_hours=args.max_hours, workers=args.workers)
    print(json.dumps(manifest["validation"], indent=2))
    if manifest["validation_status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
