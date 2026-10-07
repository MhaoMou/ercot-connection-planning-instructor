"""Build a compact, deterministic three-year ACTIVSg2000 scenario package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from ercot_game.scenario_package import EXPECTED_HORIZONS, sha256_file


NETWORK_FILES = ("bus.csv", "branch.csv", "gen.csv", "gencost.csv", "case.csv")


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_block_artifact(
    output: Path,
    block: str,
    buses: list[dict[str, str]],
    generators: list[dict[str, Any]],
    branches: list[dict[str, str]],
    profile_parameters: dict[str, Any],
) -> None:
    year_text, calendar_block = block.split("-", 1)
    block_dir = output / year_text / calendar_block
    block_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        block_dir / "bus.csv",
        ["bus_id", "type"],
        [{"bus_id": row["BUS_I"], "type": row["BUS_TYPE"]} for row in buses],
    )
    _write_csv(
        block_dir / "generator_limits.csv",
        ["generator_id", "bus_id", "p_min_mw", "p_max_mw", "marginal_cost_c1_per_mwh", "status", "profile_type"],
        generators,
    )
    _write_csv(
        block_dir / "branch_limits.csv",
        ["branch_id", "from_bus_id", "to_bus_id", "reactance_pu", "thermal_limit_mva", "status"],
        [
            {
                "branch_id": f"b{index:05d}",
                "from_bus_id": row["F_BUS"],
                "to_bus_id": row["T_BUS"],
                "reactance_pu": row["BR_X"],
                "thermal_limit_mva": row["RATE_A"],
                "status": row["BR_STATUS"],
            }
            for index, row in enumerate(branches, start=1)
        ],
    )
    (block_dir / "dcline.csv").write_text("status,from_bus_id,to_bus_id,Pt\n", encoding="utf-8")
    (block_dir / "bus_load_base.csv").write_text(
        (output / "network" / "bus_load_base.csv").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (block_dir / "generator_catalog.csv").write_text(
        (output / "network" / "generator_catalog.csv").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (block_dir / "profile_parameters.json").write_text(
        json.dumps(profile_parameters, indent=2) + "\n", encoding="utf-8"
    )
    (block_dir / "scenario_profile.json").write_text(
        json.dumps({"year": int(year_text), "calendar_block": calendar_block}, indent=2) + "\n",
        encoding="utf-8",
    )
    (block_dir / "manifest.json").write_text(
        json.dumps(
            {
                "package_version": "activsg2000-scenarios-v1",
                "validation_status": "pending",
                "block": block,
                "horizon": 4368 if calendar_block == "jan_jun" and int(year_text) % 4 == 0 else 4344 if calendar_block == "jan_jun" else 4416,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def build_package(
    source_dir: str | Path,
    output_dir: str | Path,
    *,
    enforce_activsg2000_size: bool = True,
) -> dict[str, Any]:
    source = Path(source_dir)
    output = Path(output_dir)
    if not source.is_dir():
        raise ValueError(f"ACTIVSg2000 source directory does not exist: {source}")
    missing = [name for name in NETWORK_FILES if not (source / name).is_file()]
    if missing:
        raise ValueError("missing ACTIVSg2000 source files: " + ", ".join(missing))

    network = output / "network"
    network.mkdir(parents=True, exist_ok=True)
    for name in NETWORK_FILES:
        shutil.copyfile(source / name, network / name)

    buses = _read(source / "bus.csv")
    generators = _read(source / "gen.csv")
    costs = _read(source / "gencost.csv")
    if enforce_activsg2000_size and len(buses) != 2000:
        raise ValueError(f"expected 2,000 ACTIVSg2000 buses, found {len(buses)}")
    if len(generators) != len(costs):
        raise ValueError("gen.csv and gencost.csv row counts differ")

    bus_base_load = [
        {"bus_id": row["BUS_I"], "base_load_mw": float(row["PD"])}
        for row in buses
    ]
    generator_profiles: list[dict[str, Any]] = []
    generator_catalog: list[dict[str, Any]] = []
    for index, (generator, cost) in enumerate(zip(generators, costs), start=1):
        generator_id = f"g{index:04d}"
        capacity = float(generator["PMAX"])
        if index % 10 == 0 and capacity > 0:
            profile_type = "solar"
        elif index % 7 == 0 and capacity > 0:
            profile_type = "wind"
        else:
            profile_type = "thermal"
        generator_catalog.append(
            {
                "generator_id": generator_id,
                "bus_id": generator["GEN_BUS"],
                "p_min_mw": max(0.0, float(generator["PMIN"])),
                "p_max_mw": max(0.0, capacity),
                "marginal_cost_c1_per_mwh": max(0.0, float(cost["C1"])),
                "status": int(generator["GEN_STATUS"]),
                "profile_type": profile_type,
            }
        )
        generator_profiles.append(
            {
                "generator_id": generator_id,
                "profile_type": profile_type,
                "seed": index * 7919,
                "availability_floor": 0.0 if profile_type != "thermal" else 1.0,
                "availability_ceiling": 1.0,
            }
        )

    _write_csv(network / "bus_load_base.csv", ["bus_id", "base_load_mw"], bus_base_load)
    _write_csv(
        network / "generator_catalog.csv",
        list(generator_catalog[0]),
        generator_catalog,
    )
    profile_parameters = {
        "profile_version": "controlled-three-year-v1",
        "load_model": {
            "seasonal_amplitude": 0.08,
            "daily_amplitude": 0.06,
            "weekly_amplitude": 0.025,
            "minimum_multiplier": 0.80,
            "maximum_multiplier": 1.20,
        },
        "generators": generator_profiles,
    }
    (output / "profile_parameters.json").write_text(
        json.dumps(profile_parameters, indent=2) + "\n", encoding="utf-8"
    )
    branches = _read(source / "branch.csv")
    for block in EXPECTED_HORIZONS:
        _write_block_artifact(output, block, buses, generator_catalog, branches, profile_parameters)

    network_files = sorted(
        str(path.relative_to(output)) for path in network.iterdir() if path.is_file()
    )
    network_hashes = {relative: sha256_file(output / relative) for relative in network_files}
    blocks = {
        block: {
            "year": int(block.split("-", 1)[0]),
            "calendar_block": block.split("-", 1)[1],
            "horizon": horizon,
            "time_step_hours": 1,
            "profile_recipe_hash": _hash_payload(
                {
                    "profile_parameters": "profile_parameters.json",
                    "block": block,
                    "horizon": horizon,
                }
            ),
            "sha256": _hash_payload({"block": block, "horizon": horizon, "network": network_hashes}),
        }
        for block, horizon in EXPECTED_HORIZONS.items()
    }
    manifest = {
        "package_version": "activsg2000-scenarios-v1",
        "validation_status": "pending",
        "source": {
            "name": "ACTIVSg2000",
            "repository": "https://github.com/caseformat/ACTIVSg2000",
            "scenario_kind": "controlled deterministic three-year instructional package",
        },
        "network": {"bus_count": len(buses), "generator_count": len(generators), "files": network_files, "sha256": network_hashes},
        "blocks": blocks,
        "validation": {"hour_count": 0, "passed_hour_count": 0, "failed_hour_count": 0},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    build_package(args.source_dir, args.output_dir)


if __name__ == "__main__":
    main()
