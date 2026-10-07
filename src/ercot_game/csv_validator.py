"""Student-facing validator for the canonical long-form submission CSV."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
import csv
import io
import json
import math
from pathlib import Path
from collections.abc import Iterable, Mapping, Sequence

from .csv_submission import (
    BUS_CSV_COLUMNS,
    load_generator_costs,
    load_bus_ids,
    read_student_csv,
)
from .calendars import half_year_calendar


@dataclass(frozen=True)
class ValidationIssue:
    """One actionable validation problem."""

    message: str
    row: int | None = None

    def display(self) -> str:
        prefix = f"row {self.row}: " if self.row is not None else ""
        return f"{prefix}{self.message}"


@dataclass(frozen=True)
class ValidationReport:
    """Machine-readable and human-readable validation summary."""

    path: str
    valid: bool
    issues: tuple[ValidationIssue, ...]
    team_id: str | None = None
    submission_id: str | None = None
    row_count: int = 0
    bus_count: int = 0
    generator_count: int = 0
    storage_count: int = 0
    horizon: int | None = None

    def as_json(self) -> dict[str, object]:
        result = asdict(self)
        result["issues"] = [asdict(issue) for issue in self.issues]
        return result


def _issue(message: str, row: int | None = None) -> ValidationIssue:
    return ValidationIssue(message=message, row=row)


def _value(row: Mapping[str, str | None], name: str) -> str:
    return (row.get(name) or "").strip()


def _finite_number(value: str, name: str, row_number: int, *, required: bool) -> tuple[float | None, ValidationIssue | None]:
    if not value:
        if required:
            return None, _issue(f"{name} is required", row_number)
        return 0.0, None
    try:
        number = float(value)
    except ValueError:
        return None, _issue(f"{name} must be a finite decimal", row_number)
    if not math.isfinite(number):
        return None, _issue(f"{name} must be finite", row_number)
    return number, None


def validate_csv_submission(
    path: str | Path,
    *,
    artifact_dir: str | Path | None = None,
    bus_ids: Iterable[str] | None = None,
    generator_ids: Iterable[str] | None = None,
    horizon: int,
) -> ValidationReport:
    """Validate a canonical student CSV without mutating or evaluating it.

    ``artifact_dir`` should point to a validated scenario block containing
    ``bus.csv`` and ``generator_limits.csv``. Direct ID collections are useful
    for tests and for callers that already loaded those catalogs.
    """
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if artifact_dir is not None:
        if bus_ids is not None or generator_ids is not None:
            raise ValueError("use artifact_dir or explicit catalogs, not both")
        known_buses = load_bus_ids(artifact_dir)
        generator_costs = load_generator_costs(artifact_dir)
        known_generators = frozenset(generator_costs)
    else:
        known_buses = frozenset(bus_ids) if bus_ids is not None else None
        known_generators = frozenset(generator_ids) if generator_ids is not None else None
        generator_costs = None

    source_path = Path(path)
    issues: list[ValidationIssue] = []
    rows: list[dict[str, str | None]] = []
    team_id: str | None = None
    submission_id: str | None = None
    try:
        text = source_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return ValidationReport(str(source_path), False, (_issue("submission must be UTF-8 CSV"),), horizon=horizon)
    except OSError as exc:
        raise ValueError(f"cannot read submission: {exc}") from exc

    reader = csv.DictReader(
        io.StringIO(text, newline=""),
        restkey="__extra_fields__",
        restval=None,
        strict=True,
    )
    try:
        fieldnames = reader.fieldnames
    except csv.Error as exc:
        return ValidationReport(
            str(source_path),
            False,
            (_issue(f"malformed CSV: {exc}"),),
            horizon=horizon,
        )
    if fieldnames is None:
        return ValidationReport(str(source_path), False, (_issue("submission CSV is missing a header"),), horizon=horizon)
    if tuple(reader.fieldnames) != BUS_CSV_COLUMNS:
        return ValidationReport(
            str(source_path),
            False,
            (_issue(f"header must be exactly: {','.join(BUS_CSV_COLUMNS)}"),),
            horizon=horizon,
        )

    loads: dict[tuple[int, str], float] = {}
    generator_specs: dict[str, tuple[str, float]] = {}
    storage_specs: dict[str, tuple[str, float, float]] = {}
    seen_keys: set[tuple[int, str, str, str]] = set()

    try:
        for row_number, row in enumerate(reader, start=2):
            rows.append(row)
            if "__extra_fields__" in row or any(row.get(column) is None for column in BUS_CSV_COLUMNS):
                issues.append(_issue(f"expected exactly {len(BUS_CSV_COLUMNS)} fields", row_number))
                continue

            current_team = _value(row, "team_id")
            current_submission = _value(row, "submission_id")
            bus_id = _value(row, "bus_id")
            if not current_team:
                issues.append(_issue("team_id is required", row_number))
            if not current_submission:
                issues.append(_issue("submission_id is required", row_number))
            if not bus_id:
                issues.append(_issue("bus_id is required", row_number))
            if team_id is None and current_team:
                team_id = current_team
            elif current_team and current_team != team_id:
                issues.append(_issue("one CSV may contain only one team and submission", row_number))
            if submission_id is None and current_submission:
                submission_id = current_submission
            elif current_submission and current_submission != submission_id:
                issues.append(_issue("one CSV may contain only one team and submission", row_number))

            period_value, period_error = _finite_number(_value(row, "period"), "period", row_number, required=True)
            period: int | None = None
            if period_error:
                issues.append(period_error)
            elif period_value is not None and period_value.is_integer():
                period = int(period_value)
                if not 1 <= period <= horizon:
                    issues.append(_issue(f"period must be in 1..{horizon}", row_number))
            else:
                issues.append(_issue("period must be an integer", row_number))
            if known_buses is not None and bus_id and bus_id not in known_buses:
                issues.append(_issue(f"unknown scenario bus {bus_id}", row_number))

            load, load_error = _finite_number(_value(row, "load_mw"), "load_mw", row_number, required=True)
            if load_error:
                issues.append(load_error)
            elif load is not None and load < 0:
                issues.append(_issue("load_mw must be non-negative", row_number))

            generator_id = _value(row, "generator_id")
            storage_id = _value(row, "storage_id")
            if period is not None and bus_id:
                key = (period, bus_id, generator_id, storage_id)
                if key in seen_keys:
                    issues.append(_issue(f"duplicate submission key {key}", row_number))
                seen_keys.add(key)
            if period is not None and bus_id and load is not None:
                load_key = (period, bus_id)
                if load_key in loads and loads[load_key] != load:
                    issues.append(_issue("load_mw must agree across rows for one period/bus", row_number))
                loads[load_key] = load

            storage_power, storage_power_error = _finite_number(_value(row, "storage_power_mw"), "storage_power_mw", row_number, required=False)
            storage_energy, storage_energy_error = _finite_number(_value(row, "storage_energy_mwh"), "storage_energy_mwh", row_number, required=False)
            if storage_power_error:
                issues.append(storage_power_error)
            if storage_energy_error:
                issues.append(storage_energy_error)
            if storage_id and (storage_power or 0) <= 0:
                issues.append(_issue("storage power and energy must be positive", row_number))
            if storage_id and (storage_energy or 0) <= 0:
                issues.append(_issue("storage power and energy must be positive", row_number))
            if not storage_id and ((storage_power or 0) or (storage_energy or 0)):
                issues.append(_issue("storage capacity requires storage_id", row_number))
            if storage_id and bus_id and storage_power is not None and storage_energy is not None:
                spec = (bus_id, storage_power, storage_energy)
                if storage_id in storage_specs and storage_specs[storage_id] != spec:
                    issues.append(_issue(f"storage {storage_id} is not consistent across rows", row_number))
                storage_specs[storage_id] = spec

            capacity_value = _value(row, "generator_capacity_mw")
            if generator_id:
                capacity, capacity_error = _finite_number(capacity_value, "generator_capacity_mw", row_number, required=True)
                if capacity_error:
                    issues.append(capacity_error)
                elif capacity is not None:
                    if capacity < 0:
                        issues.append(_issue("generator capacity must be non-negative", row_number))
                    if known_generators is not None and generator_id not in known_generators:
                        issues.append(_issue(f"unknown scenario generator {generator_id}", row_number))
                    spec = (bus_id, capacity)
                    if generator_id in generator_specs and generator_specs[generator_id] != spec:
                        issues.append(_issue(f"generator {generator_id} is not consistent across rows", row_number))
                    generator_specs[generator_id] = spec
            elif capacity_value:
                issues.append(_issue("generator capacity requires generator_id", row_number))
    except csv.Error as exc:
        issues.append(_issue(f"malformed CSV: {exc}"))

    # The production parser remains the final semantic authority for a valid
    # file, ensuring this checker cannot drift from batch evaluation behavior.
    if not issues:
        try:
            read_student_csv(
                source_path,
                bus_ids=known_buses,
                horizon=horizon,
                generator_cost_by_id=generator_costs,
                require_complete_horizon=True,
            )
        except ValueError as exc:
            issues.append(_issue(str(exc)))

    return ValidationReport(
        path=str(source_path),
        valid=not issues,
        issues=tuple(issues),
        team_id=team_id,
        submission_id=submission_id,
        row_count=len(rows),
        bus_count=len({bus for _, bus in loads}),
        generator_count=len(generator_specs),
        storage_count=len(storage_specs),
        horizon=horizon,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check a student dispatch-game CSV submission.")
    parser.add_argument("--file", required=True, type=Path, help="CSV submission to validate")
    parser.add_argument("--artifact-dir", type=Path, help="validated scenario block containing bus.csv and generator_limits.csv")
    parser.add_argument("--horizon", type=int, help="explicit submission horizon; use --year/--round for released calendars")
    parser.add_argument("--year", type=int, choices=(2016, 2017, 2018), help="released data year")
    parser.add_argument("--round", dest="round_name", choices=("jan_jun", "jul_dec"), help="released half-year")
    parser.add_argument("--json", action="store_true", help="print a machine-readable JSON report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.horizon is None:
        if args.year is None or args.round_name is None:
            print("CONFIGURATION ERROR: provide --horizon or both --year and --round")
            return 2
        horizon = half_year_calendar(args.year, args.round_name).horizon
    else:
        if args.horizon <= 0:
            print("CONFIGURATION ERROR: horizon must be positive")
            return 2
        horizon = args.horizon
    try:
        report = validate_csv_submission(args.file, artifact_dir=args.artifact_dir, horizon=horizon)
    except (OSError, ValueError) as exc:
        print(f"CONFIGURATION ERROR: {exc}")
        return 2
    if args.json:
        print(json.dumps(report.as_json(), indent=2))
    else:
        status = "VALID" if report.valid else "INVALID"
        print(f"{status}: {report.path}")
        if report.valid:
            print(f"team={report.team_id} submission={report.submission_id} rows={report.row_count} buses={report.bus_count} generators={report.generator_count} storage={report.storage_count} horizon={report.horizon}")
        else:
            for issue in report.issues:
                print(f"- {issue.display()}")
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
