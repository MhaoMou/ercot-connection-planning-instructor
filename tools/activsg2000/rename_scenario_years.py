"""Relabel the controlled scenario package as instructional years 2027--2029.

The mapping preserves calendar behavior and the generated hourly states:
2016 (leap) -> 2028 (leap), 2017 -> 2027, and 2018 -> 2029.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


YEAR_MAP = {"2016": "2028", "2017": "2027", "2018": "2029"}


def _hash_payload(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def migrate(package_root: str | Path) -> None:
    root = Path(package_root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for old_year, new_year in YEAR_MAP.items():
        old_dir = root / old_year
        new_dir = root / new_year
        if old_dir.exists() and new_dir.exists():
            raise RuntimeError(f"both source and target directories exist: {old_year}, {new_year}")
        if old_dir.exists():
            old_dir.rename(new_dir)

    old_blocks = dict(manifest["blocks"])
    new_blocks = {}
    for old_block, entry in old_blocks.items():
        old_year, round_name = old_block.split("-", 1)
        new_year = YEAR_MAP[old_year]
        new_block = f"{new_year}-{round_name}"
        block_dir = root / new_year / round_name

        scenario_profile_path = block_dir / "scenario_profile.json"
        scenario_profile = json.loads(scenario_profile_path.read_text(encoding="utf-8"))
        scenario_profile["year"] = int(new_year)
        scenario_profile_path.write_text(json.dumps(scenario_profile, indent=2) + "\n", encoding="utf-8")

        block_manifest_path = block_dir / "manifest.json"
        block_manifest = json.loads(block_manifest_path.read_text(encoding="utf-8"))
        block_manifest["block"] = new_block
        block_manifest["year"] = int(new_year)
        block_manifest["horizon"] = entry["horizon"]
        block_manifest_path.write_text(json.dumps(block_manifest, indent=2) + "\n", encoding="utf-8")

        new_entry = dict(entry)
        new_entry["year"] = int(new_year)
        new_entry["calendar_block"] = round_name
        new_entry["profile_recipe_hash"] = _hash_payload({
            "profile_parameters": "profile_parameters.json",
            "block": new_block,
            "horizon": entry["horizon"],
        })
        new_entry["sha256"] = _hash_payload({
            "block": new_block,
            "horizon": entry["horizon"],
            "network": manifest["network"]["sha256"],
        })
        new_blocks[new_block] = new_entry

    manifest["blocks"] = dict(sorted(new_blocks.items()))
    manifest["source"]["scenario_kind"] = (
        "controlled deterministic instructional scenario package for years 2027-2029"
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    migrate(Path(__file__).resolve().parents[2] / "data" / "processed" / "activsg2000_scenarios_v1")
