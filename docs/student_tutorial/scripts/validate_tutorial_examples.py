#!/usr/bin/env python3
"""Validate generated notebook artifacts without invoking a model or evaluator."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import nbformat


EXPECTED_NOTEBOOKS = {
    "01_dataset_inspection.ipynb",
    "02_network_visualization.ipynb",
    "03_three_bus_dcopf.ipynb",
    "04_storage_soc.ipynb",
    "05_csv_validation.ipynb",
    "06_student_preview.ipynb",
    "07_strategy_ablation.ipynb",
}
EXPECTED_ARTIFACTS = {
    "dataset_inspection.json",
    "network_structure.png",
    "generator_overlay.png",
    "demand_summary.png",
    "three_bus_dcopf.json",
    "storage_soc.json",
    "csv_validation.json",
    "student_preview.json",
    "strategy_ablation.csv",
    "strategy_ablation.png",
}
CANONICAL_COLUMNS = [
    "team_id",
    "site_id",
    "bus_id",
    "connection_capacity_mw",
    "generator_capacity_mw",
    "storage_power_mw",
    "storage_energy_mwh",
]


def _finite_numbers(value: object) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_finite_numbers(item) for item in value)
    if isinstance(value, dict):
        return all(_finite_numbers(item) for item in value.values())
    return True


def validate(run_dir: Path) -> int:
    run_dir = run_dir.resolve()
    errors: list[str] = []
    actual = {path.name for path in run_dir.glob("*.ipynb")}
    if actual != EXPECTED_NOTEBOOKS:
        errors.append(f"executed notebooks mismatch: expected {sorted(EXPECTED_NOTEBOOKS)}, got {sorted(actual)}")
    for path in run_dir.glob("*.ipynb"):
        notebook = nbformat.read(path, as_version=4)
        for cell_index, cell in enumerate(notebook.cells):
            for output in cell.get("outputs", []):
                if output.output_type == "error":
                    errors.append(f"{path.name} cell {cell_index} contains an error output")

    artifact_root = run_dir.parent
    missing = sorted(name for name in EXPECTED_ARTIFACTS if not (artifact_root / name).is_file())
    errors.extend(f"missing output artifact: {name}" for name in missing)
    for name in EXPECTED_ARTIFACTS:
        path = artifact_root / name
        if path.is_file() and path.suffix in {".png", ".csv", ".json"} and path.stat().st_size == 0:
            errors.append(f"empty output artifact: {name}")

    csv_path = artifact_root / "strategy_ablation.csv"
    if csv_path.is_file():
        with csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            errors.append("strategy_ablation.csv has no rows")
        for row in rows:
            if set(row) >= set(CANONICAL_COLUMNS) and list(row)[:7] != CANONICAL_COLUMNS:
                errors.append("canonical CSV columns are not in the required order")
    for name in ("dataset_inspection.json", "three_bus_dcopf.json", "storage_soc.json", "csv_validation.json", "student_preview.json"):
        path = artifact_root / name
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"{name} is not valid JSON: {exc}")
            else:
                if not _finite_numbers(payload):
                    errors.append(f"{name} contains a non-finite numerical value")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("validated notebook outputs")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    return validate(args.run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
