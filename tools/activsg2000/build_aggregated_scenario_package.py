"""Build the six-hour ACTIVSg2000 scenario package.

The builder preserves the fixed network tables and changes only the declared
operating resolution.  ``scenario_profiles.hourly_state`` then averages each
consecutive six-hour source block before the DCOPF is assembled.  The output
is deliberately marked pending until the aggregated states have passed the
same DCOPF validation contract as the hourly source package.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Any

from ercot_game.scenario_package import (
    AGGREGATED_EXPECTED_HORIZONS,
    AGGREGATED_TIME_STEP_HOURS,
    EXPECTED_HORIZONS,
    sha256_file,
)


_BLOCK_FILES = (
    "bus.csv",
    "generator_limits.csv",
    "branch_limits.csv",
    "dcline.csv",
    "bus_load_base.csv",
    "generator_catalog.csv",
    "profile_parameters.json",
)


def _hash_payload(payload: Any) -> str:
    import hashlib

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _copy_required_files(source_block: Path, output_block: Path) -> None:
    output_block.mkdir(parents=True, exist_ok=True)
    missing = [name for name in _BLOCK_FILES if not (source_block / name).is_file()]
    if missing:
        raise ValueError(
            f"source block {source_block} is missing required files: {', '.join(missing)}"
        )
    for name in _BLOCK_FILES:
        shutil.copyfile(source_block / name, output_block / name)


def build_aggregated_package(
    source_dir: str | Path,
    output_dir: str | Path,
    *,
    time_step_hours: int = AGGREGATED_TIME_STEP_HOURS,
) -> dict[str, Any]:
    """Create a deterministic six-hour package from an hourly package."""

    if time_step_hours != AGGREGATED_TIME_STEP_HOURS:
        raise ValueError("the released aggregate package uses time_step_hours=6")
    source = Path(source_dir)
    output = Path(output_dir)
    source_manifest_path = source / "manifest.json"
    if not source_manifest_path.is_file():
        raise ValueError(f"source scenario manifest is missing: {source_manifest_path}")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("package_version") != "activsg2000-scenarios-v1":
        raise ValueError("source package must be activsg2000-scenarios-v1")
    if set(source_manifest.get("blocks", {})) != set(EXPECTED_HORIZONS):
        raise ValueError("source package must contain all six hourly half-year blocks")

    output.mkdir(parents=True, exist_ok=True)
    source_network = source / "network"
    output_network = output / "network"
    if not source_network.is_dir():
        raise ValueError(f"source network directory is missing: {source_network}")
    if output_network.exists():
        shutil.rmtree(output_network)
    shutil.copytree(source_network, output_network)
    source_profile_parameters = source / "profile_parameters.json"
    if not source_profile_parameters.is_file():
        raise ValueError(f"source package profile_parameters.json is missing: {source_profile_parameters}")
    shutil.copyfile(source_profile_parameters, output / "profile_parameters.json")

    block_entries: dict[str, dict[str, Any]] = {}
    for block, source_horizon in EXPECTED_HORIZONS.items():
        year_text, calendar_block = block.split("-", 1)
        source_block = source / year_text / calendar_block
        output_block = output / year_text / calendar_block
        _copy_required_files(source_block, output_block)
        profile_payload = json.loads(
            (source_block / "scenario_profile.json").read_text(encoding="utf-8")
        ) if (source_block / "scenario_profile.json").is_file() else {}
        profile_payload.update(
            {
                "year": int(year_text),
                "calendar_block": calendar_block,
                "time_step_hours": time_step_hours,
                "source_horizon_hours": source_horizon,
                "aggregation_method": "mean",
                "aggregation_note": (
                    "Each operating period represents six consecutive source hours; "
                    "MW rates and loads are averaged and flow-based economics use "
                    "time_step_hours=6."
                ),
            }
        )
        (output_block / "scenario_profile.json").write_text(
            json.dumps(profile_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        block_entries[block] = {
            "year": int(year_text),
            "calendar_block": calendar_block,
            "horizon": AGGREGATED_EXPECTED_HORIZONS[block],
            "source_horizon_hours": source_horizon,
            "time_step_hours": time_step_hours,
            "aggregation_method": "mean",
            "validation_status": "pending_aggregation_validation",
            "sha256": _hash_payload(
                {
                    "block": block,
                    "horizon": AGGREGATED_EXPECTED_HORIZONS[block],
                    "source_horizon_hours": source_horizon,
                    "time_step_hours": time_step_hours,
                }
            ),
        }
        (output_block / "manifest.json").write_text(
            json.dumps(
                {
                    "package_version": "activsg2000-scenarios-v2-6h",
                    "validation_status": "pending_aggregation_validation",
                    "block": block,
                    **block_entries[block],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    network_files = sorted(
        str(path.relative_to(output))
        for path in output_network.iterdir()
        if path.is_file()
    )
    network_hashes = {relative: sha256_file(output / relative) for relative in network_files}
    manifest = {
        "package_version": "activsg2000-scenarios-v2-6h",
        "validation_status": "pending_aggregation_validation",
        "source_package_version": "activsg2000-scenarios-v1",
        "time_step_hours": time_step_hours,
        "aggregation_method": "mean",
        "blocks": block_entries,
        "network": {
            "bus_count": source_manifest.get("network", {}).get("bus_count"),
            "generator_count": source_manifest.get("network", {}).get("generator_count"),
            "files": network_files,
            "sha256": network_hashes,
        },
        "validation": {
            "release_status": "pending_aggregation_validation",
            "source_hour_count": sum(EXPECTED_HORIZONS.values()),
            "aggregate_period_count": sum(AGGREGATED_EXPECTED_HORIZONS.values()),
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build_aggregated_package(args.source_dir, args.output_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
