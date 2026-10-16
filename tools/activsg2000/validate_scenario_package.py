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
from ercot_game.scenario_package import expected_horizons_for_manifest
from tools.activsg2000.dcopf import Branch, Bus, Generator, DCOPFInfeasible, solve_dcopf


_MISSING_HIGHSPY_MARKER = "optional project dependency 'highspy'"


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


def _is_missing_highspy(exc: RuntimeError) -> bool:
    """Return whether a runtime error is the known missing-HiGHS dependency."""
    return _MISSING_HIGHSPY_MARKER in str(exc).lower()


def _validate_block(package: Path, block: str, horizon: int, limit: int | None = None) -> dict[str, Any]:
    buses, generators, branches = _network(package)
    load_profile_inputs(package)
    year_text, calendar_block = block.split("-", 1)
    year = int(year_text)
    digest = hashlib.sha256()
    passed = 0
    failures: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    hour_limit = horizon if limit is None else min(horizon, limit)
    attempted = 0
    for period_index in range(hour_limit):
        attempted += 1
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
        except RuntimeError as exc:
            event = {"block": block, "period_index": period_index, "reason": str(exc)}
            if _is_missing_highspy(exc):
                unverified.append(event)
                break
            failures.append(event)
        except (DCOPFInfeasible, ValueError) as exc:
            failures.append({"block": block, "period_index": period_index, "reason": str(exc)})
        else:
            passed += 1
    if unverified:
        status = "unverified_dependency"
    elif failures:
        status = "failed"
    elif attempted == horizon:
        status = "passed"
    else:
        status = "partial"
    return {
        "block": block,
        "digest": digest.hexdigest(),
        "status": status,
        "passed": passed,
        "failed": len(failures),
        "unverified": unverified,
        "hour_count": attempted,
        "complete": attempted == horizon and not unverified,
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
    manifest_path = package / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"scenario manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("scenario manifest must be a JSON object")
    expected_horizons = expected_horizons_for_manifest(manifest)
    total_expected = sum(expected_horizons.values())
    if max_hours is not None and max_hours <= 0:
        raise ValueError("max_hours must be positive")
    if not isinstance(workers, int) or isinstance(workers, bool) or workers <= 0:
        raise ValueError("workers must be a positive integer")
    blocks = list(expected_horizons.items())
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
    unverified: list[dict[str, Any]] = []
    for result in results:
        block = result["block"]
        passed += result["passed"]
        failures.extend(result["failures"])
        unverified.extend(result["unverified"])
        year_text, calendar_block = block.split("-", 1)
        block_manifest_path = package / year_text / calendar_block / "manifest.json"
        if block_manifest_path.is_file():
            block_manifest = json.loads(block_manifest_path.read_text(encoding="utf-8"))
            block_manifest["validation_status"] = result["status"]
            if result["status"] == "unverified_dependency":
                block_manifest["validation_reason"] = result["unverified"][0]["reason"]
                block_manifest["validation"] = {
                    "classification": "dependency_limited",
                    "unverified_hour_count": len(result["unverified"]),
                    "unverified": result["unverified"],
                    "reason": result["unverified"][0]["reason"],
                }
            elif result["status"] == "failed":
                block_manifest["validation_reason"] = result["failures"][0]["reason"]
                block_manifest["validation"] = {
                    "classification": "solver_or_input_failure",
                    "failed_hour_count": len(result["failures"]),
                    "failures": result["failures"],
                }
            elif result["status"] == "passed":
                block_manifest.pop("validation_reason", None)
                block_manifest.pop("validation", None)
                block_manifest["validation_status"] = "passed"
                block_manifest["validated_state_hash"] = result["digest"]
            block_manifest_path.write_text(json.dumps(block_manifest, indent=2) + "\n", encoding="utf-8")
        if block in manifest.get("blocks", {}):
            manifest["blocks"][block]["validation_status"] = result["status"]
            if result["status"] == "passed" and result["complete"]:
                manifest["blocks"][block]["validated_state_hash"] = result["digest"]
            else:
                manifest["blocks"][block].pop("validated_state_hash", None)

    status = (
        "unverified_dependency"
        if unverified
        else "passed"
        if passed == total_expected and not failures
        else "failed"
        if failures
        else "partial"
    )
    manifest["validation_status"] = status
    manifest["validation"] = {
        "hour_count": passed + len(failures) + len(unverified),
        "expected_hour_count": total_expected,
        "passed_hour_count": passed,
        "failed_hour_count": len(failures),
        "unverified_hour_count": len(unverified),
        "failures": failures[:100],
    }
    if unverified:
        reason = unverified[0]["reason"]
        manifest["validation"]["classification"] = "dependency_limited"
        manifest["validation"]["reason"] = reason
        manifest["validation"]["unverified"] = unverified[:100]
        manifest["validation"]["release_status"] = "blocked_pending_highspy_validation"
        manifest["validation"]["required_before_release"] = (
            f"Run successful HiGHS-backed validation for all {total_expected} hours with highspy installed; "
            "only then may validation_status become passed and this package be released."
        )
        manifest["validation_reason"] = reason
    for block in expected_horizons:
        manifest["blocks"][block].setdefault("validation_status", "not_run")
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
    if manifest["validation_status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
