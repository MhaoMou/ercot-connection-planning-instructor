"""Scalable linear DC optimal power flow backed by HiGHS.

The model is a single-period LP. Multi-period evaluation should call it once
per period with the scenario's demand and availability values. Storage and
candidate data-center load can be added as additional variables in the
evaluator layer without changing the network balance equations here.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
from pathlib import Path
import csv
from collections.abc import Mapping, Sequence
from types import MappingProxyType


@dataclass(frozen=True)
class Bus:
    bus_id: str
    demand_mw: float
    reference: bool = False


@dataclass(frozen=True)
class Generator:
    generator_id: str
    bus_id: str
    p_min_mw: float
    p_max_mw: float
    marginal_cost_per_mwh: float


@dataclass(frozen=True)
class Branch:
    branch_id: str
    from_bus_id: str
    to_bus_id: str
    susceptance_pu: float
    thermal_limit_mw: float


@dataclass(frozen=True)
class DCOPFResult:
    status: str
    objective_cost: float
    generation_mw: dict[str, float]
    angle_by_bus: dict[str, float]
    flow_mw_by_branch: dict[str, float]
    balance_dual_by_bus: dict[str, float]


class DCOPFInfeasible(ValueError):
    """Raised when HiGHS reports no feasible DCOPF solution."""


class DCOPFUnresolved(RuntimeError):
    """Raised when HiGHS cannot resolve the DCOPF model status."""


def extract_lmp_by_bus(result: DCOPFResult, *, clip_negative: bool = False) -> dict[str, float]:
    """Return nodal energy prices from a solved DCOPF balance dual.

    The balance rows are written as ``generation + net inflow = demand``.
    Consequently, the HiGHS row dual is the marginal objective cost of one
    additional MW of demand at that bus.  ``clip_negative`` is available for
    the instructional settlement artifact, whose published prices are
    required to be non-negative; the raw duals remain available separately.
    """

    prices = {}
    for bus_id, dual in result.balance_dual_by_bus.items():
        value = float(dual)
        if not math.isfinite(value):
            raise ValueError(f"non-finite balance dual for bus {bus_id}")
        prices[bus_id] = max(0.0, value) if clip_negative else value
    return prices


def validate_balance_dual_sign(
    base_result: DCOPFResult,
    perturbed_result: DCOPFResult,
    *,
    bus_id: str,
    epsilon: float,
    absolute_tolerance: float = 1e-6,
    relative_tolerance: float = 1e-5,
) -> dict[str, float | str]:
    """Validate the balance-dual sign against a finite-difference objective.

    This check is intentionally explicit because a sign mistake would turn
    marginal generation costs into incorrect LMPs while still producing a
    numerically feasible dispatch.
    """

    if not math.isfinite(epsilon) or epsilon <= 0:
        raise ValueError("epsilon must be finite and positive")
    if bus_id not in base_result.balance_dual_by_bus:
        raise ValueError(f"base result has no balance dual for bus {bus_id}")
    if base_result.status not in {"optimal", "empty"} or perturbed_result.status not in {"optimal", "empty"}:
        raise ValueError("dual sign validation requires solved DCOPF results")
    finite_difference = (perturbed_result.objective_cost - base_result.objective_cost) / epsilon
    raw_dual = float(base_result.balance_dual_by_bus[bus_id])
    tolerance = absolute_tolerance + relative_tolerance * max(1.0, abs(finite_difference), abs(raw_dual))
    if abs(finite_difference - raw_dual) <= tolerance:
        sign = "positive"
    elif abs(finite_difference + raw_dual) <= tolerance:
        sign = "negative"
    else:
        raise ValueError(
            f"balance dual sign validation failed for {bus_id}: "
            f"finite_difference={finite_difference}, raw_dual={raw_dual}"
        )
    return {
        "sign": sign,
        "bus_id": bus_id,
        "epsilon": float(epsilon),
        "finite_difference": float(finite_difference),
        "raw_dual": raw_dual,
        "tolerance": float(tolerance),
    }


@dataclass(frozen=True)
class PreparedArtifact:
    """Parsed, immutable source tables reused across hourly solves."""

    buses_raw: tuple[Mapping[str, str], ...]
    demand_rows: tuple[Mapping[str, str], ...]
    generators_raw: tuple[Mapping[str, str], ...]
    branches_raw: tuple[Mapping[str, str], ...]
    dcline_raw: tuple[Mapping[str, str], ...]
    renewable_profile_mw_by_generator: Mapping[str, tuple[float, ...]]
    scenario_year: int | None = None
    scenario_block: str | None = None


@lru_cache(maxsize=8)
def _cached_network_index(
    branches: tuple[Branch, ...],
    generators: tuple[Generator, ...],
) -> tuple[dict[str, tuple[Generator, ...]], dict[str, tuple[Branch, ...]]]:
    """Index static topology objects once instead of scanning all objects per bus."""
    generators_by_bus: dict[str, list[Generator]] = {}
    for generator in generators:
        generators_by_bus.setdefault(generator.bus_id, []).append(generator)
    branches_by_bus: dict[str, list[Branch]] = {}
    for branch in branches:
        branches_by_bus.setdefault(branch.from_bus_id, []).append(branch)
        if branch.to_bus_id != branch.from_bus_id:
            branches_by_bus.setdefault(branch.to_bus_id, []).append(branch)
    return (
        {bus_id: tuple(values) for bus_id, values in generators_by_bus.items()},
        {bus_id: tuple(values) for bus_id, values in branches_by_bus.items()},
    )


def _read_csv_rows(path: Path) -> tuple[Mapping[str, str], ...]:
    with path.open(newline="", encoding="utf-8") as handle:
        return tuple(MappingProxyType(dict(row)) for row in csv.DictReader(handle))


def _read_renewable_profiles(root: Path) -> Mapping[str, tuple[float, ...]]:
    profiles: dict[str, list[float]] = {}
    for filename in ("solar.csv", "wind.csv", "hydro.csv"):
        path = root / filename
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            if len(fieldnames) < 2:
                raise ValueError(f"{filename} must contain a timestamp and at least one generator column")
            generator_ids = fieldnames[1:]
            values_by_generator = {generator_id: [] for generator_id in generator_ids}
            for row_number, row in enumerate(reader, start=2):
                for generator_id in generator_ids:
                    try:
                        value = float(row[generator_id])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError(
                            f"{filename} profile value for {generator_id} is not numeric at row {row_number}"
                        ) from exc
                    if not math.isfinite(value) or value < 0:
                        raise ValueError(
                            f"{filename} profile value for {generator_id} must be finite and non-negative"
                        )
                    values_by_generator[generator_id].append(value)
            for generator_id, values in values_by_generator.items():
                if generator_id in profiles:
                    raise ValueError(f"renewable profile is duplicated for generator {generator_id}")
                profiles[generator_id] = values
    return MappingProxyType({generator_id: tuple(values) for generator_id, values in profiles.items()})


_ARTIFACT_FILENAMES = (
    "bus.csv",
    "demand_bus.csv",
    "generator_limits.csv",
    "branch_limits.csv",
    "dcline.csv",
    "solar.csv",
    "wind.csv",
    "hydro.csv",
    "scenario_profile.json",
    "bus_load_base.csv",
    "generator_catalog.csv",
    "manifest.json",
)


def _artifact_fingerprint(root: Path) -> tuple[tuple[str, int | None, int | None, int | None, int | None], ...]:
    """Return a cheap identity for the files that define a prepared artifact.

    Device/inode, size, and nanosecond mtime detect normal in-place edits and
    atomic replacement without hashing the 200+ MB demand table on every
    hourly solve.  ``clear_prepared_artifact_cache`` is available for callers
    that deliberately replace a file while preserving all of these metadata.
    """
    fingerprint = []
    for filename in _ARTIFACT_FILENAMES:
        path = root / filename
        try:
            stat = path.stat()
        except FileNotFoundError:
            fingerprint.append((filename, None, None, None, None))
        else:
            fingerprint.append((filename, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))
    return tuple(fingerprint)


@lru_cache(maxsize=8)
def _load_prepared_artifact_cached(
    root_string: str,
    fingerprint: tuple[tuple[str, int | None, int | None, int | None, int | None], ...],
) -> PreparedArtifact:
    """Load and cache the static CSV tables for a prepared dispatch artifact."""
    root = Path(root_string)
    scenario_year = scenario_block = None
    scenario_path = root / "scenario_profile.json"
    if scenario_path.is_file():
        profile = __import__("json").loads(scenario_path.read_text(encoding="utf-8"))
        scenario_year = int(profile["year"])
        scenario_block = str(profile["calendar_block"])
    return PreparedArtifact(
        buses_raw=_read_csv_rows(root / "bus.csv"),
        demand_rows=() if scenario_path.is_file() else _read_csv_rows(root / "demand_bus.csv"),
        generators_raw=_read_csv_rows(root / "generator_limits.csv"),
        branches_raw=_read_csv_rows(root / "branch_limits.csv"),
        dcline_raw=_read_csv_rows(root / "dcline.csv"),
        renewable_profile_mw_by_generator=_read_renewable_profiles(root),
        scenario_year=scenario_year,
        scenario_block=scenario_block,
    )


def clear_prepared_artifact_cache() -> None:
    """Invalidate all cached prepared artifacts after deliberate file replacement."""
    _load_prepared_artifact_cached.cache_clear()


def load_prepared_artifact(artifact_dir: str | Path) -> PreparedArtifact:
    """Load a prepared artifact, invalidating the cache when its files change."""
    root = Path(artifact_dir).resolve()
    return _load_prepared_artifact_cached(str(root), _artifact_fingerprint(root))


# Preserve the cache-management affordance exposed by the former decorated
# public function while keeping the cache key independent of mutable callers.
load_prepared_artifact.cache_clear = clear_prepared_artifact_cache  # type: ignore[attr-defined]


def _highs():
    try:
        from highspy import Highs, HighsModelStatus, kHighsInf
    except ImportError as exc:
        raise RuntimeError(
            "DCOPF requires the optional project dependency 'highspy'; install the project dependencies first"
        ) from exc
    return Highs, HighsModelStatus, kHighsInf


def _validate(
    buses: tuple[Bus, ...], generators: tuple[Generator, ...], branches: tuple[Branch, ...]
) -> tuple[dict[str, Bus], str]:
    if not buses or len({bus.bus_id for bus in buses}) != len(buses):
        raise ValueError("bus IDs must be unique and non-empty")
    bus_by_id = {bus.bus_id: bus for bus in buses}
    references = [bus.bus_id for bus in buses if bus.reference]
    if len(references) > 1:
        raise ValueError("at most one reference bus is allowed")
    reference = references[0] if references else buses[0].bus_id
    for bus in buses:
        if not math.isfinite(bus.demand_mw) or bus.demand_mw < 0:
            raise ValueError(f"invalid demand for bus {bus.bus_id}")
    generator_ids: set[str] = set()
    for generator in generators:
        if generator.generator_id in generator_ids:
            raise ValueError(f"duplicate generator ID: {generator.generator_id}")
        generator_ids.add(generator.generator_id)
        if generator.bus_id not in bus_by_id:
            raise ValueError(f"unknown generator bus_id: {generator.bus_id}")
        if not all(math.isfinite(value) for value in (generator.p_min_mw, generator.p_max_mw, generator.marginal_cost_per_mwh)):
            raise ValueError(f"non-finite generator data: {generator.generator_id}")
        if generator.p_min_mw < 0 or generator.p_max_mw < generator.p_min_mw:
            raise ValueError(f"invalid generator limits: {generator.generator_id}")
    branch_ids: set[str] = set()
    for branch in branches:
        if branch.branch_id in branch_ids:
            raise ValueError(f"duplicate branch ID: {branch.branch_id}")
        branch_ids.add(branch.branch_id)
        if branch.from_bus_id not in bus_by_id:
            raise ValueError(f"unknown from_bus_id: {branch.from_bus_id}")
        if branch.to_bus_id not in bus_by_id:
            raise ValueError(f"unknown to_bus_id: {branch.to_bus_id}")
        if not math.isfinite(branch.susceptance_pu) or branch.susceptance_pu == 0:
            raise ValueError(f"invalid branch susceptance: {branch.branch_id}")
        if not math.isfinite(branch.thermal_limit_mw) or branch.thermal_limit_mw < 0:
            raise ValueError(f"invalid branch limit: {branch.branch_id}")
    return bus_by_id, reference


def solve_dcopf(
    *,
    buses: tuple[Bus, ...],
    generators: tuple[Generator, ...],
    branches: tuple[Branch, ...],
    fixed_injection_mw_by_bus: dict[str, float] | None = None,
    base_mva: float = 1.0,
) -> DCOPFResult:
    """Solve one fixed-load linear DCOPF period with HiGHS.

    Load is inelastic in the dispatch game. If generation, imports, and
    network limits cannot serve it, ``DCOPFInfeasible`` is raised; the solver
    does not introduce load-shedding or curtailment variables.
    """
    Highs, HighsModelStatus, highs_inf = _highs()
    bus_by_id, reference = _validate(buses, generators, branches)
    injections = fixed_injection_mw_by_bus or {}
    if set(injections) - set(bus_by_id):
        raise ValueError("fixed injection references an unknown bus")
    if any(not math.isfinite(value) for value in injections.values()):
        raise ValueError("fixed injections must be finite")
    if not math.isfinite(base_mva) or base_mva <= 0:
        raise ValueError("base_mva must be finite and positive")

    bus_ids = [bus.bus_id for bus in buses]
    non_reference = [bus_id for bus_id in bus_ids if bus_id != reference]
    generators_by_bus, branches_by_bus = _cached_network_index(branches, generators)
    # Scale the free angle variables to keep the LP coefficients well
    # conditioned.  The source artifact contains very small reactances, so
    # using raw radians with ``susceptance * base_mva`` can produce
    # coefficients spanning five orders of magnitude.  ``scaled_angle`` is
    # still an angle in radians divided by this constant; flows and returned
    # angles are converted back below.  This is numerically equivalent to
    # the unscaled DCOPF.
    angle_scale = 1000.0
    highs = Highs()
    highs.setOptionValue("output_flag", False)
    highs.setOptionValue("presolve", "on")
    highs.setOptionValue("parallel", "off")
    generator_columns: dict[str, object] = {}
    angle_columns: dict[str, object] = {reference: None}
    for generator in generators:
        generator_columns[generator.generator_id] = highs.addVariable(
            lb=generator.p_min_mw,
            ub=generator.p_max_mw,
            obj=generator.marginal_cost_per_mwh,
            name=f"gen:{generator.generator_id}",
        )
    for bus_id in non_reference:
        angle_columns[bus_id] = highs.addVariable(
            lb=-highs_inf,
            ub=highs_inf,
            obj=0.0,
            name=f"theta:{bus_id}",
        )

    def add_coefficient(coefficients: dict[int, float], variable: object, value: float) -> None:
        index = int(variable.index)
        coefficients[index] = coefficients.get(index, 0.0) + value

    balance_row_indices: dict[str, int] = {}
    for bus in buses:
        coefficients: dict[int, float] = {}
        for generator in generators_by_bus.get(bus.bus_id, ()):
            add_coefficient(coefficients, generator_columns[generator.generator_id], 1.0)
        for branch in branches_by_bus.get(bus.bus_id, ()):
            susceptance_mw_per_rad = branch.susceptance_pu * base_mva
            if branch.from_bus_id == bus.bus_id:
                if branch.from_bus_id != reference:
                    add_coefficient(coefficients, angle_columns[branch.from_bus_id], -susceptance_mw_per_rad / angle_scale)
                if branch.to_bus_id != reference:
                    add_coefficient(coefficients, angle_columns[branch.to_bus_id], susceptance_mw_per_rad / angle_scale)
            elif branch.to_bus_id == bus.bus_id:
                if branch.to_bus_id != reference:
                    add_coefficient(coefficients, angle_columns[branch.to_bus_id], -susceptance_mw_per_rad / angle_scale)
                if branch.from_bus_id != reference:
                    add_coefficient(coefficients, angle_columns[branch.from_bus_id], susceptance_mw_per_rad / angle_scale)
        rhs = bus.demand_mw - injections.get(bus.bus_id, 0.0)
        highs.addRow(rhs, rhs, len(coefficients), list(coefficients), list(coefficients.values()))
        balance_row_indices[bus.bus_id] = len(balance_row_indices)

    for branch in branches:
        coefficients: dict[int, float] = {}
        susceptance_mw_per_rad = branch.susceptance_pu * base_mva
        if branch.from_bus_id != reference:
            add_coefficient(coefficients, angle_columns[branch.from_bus_id], susceptance_mw_per_rad / angle_scale)
        if branch.to_bus_id != reference:
            add_coefficient(coefficients, angle_columns[branch.to_bus_id], -susceptance_mw_per_rad / angle_scale)
        highs.addRow(-branch.thermal_limit_mw, branch.thermal_limit_mw, len(coefficients), list(coefficients), list(coefficients.values()))

    highs.run()
    model_status = highs.getModelStatus()
    if model_status == getattr(HighsModelStatus, "kModelEmpty", None):
        balance_rhs = tuple(bus.demand_mw - injections.get(bus.bus_id, 0.0) for bus in buses)
        if all(math.isclose(value, 0.0, abs_tol=1e-9) for value in balance_rhs):
            return DCOPFResult(
                status="empty",
                objective_cost=0.0,
                generation_mw={},
                angle_by_bus={reference: 0.0},
                flow_mw_by_branch={branch.branch_id: 0.0 for branch in branches},
                balance_dual_by_bus={bus.bus_id: 0.0 for bus in buses},
            )
        raise DCOPFInfeasible(f"DCOPF physically infeasible: {model_status}")
    if model_status == getattr(HighsModelStatus, "kInfeasible", None):
        raise DCOPFInfeasible(f"DCOPF physically infeasible: {model_status}")
    if model_status != HighsModelStatus.kOptimal:
        raise DCOPFUnresolved(f"DCOPF solver unresolved: {model_status}")
    solution = highs.getSolution()
    generation = {generator_id: round(solution.col_value[int(variable.index)], 10) for generator_id, variable in generator_columns.items()}
    raw_angles = {reference: 0.0}
    raw_angles.update({
        bus_id: solution.col_value[int(variable.index)] / angle_scale
        for bus_id, variable in angle_columns.items()
        if bus_id != reference
    })
    angles = {bus_id: round(value, 10) for bus_id, value in raw_angles.items()}
    flows = {
        branch.branch_id: round(
            branch.susceptance_pu * base_mva * (raw_angles[branch.from_bus_id] - raw_angles[branch.to_bus_id]), 10
        )
        for branch in branches
    }
    duals = highs.getSolution().row_dual
    balance_duals = {bus_id: round(duals[index], 10) for bus_id, index in balance_row_indices.items()}
    return DCOPFResult(
        status="optimal",
        objective_cost=round(float(highs.getObjectiveValue()), 10),
        generation_mw=generation,
        angle_by_bus=angles,
        flow_mw_by_branch=flows,
        balance_dual_by_bus=balance_duals,
    )


def solve_prepared_period(
    artifact_dir: str | Path,
    *,
    period_index: int = 0,
    additional_demand_mw_by_bus: Mapping[str, float] | None = None,
    additional_generators: Sequence[Generator] = (),
) -> DCOPFResult:
    """Solve one hourly period from a validated prepared scenario block."""
    artifact = load_prepared_artifact(str(Path(artifact_dir).resolve()))
    if artifact.scenario_year is not None:
        from ercot_game.scenario_profiles import hourly_state
        from ercot_game.scenario_package import require_validated_block

        require_validated_block(artifact_dir)
        demand_values, availability = hourly_state(
            artifact_dir,
            year=artifact.scenario_year,
            calendar_block=artifact.scenario_block or "jan_jun",
            period_index=period_index,
        )
        demand_row = demand_values
    else:
        if not 0 <= period_index < len(artifact.demand_rows):
            raise ValueError(f"period_index out of range: {period_index}")
        demand_row = artifact.demand_rows[period_index]
        availability = {}
    submitted_demand = additional_demand_mw_by_bus or {}
    buses = tuple(
        Bus(
            row["bus_id"],
            float(demand_row[row["bus_id"]]) + float(submitted_demand.get(row["bus_id"], 0.0)),
            reference=row.get("type") == "3",
        )
        for row in artifact.buses_raw
    )
    source_generators = tuple(
        Generator(
            row["generator_id"],
            row["bus_id"],
            float(row["p_min_mw"]),
            # Classroom dispatch uses the dataset nameplate limits.  The
            # prepared artifact retains hourly renewable profiles for future
            # calibrated scenarios, but the current no-curtailment release
            # must not make the source network infeasible solely because an
            # availability profile is applied as a hard upper bound.
            min(float(row["p_max_mw"]), availability.get(row["generator_id"], float(row["p_max_mw"]))),
            float(row["marginal_cost_c1_per_mwh"]),
        )
        for row in artifact.generators_raw
        if row.get("status", "1") == "1"
    )
    branches = tuple(
        Branch(row["branch_id"], row["from_bus_id"], row["to_bus_id"], 1.0 / float(row["reactance_pu"]), float(row["thermal_limit_mva"]))
        for row in artifact.branches_raw
        if row.get("status", "1") == "1" and float(row["reactance_pu"]) != 0.0
    )
    bus_ids = {bus.bus_id for bus in buses}
    if set(submitted_demand) - bus_ids:
        raise ValueError("additional demand references an unknown bus")
    for bus_id, demand_mw in submitted_demand.items():
        if not math.isfinite(demand_mw) or demand_mw < 0:
            raise ValueError(f"additional demand must be finite and non-negative: {bus_id}")
    for generator in additional_generators:
        if generator.bus_id not in bus_ids:
            raise ValueError(f"additional generator references an unknown bus: {generator.bus_id}")
    fixed_injections: dict[str, float] = {}
    for row in artifact.dcline_raw:
        if row.get("status", "1") != "1":
            continue
        receiving_bus = row["to_bus_id"]
        if receiving_bus in bus_ids:
            fixed_injections[receiving_bus] = fixed_injections.get(receiving_bus, 0.0) + float(row["Pt"])
    return solve_dcopf(
        buses=buses,
        generators=source_generators + tuple(additional_generators),
        branches=branches,
        fixed_injection_mw_by_bus=fixed_injections,
        base_mva=100.0,
    )
