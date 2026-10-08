"""Strict CSV submission reader for the dispatch game.

Current submissions are one row per site. Historical hourly CSVs remain
available through the explicit legacy parser below.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import io
import math
import re
from pathlib import Path
from collections.abc import Mapping

from .models import ConnectionPlan, GeneratorPlan, SitePlan, StoragePlan, TeamPlan


# Canonical public submission contract: exactly one row per site. The legacy
# names below are retained as identity aliases so existing imports continue to
# resolve to the canonical seven-column tuple.
CANONICAL_CSV_COLUMNS = (
    "team_id", "site_id", "bus_id", "connection_capacity_mw",
    "generator_capacity_mw", "storage_power_mw", "storage_energy_mwh",
)
LEGACY_CSV_COLUMNS = (
    "team_id", "submission_id", "period", "site_id", "load_mw",
    "generator_id", "generator_capacity_mw", "storage_id",
    "storage_power_mw", "storage_energy_mwh",
)
LEGACY_BUS_CSV_COLUMNS = tuple(
    "bus_id" if column == "site_id" else column for column in LEGACY_CSV_COLUMNS
)

CSV_COLUMNS = CANONICAL_CSV_COLUMNS  # Deprecated compatibility alias.
BUS_CSV_COLUMNS = CANONICAL_CSV_COLUMNS  # Deprecated compatibility alias.
STORAGE_DURATION_HOURS = 4.0
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CANONICAL_HEADER_MESSAGE = (
    "submission header must be exactly: " + ",".join(CANONICAL_CSV_COLUMNS)
)
_CANONICAL_EXAMPLE = (
    "Example: team-a,site-north,1001,40,20,10,40"
)

# Team-built generators use a common convex curve, independent of the public
# generator catalog. The scalar is retained only for compatibility with the
# canonical plan model; dispatch expands it into three cost segments.
TEAM_GENERATOR_COST_CURVE = ((0.25, 60.0), (0.50, 100.0), (0.25, 160.0))
TEAM_GENERATOR_COMPATIBILITY_COST_PER_MWH = 100.0


def load_bus_ids(artifact_dir: str | Path) -> frozenset[str]:
    """Load valid source bus IDs from a validated scenario block."""
    with (Path(artifact_dir) / "bus.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, strict=True)
        try:
            fieldnames = tuple(reader.fieldnames or ())
            if "bus_id" not in fieldnames:
                raise ValueError("bus catalog header must contain a bus_id column")
            bus_ids = set()
            for row_number, row in enumerate(reader, start=2):
                bus_id = (row.get("bus_id") or "").strip()
                if not bus_id:
                    raise ValueError(f"bus catalog row {row_number}: bus_id is required")
                bus_ids.add(bus_id)
        except csv.Error as exc:
            raise ValueError(f"malformed bus catalog CSV: {exc}") from None
    bus_ids = frozenset(bus_ids)
    if not bus_ids:
        raise ValueError("prepared artifact contains no bus IDs")
    return bus_ids


def load_generator_costs(artifact_dir: str | Path) -> dict[str, float]:
    """Load dataset marginal generation costs keyed by generator ID."""
    with (Path(artifact_dir) / "generator_limits.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, strict=True)
        try:
            fieldnames = tuple(reader.fieldnames or ())
            required = ("generator_id", "marginal_cost_c1_per_mwh")
            if any(name not in fieldnames for name in required):
                raise ValueError(
                    "generator catalog header must contain: " + ",".join(required)
                )
            result: dict[str, float] = {}
            for row_number, row in enumerate(reader, start=2):
                generator_id = (row.get("generator_id") or "").strip()
                if not generator_id:
                    raise ValueError(f"generator catalog row {row_number}: generator_id is required")
                try:
                    cost = float(row["marginal_cost_c1_per_mwh"])
                except (KeyError, TypeError, ValueError):
                    raise ValueError(
                        f"generator catalog row {row_number}: marginal_cost_c1_per_mwh must be finite and non-negative"
                    ) from None
                if not math.isfinite(cost) or cost < 0:
                    raise ValueError(
                        f"generator catalog row {row_number}: marginal_cost_c1_per_mwh must be finite and non-negative"
                    )
                if generator_id in result:
                    raise ValueError(f"generator catalog row {row_number}: duplicate generator_id {generator_id}")
                result[generator_id] = cost
        except csv.Error as exc:
            raise ValueError(f"malformed generator catalog CSV: {exc}") from None
    if not result:
        raise ValueError("prepared artifact contains no generator cost records")
    return result


@dataclass(frozen=True)
class CSVSubmission:
    team_id: str
    submission_id: str | None
    plan: TeamPlan
    sha256: str


def read_submission_identity(path: str | Path) -> tuple[str | None, str | None]:
    """Read the first declared team and optional submission identifier.

    This deliberately performs no schema or value validation.  Batch
    collection uses it only to preserve a readable team identifier when the
    full submission fails validation.
    """
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    try:
        if reader.fieldnames is None:
            return None, None
        for row in reader:
            team_id = (row.get("team_id") or "").strip()
            if team_id:
                submission_id = (row.get("submission_id") or "").strip() or None
                return team_id, submission_id
    except csv.Error as exc:
        raise ValueError(f"malformed CSV: {exc}") from None
    return None, None


def _strict_rows(reader):
    """Yield rows while converting parser failures into per-file errors."""
    try:
        yield from reader
    except csv.Error as exc:
        raise ValueError(f"malformed CSV: {exc}") from None


def _text(row: Mapping[str, str], name: str, row_number: int, *, required: bool = False) -> str:
    value = (row.get(name) or "").strip()
    if required and not value:
        raise ValueError(f"row {row_number}: {name} is required")
    return value


def _number(row: Mapping[str, str], name: str, row_number: int, *, required: bool = True) -> float:
    value = _text(row, name, row_number, required=required)
    if not value:
        if required:
            raise ValueError(f"row {row_number}: {name} is required")
        return 0.0
    try:
        number = float(value)
    except ValueError:
        raise ValueError(f"row {row_number}: {name} must be a finite decimal") from None
    if not math.isfinite(number):
        raise ValueError(f"row {row_number}: {name} must be finite")
    return number


def _validate_id(value: str, name: str, row_number: int) -> str:
    if not value:
        raise ValueError(f"row {row_number}: {name} is required")
    if _ID_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"row {row_number}: {name} is malformed; use letters, numbers, "
            "underscore, period, colon, or hyphen without whitespace"
        )
    return value


def _canonical_number(row: Mapping[str, str | None], name: str, row_number: int) -> float:
    value = (row.get(name) or "").strip()
    if not value:
        raise ValueError(f"row {row_number}: {name} is required")
    try:
        number = float(value)
    except ValueError:
        raise ValueError(f"row {row_number}: {name} must be a finite decimal") from None
    if not math.isfinite(number):
        raise ValueError(f"row {row_number}: {name} must be finite")
    if number < 0:
        raise ValueError(f"row {row_number}: {name} must be non-negative")
    return number


def read_student_csv(
    path: str | Path,
    *,
    bus_ids: set[str] | frozenset[str] | None = None,
    horizon: int | None = None,
    generator_cost_by_id: Mapping[str, float] | None = None,
    region_to_source_bus: Mapping[str, str] | None = None,
    default_generator_cost_per_mwh: float | None = None,
    require_complete_horizon: bool = False,
    allow_legacy: bool = False,
) -> CSVSubmission:
    """Read the canonical one-row-per-site student CSV.

    The canonical contract has exactly seven columns and does not contain a
    submission ID or hourly rows. ``horizon`` is optional and only controls
    the length of the legacy compatibility profile exposed to older evaluator
    callers. Set ``allow_legacy`` explicitly when reading historical
    long-form fixtures; normal validation never does so.
    """
    if horizon is not None and horizon <= 0:
        raise ValueError("horizon must be positive")
    if not isinstance(require_complete_horizon, bool):
        raise ValueError("require_complete_horizon must be a boolean")
    if not isinstance(allow_legacy, bool):
        raise ValueError("allow_legacy must be a boolean")
    if bus_ids is None and region_to_source_bus is not None:
        bus_ids = frozenset(region_to_source_bus.values())
    if generator_cost_by_id is None and default_generator_cost_per_mwh is not None:
        if not math.isfinite(default_generator_cost_per_mwh) or default_generator_cost_per_mwh < 0:
            raise ValueError("default generator cost must be finite and non-negative")

    source_path = Path(path)
    raw = source_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("submission must be UTF-8 CSV") from None

    reader = csv.DictReader(
        io.StringIO(text, newline=""),
        restkey="__extra_fields__",
        restval=None,
        strict=True,
    )
    try:
        fieldnames = reader.fieldnames
    except csv.Error as exc:
        raise ValueError(f"malformed CSV: {exc}") from None
    if fieldnames is None:
        raise ValueError("submission CSV is missing a header")
    fieldnames = tuple(fieldnames)
    if fieldnames in (LEGACY_CSV_COLUMNS, LEGACY_BUS_CSV_COLUMNS):
        if allow_legacy:
            return read_legacy_student_csv(
                source_path,
                bus_ids=bus_ids,
                horizon=horizon or 1,
                generator_cost_by_id=generator_cost_by_id,
                region_to_source_bus=region_to_source_bus,
                default_generator_cost_per_mwh=default_generator_cost_per_mwh,
                require_complete_horizon=require_complete_horizon,
            )
        raise ValueError(f"{_CANONICAL_HEADER_MESSAGE}; {_CANONICAL_EXAMPLE}")
    if fieldnames != CANONICAL_CSV_COLUMNS:
        raise ValueError(f"{_CANONICAL_HEADER_MESSAGE}; {_CANONICAL_EXAMPLE}")

    team_id: str | None = None
    sites: list[SitePlan] = []
    seen_site_ids: set[str] = set()
    for row_number, row in enumerate(_strict_rows(reader), start=2):
        if "__extra_fields__" in row or any(row.get(column) is None for column in CANONICAL_CSV_COLUMNS):
            raise ValueError(
                f"row {row_number}: expected exactly {len(CANONICAL_CSV_COLUMNS)} fields"
            )
        current_team = _validate_id(_text(row, "team_id", row_number), "team_id", row_number)
        site_id = _validate_id(_text(row, "site_id", row_number), "site_id", row_number)
        bus_id = _validate_id(_text(row, "bus_id", row_number), "bus_id", row_number)
        if team_id is None:
            team_id = current_team
        elif current_team != team_id:
            raise ValueError(f"row {row_number}: one CSV may contain only one team_id")
        if site_id in seen_site_ids:
            raise ValueError(f"row {row_number}: duplicate site_id {site_id}")
        seen_site_ids.add(site_id)
        if bus_ids is not None and bus_id not in bus_ids:
            raise ValueError(f"row {row_number}: unknown scenario bus {bus_id}")

        connection_capacity = _canonical_number(row, "connection_capacity_mw", row_number)
        generator_capacity = _canonical_number(row, "generator_capacity_mw", row_number)
        storage_power = _canonical_number(row, "storage_power_mw", row_number)
        storage_energy = _canonical_number(row, "storage_energy_mwh", row_number)
        if generator_capacity > connection_capacity:
            raise ValueError(
                f"row {row_number}: generator capacity cannot exceed connection capacity"
            )
        if (storage_power == 0) != (storage_energy == 0):
            raise ValueError(
                f"row {row_number}: storage power and energy must both be zero or positive"
            )
        if storage_power > 0 and not math.isclose(
            storage_energy,
            STORAGE_DURATION_HOURS * storage_power,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(
                f"row {row_number}: storage energy must equal four times storage power"
            )
        sites.append(
            SitePlan(
                site_id=site_id,
                bus_id=bus_id,
                connection_capacity_mw=connection_capacity,
                generator_capacity_mw=generator_capacity,
                storage_power_mw=storage_power,
                storage_energy_mwh=storage_energy,
            )
        )

    if team_id is None:
        raise ValueError("submission CSV must contain at least one data row")

    profile = tuple(1.0 for _ in range(horizon)) if horizon is not None else ()
    connections = tuple(
        ConnectionPlan(site.site_id, site.bus_id, site.connection_capacity_mw, profile)
        for site in sites
    )
    generators = tuple(
        GeneratorPlan(
            f"generator:{team_id}:{site.site_id}",
            site.site_id,
            site.generator_capacity_mw,
            TEAM_GENERATOR_COMPATIBILITY_COST_PER_MWH,
        )
        for site in sites
        if site.generator_capacity_mw > 0
    )
    storages = tuple(
        StoragePlan(
            f"storage:{team_id}:{site.site_id}",
            site.site_id,
            site.storage_power_mw,
            site.storage_energy_mwh,
        )
        for site in sites
        if site.storage_power_mw > 0
    )
    return CSVSubmission(
        team_id=team_id,
        submission_id=None,
        plan=TeamPlan(
            team_id,
            connections=connections,
            generators=generators,
            storages=storages,
            sites=tuple(sites),
        ),
        sha256=digest,
    )


def read_legacy_student_csv(
    path: str | Path,
    *,
    bus_ids: set[str] | frozenset[str] | None = None,
    horizon: int,
    generator_cost_by_id: Mapping[str, float] | None = None,
    region_to_source_bus: Mapping[str, str] | None = None,
    default_generator_cost_per_mwh: float | None = None,
    require_complete_horizon: bool = False,
) -> CSVSubmission:
    """Read one canonical student CSV using scenario source bus IDs.

    ``region_to_source_bus`` is retained only as a deprecated compatibility
    alias for older tests/fixtures. New submissions must identify a source bus
    directly. Generator operating costs are inherited from the dataset catalog;
    a default cost is accepted only for legacy callers that omit the catalog.
    """
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if not isinstance(require_complete_horizon, bool):
        raise ValueError("require_complete_horizon must be a boolean")
    if bus_ids is None:
        if region_to_source_bus is not None:
            bus_ids = frozenset(region_to_source_bus.values())
        else:
            bus_ids = None
    if generator_cost_by_id is None and default_generator_cost_per_mwh is not None:
        if not math.isfinite(default_generator_cost_per_mwh) or default_generator_cost_per_mwh < 0:
            raise ValueError("default generator cost must be finite and non-negative")
    source_path = Path(path)
    raw = source_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("submission must be UTF-8 CSV") from None
    extra_fields_key = "__extra_fields__"
    reader = csv.DictReader(
        io.StringIO(text, newline=""),
        restkey=extra_fields_key,
        restval=None,
        strict=True,
    )
    try:
        fieldnames = reader.fieldnames
    except csv.Error as exc:
        raise ValueError(f"malformed CSV: {exc}") from None
    if fieldnames is None:
        raise ValueError("submission CSV is missing a header")
    fieldnames = tuple(reader.fieldnames)
    bus_header = fieldnames == LEGACY_BUS_CSV_COLUMNS
    if fieldnames not in (LEGACY_CSV_COLUMNS, LEGACY_BUS_CSV_COLUMNS):
        raise ValueError(f"legacy submission header must be exactly: {','.join(LEGACY_BUS_CSV_COLUMNS)}")
    location_column = "bus_id" if bus_header else "site_id"

    team_id: str | None = None
    submission_id: str | None = None
    # Store one row per period/site, then build profiles after validation.
    loads: dict[tuple[int, str], float] = {}
    generator_specs: dict[str, tuple[str, float]] = {}
    storage_specs: dict[str, tuple[str, float, float]] = {}
    seen_keys: set[tuple[int, str, str, str]] = set()
    for row_number, row in enumerate(_strict_rows(reader), start=2):
        if extra_fields_key in row or any(row[column] is None for column in fieldnames):
            raise ValueError(f"row {row_number}: expected exactly {len(LEGACY_CSV_COLUMNS)} fields")
        current_team = _text(row, "team_id", row_number, required=True)
        current_submission = _text(row, "submission_id", row_number, required=True)
        bus_id = _text(row, location_column, row_number, required=True)
        if not bus_header and region_to_source_bus is not None:
            bus_id = region_to_source_bus.get(bus_id, bus_id)
        if team_id is None:
            team_id, submission_id = current_team, current_submission
        elif (current_team, current_submission) != (team_id, submission_id):
            raise ValueError(f"row {row_number}: one CSV may contain only one team and submission")
        try:
            period = int(_text(row, "period", row_number, required=True))
        except ValueError:
            raise ValueError(f"row {row_number}: period must be an integer") from None
        if not 1 <= period <= horizon:
            raise ValueError(f"row {row_number}: period must be in 1..{horizon}")
        if bus_ids is not None and bus_id not in bus_ids:
            raise ValueError(f"row {row_number}: unknown scenario bus {bus_id}")
        load = _number(row, "load_mw", row_number)
        if load < 0:
            raise ValueError(f"row {row_number}: load_mw must be non-negative")
        generator_id = _text(row, "generator_id", row_number)
        storage_id = _text(row, "storage_id", row_number)
        key = (period, bus_id, generator_id, storage_id)
        if key in seen_keys:
            raise ValueError(f"row {row_number}: duplicate submission key {key}")
        seen_keys.add(key)
        load_key = (period, bus_id)
        if load_key in loads and loads[load_key] != load:
            raise ValueError(f"row {row_number}: load_mw must agree across rows for one period/site")
        loads[load_key] = load
        storage_power = _number(row, "storage_power_mw", row_number, required=False)
        storage_energy = _number(row, "storage_energy_mwh", row_number, required=False)
        if not bus_header and region_to_source_bus is not None and (storage_id or storage_power or storage_energy):
            raise ValueError(f"row {row_number}: storage is reserved for a later schema version in legacy submissions")
        if storage_id and (storage_power <= 0 or storage_energy <= 0):
            raise ValueError(f"row {row_number}: storage power and energy must be positive")
        if not storage_id and (storage_power or storage_energy):
            raise ValueError(f"row {row_number}: storage capacity requires storage_id")
        if storage_id:
            storage_specs.setdefault(storage_id, (bus_id, storage_power, storage_energy))
            if storage_specs[storage_id] != (bus_id, storage_power, storage_energy):
                raise ValueError(f"row {row_number}: storage {storage_id} is not consistent across rows")
        if generator_id:
            capacity = _number(row, "generator_capacity_mw", row_number)
            if capacity < 0:
                raise ValueError(f"row {row_number}: generator capacity must be non-negative")
            existing = generator_specs.get(generator_id)
            spec = (bus_id, capacity)
            if existing is not None and existing != spec:
                raise ValueError(f"row {row_number}: generator {generator_id} is not consistent across rows")
            generator_specs[generator_id] = spec
        elif _text(row, "generator_capacity_mw", row_number):
            raise ValueError(f"row {row_number}: generator capacity requires generator_id")

    if team_id is None or submission_id is None:
        raise ValueError("submission CSV must contain at least one data row")
    if require_complete_horizon:
        expected_periods = set(range(1, horizon + 1))
        for bus_id in sorted({bus_id for _, bus_id in loads}):
            actual_periods = {period for period, submitted_bus in loads if submitted_bus == bus_id}
            if actual_periods != expected_periods:
                missing = sorted(expected_periods - actual_periods)
                raise ValueError(
                    f"bus {bus_id} must cover every period 1..{horizon}; "
                    f"missing {len(missing)} period(s)"
                )
    site_ids = sorted({bus_id for _, bus_id in loads})
    connections: list[ConnectionPlan] = []
    for site_id in site_ids:
        profile = tuple(loads.get((period, site_id), 0.0) for period in range(1, horizon + 1))
        capacity = max(profile)
        normalized = tuple((value / capacity) if capacity else 0.0 for value in profile)
        connections.append(ConnectionPlan(site_id, site_id, capacity, normalized))
    generators = tuple(
        GeneratorPlan(
            generator_id,
            next(connection.connection_id for connection in connections if connection.bus_id == bus_id),
            capacity,
            TEAM_GENERATOR_COMPATIBILITY_COST_PER_MWH,
        )
        for generator_id, (bus_id, capacity) in sorted(generator_specs.items())
    )
    storages = tuple(
        StoragePlan(storage_id, next(connection.connection_id for connection in connections if connection.bus_id == bus_id), power, energy)
        for storage_id, (bus_id, power, energy) in sorted(storage_specs.items())
    )
    return CSVSubmission(
        team_id=team_id,
        submission_id=submission_id,
        plan=TeamPlan(team_id, connections=tuple(connections), generators=generators, storages=storages),
        sha256=digest,
    )
