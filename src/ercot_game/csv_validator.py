"""Student-facing validator for the canonical long-form submission CSV."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
import csv
import io
import json
import math
import re
from pathlib import Path
from collections.abc import Iterable, Mapping, Sequence

from .csv_submission import CANONICAL_CSV_COLUMNS, load_bus_ids, read_student_csv
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
    site_count: int = 0
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


_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_VALID_HEADER = ",".join(CANONICAL_CSV_COLUMNS)
_VALID_EXAMPLE = "team-a,site-north,3002041,40,20,10,40"


def validate_csv_submission(
    path: str | Path,
    *,
    artifact_dir: str | Path | None = None,
    bus_ids: Iterable[str] | None = None,
    generator_ids: Iterable[str] | None = None,
    horizon: int | None = None,
) -> ValidationReport:
    """Validate a canonical one-row-per-site student CSV.

    ``artifact_dir`` should point to a validated scenario block containing
    ``bus.csv`` and ``generator_limits.csv``. Direct ID collections are useful
    for tests and for callers that already loaded those catalogs.

    ``horizon`` is retained in the report for CLI compatibility; it does not
    impose a row-count or hourly-coverage requirement.
    """
    if horizon is not None and horizon <= 0:
        raise ValueError("horizon must be positive")
    if artifact_dir is not None:
        if bus_ids is not None or generator_ids is not None:
            raise ValueError("use artifact_dir or explicit catalogs, not both")
        known_buses = load_bus_ids(artifact_dir)
        # Team-built generator IDs are user-defined labels. The public
        # generator catalog is not an asset-ID registry for submissions.
        generator_costs = None
        known_generators = None
    else:
        known_buses = frozenset(bus_ids) if bus_ids is not None else None
        known_generators = frozenset(generator_ids) if generator_ids is not None else None
        generator_costs = None

    source_path = Path(path)
    issues: list[ValidationIssue] = []
    rows: list[dict[str, str | None]] = []
    team_id: str | None = None
    site_ids: set[str] = set()
    bus_values: set[str] = set()
    generator_count = 0
    storage_count = 0
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
    if tuple(reader.fieldnames) != CANONICAL_CSV_COLUMNS:
        return ValidationReport(
            str(source_path),
            False,
            (_issue(
                f"header must be exactly: {_VALID_HEADER}; "
                f"example row: {_VALID_EXAMPLE}"
            ),),
            horizon=horizon,
        )

    try:
        for row_number, row in enumerate(reader, start=2):
            rows.append(row)
            if "__extra_fields__" in row or any(row.get(column) is None for column in CANONICAL_CSV_COLUMNS):
                issues.append(_issue(f"expected exactly {len(CANONICAL_CSV_COLUMNS)} fields", row_number))
                continue

            current_team = _value(row, "team_id")
            site_id = _value(row, "site_id")
            bus_id = _value(row, "bus_id")
            if not current_team:
                issues.append(_issue("team_id is required", row_number))
            elif _ID_PATTERN.fullmatch(current_team) is None:
                issues.append(_issue("team_id is malformed; use a nonempty ID without whitespace", row_number))
            if not site_id:
                issues.append(_issue("site_id is required", row_number))
            elif _ID_PATTERN.fullmatch(site_id) is None:
                issues.append(_issue("site_id is malformed; use a nonempty ID without whitespace", row_number))
            if not bus_id:
                issues.append(_issue("bus_id is required", row_number))
            elif _ID_PATTERN.fullmatch(bus_id) is None:
                issues.append(_issue("bus_id is malformed; use a nonempty ID without whitespace", row_number))
            if team_id is None and current_team:
                team_id = current_team
            elif current_team and current_team != team_id:
                issues.append(_issue("one CSV may contain only one team_id", row_number))
            if known_buses is not None and bus_id and bus_id not in known_buses:
                issues.append(_issue(f"unknown scenario bus {bus_id}", row_number))

            if site_id and site_id in site_ids:
                issues.append(_issue(f"duplicate site_id {site_id}", row_number))
            elif site_id:
                site_ids.add(site_id)
            if bus_id:
                bus_values.add(bus_id)

            values: dict[str, float | None] = {}
            for name in CANONICAL_CSV_COLUMNS[3:]:
                value, error = _finite_number(_value(row, name), name, row_number, required=True)
                if error:
                    issues.append(error)
                elif value is not None and value < 0:
                    issues.append(_issue(f"{name} must be non-negative", row_number))
                values[name] = value
            connection = values["connection_capacity_mw"]
            generator = values["generator_capacity_mw"]
            storage_power = values["storage_power_mw"]
            storage_energy = values["storage_energy_mwh"]
            if connection is not None and generator is not None and generator > connection:
                issues.append(_issue("generator capacity cannot exceed connection capacity", row_number))
            if storage_power is not None and storage_energy is not None:
                if (storage_power == 0) != (storage_energy == 0):
                    issues.append(_issue("storage power and energy must both be zero or positive", row_number))
                elif storage_power > 0 and not math.isclose(storage_energy, 4 * storage_power, rel_tol=1e-9, abs_tol=1e-9):
                    issues.append(_issue("storage energy must equal four times storage power", row_number))
                if storage_power > 0:
                    storage_count += 1
            if generator is not None and generator > 0:
                generator_count += 1
    except csv.Error as exc:
        issues.append(_issue(f"malformed CSV: {exc}"))

    if not rows:
        issues.append(_issue("submission CSV must contain at least one site row"))

    # The production parser remains the final semantic authority for a valid
    # file, ensuring this checker cannot drift from batch evaluation behavior.
    if not issues:
        try:
            read_student_csv(
                source_path,
                bus_ids=known_buses,
                generator_cost_by_id=generator_costs,
            )
        except ValueError as exc:
            issues.append(_issue(str(exc)))

    return ValidationReport(
        path=str(source_path),
        valid=not issues,
        issues=tuple(issues),
        team_id=team_id,
        row_count=len(rows),
        bus_count=len(bus_values),
        generator_count=generator_count,
        storage_count=storage_count,
        site_count=len(site_ids),
        horizon=horizon,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check a student dispatch-game CSV submission.")
    parser.add_argument("--file", required=True, type=Path, help="CSV submission to validate")
    parser.add_argument("--artifact-dir", type=Path, help="validated scenario block containing bus.csv and generator_limits.csv")
    parser.add_argument("--horizon", type=int, help="explicit submission horizon; use --year/--round for released calendars")
    parser.add_argument("--year", type=int, choices=(2027, 2028, 2029), help="released instructional scenario year")
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
            print(
                f"team={report.team_id} sites={report.site_count} rows={report.row_count} "
                f"buses={report.bus_count} generators={report.generator_count} "
                f"storage={report.storage_count} horizon={report.horizon}"
            )
        else:
            for issue in report.issues:
                print(f"- {issue.display()}")
            print(f"Expected header: {_VALID_HEADER}")
            print(f"Example row: {_VALID_EXAMPLE}")
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
