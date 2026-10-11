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
STRATEGY_COLUMNS = CANONICAL_COLUMNS + ["variant", "validation_status", "preview_metric"]


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


def validate_strategy_csv(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle, strict=True)
            try:
                header = next(reader)
            except StopIteration:
                return ["strategy_ablation.csv is empty"]
            if header != STRATEGY_COLUMNS:
                errors.append("strategy_ablation.csv must use the exact canonical CSV header and strategy columns")
            for row_number, row in enumerate(reader, start=2):
                if len(row) != len(STRATEGY_COLUMNS):
                    errors.append(f"strategy_ablation.csv row {row_number} must have exactly {len(STRATEGY_COLUMNS)} fields")
                    continue
                for column in CANONICAL_COLUMNS[:3]:
                    if not row[CANONICAL_COLUMNS.index(column)].strip():
                        errors.append(f"strategy_ablation.csv row {row_number} {column} is required")
                for column in CANONICAL_COLUMNS[3:]:
                    try:
                        value = float(row[CANONICAL_COLUMNS.index(column)])
                    except ValueError:
                        errors.append(f"strategy_ablation.csv row {row_number} {column} must be numeric")
                        continue
                    if not math.isfinite(value):
                        errors.append(f"strategy_ablation.csv row {row_number} {column} must be numeric and finite")
                    elif value < 0:
                        errors.append(f"strategy_ablation.csv row {row_number} {column} must be non-negative")
                try:
                    preview_metric = float(row[-1])
                except ValueError:
                    errors.append(f"strategy_ablation.csv row {row_number} preview_metric must be numeric")
                else:
                    if not math.isfinite(preview_metric):
                        errors.append(f"strategy_ablation.csv row {row_number} preview_metric must be finite")
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        errors.append(f"strategy_ablation.csv is malformed: {exc}")
    return errors


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
    allowed_published_entries = EXPECTED_ARTIFACTS | {run_dir.name}
    unexpected = sorted(
        path.name
        for path in artifact_root.iterdir()
        if path.name not in allowed_published_entries
    )
    errors.extend(f"unexpected published output: {name}" for name in unexpected)
    missing = sorted(name for name in EXPECTED_ARTIFACTS if not (artifact_root / name).is_file())
    errors.extend(f"missing output artifact: {name}" for name in missing)
    for name in EXPECTED_ARTIFACTS:
        path = artifact_root / name
        if path.is_file() and path.suffix in {".png", ".csv", ".json"} and path.stat().st_size == 0:
            errors.append(f"empty output artifact: {name}")

    csv_path = artifact_root / "strategy_ablation.csv"
    if csv_path.is_file():
        errors.extend(validate_strategy_csv(csv_path))
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
