"""Scalable linear DC optimal power flow backed by HiGHS.

The model includes a single-period LP and a horizon entry point that adds
team-dedicated storage while retaining the same per-period network balance
equations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import math
from pathlib import Path
import csv
from collections.abc import Mapping, Sequence
from numbers import Real
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
    storage_charge_mw: dict[str, float] = field(default_factory=dict)
    storage_discharge_mw: dict[str, float] = field(default_factory=dict)
    storage_soc_mwh: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class StorageDispatchAsset:
    """One team-dedicated storage asset in the centralized dispatch model."""

    storage_id: str
    team_id: str
    bus_id: str
    power_mw: float
    energy_mwh: float
    initial_soc_mwh: float


def _group_storage_by_owner_bus(
    storage_assets: Sequence[StorageDispatchAsset],
) -> dict[tuple[str, str], tuple[StorageDispatchAsset, ...]]:
    """Partition storage assets by the team and bus that own them."""
    grouped: dict[tuple[str, str], list[StorageDispatchAsset]] = {}
    for storage in storage_assets:
        grouped.setdefault((storage.team_id, storage.bus_id), []).append(storage)
    return {key: tuple(values) for key, values in grouped.items()}


@dataclass(frozen=True)
class DCOPFHorizonResult:
    """One coupled multi-period DCOPF solve with intertemporal storage state."""

    status: str
    periods: tuple[DCOPFResult, ...]
    final_soc_mwh_by_storage: dict[str, float]

    @property
    def objective_cost(self) -> float:
        return sum(period.objective_cost for period in self.periods)


class DCOPFInfeasible(ValueError):
    """Raised when HiGHS reports no feasible DCOPF solution."""


class DCOPFUnresolved(RuntimeError):
    """Raised when HiGHS cannot resolve the DCOPF model status."""


def _is_finite_real(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


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
        from highspy import Highs, HighsModelStatus, HighsVarType, kHighsInf
    except ImportError as exc:
        raise RuntimeError(
            "DCOPF requires the optional project dependency 'highspy'; install the project dependencies first"
        ) from exc
    return Highs, HighsModelStatus, HighsVarType, kHighsInf


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


class ReusableDCOPFModel:
    """Reusable single-period DCOPF model for a fixed network topology.

    The HiGHS model is assembled once.  Subsequent calls update only the
    balance-row right-hand sides and re-solve the same LP.  This is intended
    for repeated hourly dispatch with unchanged generators, branches, and
    generator limits.
    """

    def __init__(
        self,
        *,
        buses: tuple[Bus, ...],
        generators: tuple[Generator, ...],
        branches: tuple[Branch, ...],
        base_mva: float = 1.0,
    ) -> None:
        if not math.isfinite(base_mva) or base_mva <= 0:
            raise ValueError("base_mva must be finite and positive")
        self._buses, self._reference = _validate(buses, generators, branches)
        self._bus_order = tuple(bus.bus_id for bus in buses)
        self._generators = tuple(generators)
        self._branches = tuple(branches)
        self._base_mva = base_mva
        self._angle_scale = 1000.0
        self._generators_by_bus, self._branches_by_bus = _cached_network_index(
            self._branches, self._generators
        )

        Highs, _HighsModelStatus, _HighsVarType, highs_inf = _highs()
        self._highs = Highs()
        self._highs.setOptionValue("output_flag", False)
        self._highs.setOptionValue("presolve", "on")
        self._highs.setOptionValue("parallel", "off")
        self._generator_columns: dict[str, object] = {}
        self._angle_columns: dict[str, object] = {self._reference: None}
        self._balance_row_indices: dict[str, int] = {}

        for generator in self._generators:
            self._generator_columns[generator.generator_id] = self._highs.addVariable(
                lb=generator.p_min_mw,
                ub=generator.p_max_mw,
                obj=generator.marginal_cost_per_mwh,
                name=f"gen:{generator.generator_id}",
            )
        for bus_id in self._bus_order:
            if bus_id != self._reference:
                self._angle_columns[bus_id] = self._highs.addVariable(
                    lb=-highs_inf,
                    ub=highs_inf,
                    obj=0.0,
                    name=f"theta:{bus_id}",
                )

        def add_coefficient(coefficients: dict[int, float], variable: object, value: float) -> None:
            index = int(variable.index)
            coefficients[index] = coefficients.get(index, 0.0) + value

        for bus_id in self._bus_order:
            coefficients: dict[int, float] = {}
            for generator in self._generators_by_bus.get(bus_id, ()):
                add_coefficient(coefficients, self._generator_columns[generator.generator_id], 1.0)
            for branch in self._branches_by_bus.get(bus_id, ()):
                susceptance = branch.susceptance_pu * self._base_mva
                if branch.from_bus_id == bus_id:
                    if branch.from_bus_id != self._reference:
                        add_coefficient(coefficients, self._angle_columns[branch.from_bus_id], -susceptance / self._angle_scale)
                    if branch.to_bus_id != self._reference:
                        add_coefficient(coefficients, self._angle_columns[branch.to_bus_id], susceptance / self._angle_scale)
                elif branch.to_bus_id == bus_id:
                    if branch.to_bus_id != self._reference:
                        add_coefficient(coefficients, self._angle_columns[branch.to_bus_id], -susceptance / self._angle_scale)
                    if branch.from_bus_id != self._reference:
                        add_coefficient(coefficients, self._angle_columns[branch.from_bus_id], susceptance / self._angle_scale)
            self._highs.addRow(
                0.0,
                0.0,
                len(coefficients),
                list(coefficients),
                list(coefficients.values()),
            )
            self._balance_row_indices[bus_id] = len(self._balance_row_indices)

        for branch in self._branches:
            coefficients: dict[int, float] = {}
            susceptance = branch.susceptance_pu * self._base_mva
            if branch.from_bus_id != self._reference:
                add_coefficient(coefficients, self._angle_columns[branch.from_bus_id], susceptance / self._angle_scale)
            if branch.to_bus_id != self._reference:
                add_coefficient(coefficients, self._angle_columns[branch.to_bus_id], -susceptance / self._angle_scale)
            self._highs.addRow(
                -branch.thermal_limit_mw,
                branch.thermal_limit_mw,
                len(coefficients),
                list(coefficients),
                list(coefficients.values()),
            )

    def solve(
        self,
        buses: tuple[Bus, ...],
        fixed_injection_mw_by_bus: Mapping[str, float] | None = None,
        *,
        return_result: bool = True,
    ) -> DCOPFResult | str:
        if tuple(bus.bus_id for bus in buses) != self._bus_order:
            raise ValueError("topology inputs must match the reusable model")
        if any(not math.isfinite(bus.demand_mw) or bus.demand_mw < 0 for bus in buses):
            raise ValueError("invalid demand in reusable DCOPF solve")
        injections = dict(fixed_injection_mw_by_bus or {})
        if set(injections) - set(self._bus_order):
            raise ValueError("fixed injection references an unknown bus")
        if any(not math.isfinite(value) for value in injections.values()):
            raise ValueError("fixed injections must be finite")
        for bus in buses:
            rhs = bus.demand_mw - injections.get(bus.bus_id, 0.0)
            self._highs.changeRowBounds(self._balance_row_indices[bus.bus_id], rhs, rhs)

        self._highs.run()
        model_status = self._highs.getModelStatus()
        _Highs, HighsModelStatus, _HighsVarType, _highs_inf = _highs()
        if model_status == getattr(HighsModelStatus, "kInfeasible", None):
            raise DCOPFInfeasible(f"DCOPF physically infeasible: {model_status}")
        if model_status != HighsModelStatus.kOptimal:
            raise DCOPFUnresolved(f"DCOPF solver unresolved: {model_status}")
        if not return_result:
            return "optimal"

        solution = self._highs.getSolution()

        def value(variable: object) -> float:
            return float(solution.col_value[int(variable.index)])

        generation = {
            generator_id: round(value(variable), 10)
            for generator_id, variable in self._generator_columns.items()
        }
        raw_angles = {self._reference: 0.0}
        raw_angles.update({
            bus_id: value(variable) / self._angle_scale
            for bus_id, variable in self._angle_columns.items()
            if bus_id != self._reference
        })
        angles = {bus_id: round(angle, 10) for bus_id, angle in raw_angles.items()}
        flows = {
            branch.branch_id: round(
                branch.susceptance_pu * self._base_mva
                * (raw_angles[branch.from_bus_id] - raw_angles[branch.to_bus_id]),
                10,
            )
            for branch in self._branches
        }
        balance_duals = {
            bus_id: round(self._highs.getSolution().row_dual[row_index], 10)
            for bus_id, row_index in self._balance_row_indices.items()
        }
        return DCOPFResult(
            status="optimal",
            objective_cost=round(float(self._highs.getObjectiveValue()), 10),
            generation_mw=generation,
            angle_by_bus=angles,
            flow_mw_by_branch=flows,
            balance_dual_by_bus=balance_duals,
        )


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
    Highs, HighsModelStatus, _HighsVarType, highs_inf = _highs()
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


def solve_dcopf_horizon(
    *,
    periods: Sequence[tuple[tuple[Bus, ...], tuple[Generator, ...], tuple[Branch, ...]]],
    storage_assets: Sequence[StorageDispatchAsset] = (),
    fixed_injection_mw_by_period: Sequence[Mapping[str, float]] | None = None,
    owner_load_mw_by_team: Mapping[str, Sequence[Mapping[str, float]]] | None = None,
    time_step_hours: float = 1.0,
    base_mva: float = 1.0,
) -> DCOPFHorizonResult:
    """Solve a coupled multi-period DCOPF with team-dedicated storage.

    The network and generator constraints are the same linear DCOPF used by
    :func:`solve_dcopf`.  Storage charge/discharge and SOC variables are added
    to one coupled HiGHS model, with a binary operating-mode variable per
    asset/period.  This makes storage-bearing horizons mixed-integer models,
    but exactly prevents simultaneous positive charge and discharge while
    preserving the existing result maps.  Persisted initial SOC and terminal
    SOC are both constrained to the half-full-to-full interval.  Owner load is
    required for storage-bearing solves, and each team's total discharge is
    capped separately at each owned storage bus by that team's load at that
    same bus and period.
    """
    if not periods:
        raise ValueError("periods must not be empty")
    if not _is_finite_real(time_step_hours) or time_step_hours <= 0:
        raise ValueError("time_step_hours must be finite and positive")
    if not _is_finite_real(base_mva) or base_mva <= 0:
        raise ValueError("base_mva must be finite and positive")

    try:
        period_data = tuple(periods)
    except TypeError as exc:
        raise ValueError("periods must be a sequence of (buses, generators, branches) triples") from exc
    period_count = len(period_data)
    for period in period_data:
        if isinstance(period, (str, bytes)) or not isinstance(period, Sequence) or len(period) != 3:
            raise ValueError("periods must contain (buses, generators, branches) triples")
    if fixed_injection_mw_by_period is None:
        fixed_injections = ({},) * period_count
    else:
        try:
            fixed_injections = tuple(fixed_injection_mw_by_period)
        except TypeError as exc:
            raise ValueError("fixed injections must be a sequence of period mappings") from exc
        if len(fixed_injections) != period_count:
            raise ValueError("fixed injections must match the number of periods")
        if any(not isinstance(injections, Mapping) for injections in fixed_injections):
            raise ValueError("fixed injections must contain mappings")

    storage_assets = tuple(storage_assets)
    if storage_assets and owner_load_mw_by_team is None:
        raise ValueError("owner load is required when storage assets are present")
    storage_by_id: dict[str, StorageDispatchAsset] = {}
    for storage in storage_assets:
        if not isinstance(storage, StorageDispatchAsset):
            raise ValueError("storage_assets must contain StorageDispatchAsset values")
        if not storage.storage_id or storage.storage_id in storage_by_id:
            raise ValueError(f"storage IDs must be unique and non-empty: {storage.storage_id}")
        if not storage.team_id or not storage.bus_id:
            raise ValueError(f"storage {storage.storage_id} must identify a team and bus")
        if (
            not all(
                math.isfinite(value)
                for value in (
                    storage.power_mw,
                    storage.energy_mwh,
                    storage.initial_soc_mwh,
                )
            )
            or storage.power_mw <= 0
            or storage.energy_mwh <= 0
            or storage.initial_soc_mwh < storage.energy_mwh * 0.5
            or storage.initial_soc_mwh > storage.energy_mwh
        ):
            raise ValueError(f"invalid storage dimensions or initial SOC: {storage.storage_id}")
        storage_by_id[storage.storage_id] = storage

    owner_loads: dict[str, tuple[Mapping[str, float], ...]] = {}
    if owner_load_mw_by_team is not None:
        if not isinstance(owner_load_mw_by_team, Mapping):
            raise ValueError("owner_load_mw_by_team must be a mapping")
        for team_id, values in owner_load_mw_by_team.items():
            if not isinstance(team_id, str) or not team_id:
                raise ValueError("owner load team IDs must be non-empty strings")
            if values is None or isinstance(values, (str, bytes)):
                raise ValueError(f"owner load for {team_id} must be a sequence of period mappings")
            try:
                values = tuple(values)
            except TypeError as exc:
                raise ValueError(
                    f"owner load for {team_id} must be a sequence of period mappings"
                ) from exc
            if len(values) != period_count:
                raise ValueError(f"owner load for {team_id} must match the number of periods")
            normalized: list[Mapping[str, float]] = []
            for period_load in values:
                if not isinstance(period_load, Mapping):
                    raise ValueError(f"owner load for {team_id} must contain mappings")
                checked: dict[str, float] = {}
                for bus_id, amount in period_load.items():
                    if not isinstance(bus_id, str) or not _is_finite_real(amount) or amount < 0:
                        raise ValueError(f"owner load for {team_id} must be finite and non-negative")
                    checked[bus_id] = float(amount)
                normalized.append(checked)
            owner_loads[team_id] = tuple(normalized)
        missing_owners = sorted(
            {storage.team_id for storage in storage_assets} - set(owner_loads)
        )
        if missing_owners:
            raise ValueError(
                "owner load is required for storage teams: " + ", ".join(missing_owners)
            )

    model_inputs: list[tuple[dict[str, Bus], str, tuple[Generator, ...], tuple[Branch, ...], dict[str, float]]] = []
    for period_index, (buses, generators, branches) in enumerate(period_data):
        bus_by_id, reference = _validate(buses, generators, branches)
        injections = dict(fixed_injections[period_index])
        if set(injections) - set(bus_by_id):
            raise ValueError("fixed injection references an unknown bus")
        if any(not _is_finite_real(value) for value in injections.values()):
            raise ValueError("fixed injections must be finite")
        for storage in storage_assets:
            if storage.bus_id not in bus_by_id:
                raise ValueError(f"storage {storage.storage_id} references an unknown bus")
        if owner_load_mw_by_team is not None:
            for team_id, period_load in owner_loads.items():
                if set(period_load[period_index]) - set(bus_by_id):
                    raise ValueError(f"owner load for {team_id} references an unknown bus")
        model_inputs.append((bus_by_id, reference, tuple(generators), tuple(branches), injections))

    Highs, HighsModelStatus, HighsVarType, highs_inf = _highs()
    highs = Highs()
    highs.setOptionValue("output_flag", False)
    highs.setOptionValue("presolve", "on")
    highs.setOptionValue("parallel", "off")
    angle_scale = 1000.0
    generator_columns: dict[tuple[int, str], object] = {}
    angle_columns: dict[tuple[int, str], object] = {}
    charge_columns: dict[tuple[int, str], object] = {}
    discharge_columns: dict[tuple[int, str], object] = {}
    mode_columns: dict[tuple[int, str], object] = {}
    soc_columns: dict[tuple[int, str], object] = {}
    balance_row_indices: dict[tuple[int, str], int] = {}

    def add_coefficient(coefficients: dict[int, float], variable: object, value: float) -> None:
        index = int(variable.index)
        coefficients[index] = coefficients.get(index, 0.0) + value

    for period_index, (_bus_by_id, reference, generators, _branches, _injections) in enumerate(model_inputs):
        for generator in generators:
            generator_columns[(period_index, generator.generator_id)] = highs.addVariable(
                lb=generator.p_min_mw,
                ub=generator.p_max_mw,
                obj=generator.marginal_cost_per_mwh,
                name=f"gen:{period_index}:{generator.generator_id}",
            )
        for bus_id in _bus_by_id:
            if bus_id != reference:
                angle_columns[(period_index, bus_id)] = highs.addVariable(
                    lb=-highs_inf,
                    ub=highs_inf,
                    obj=0.0,
                    name=f"theta:{period_index}:{bus_id}",
                )

    for storage in storage_assets:
        for period_index in range(period_count):
            charge_columns[(period_index, storage.storage_id)] = highs.addVariable(
                lb=0.0,
                ub=storage.power_mw,
                obj=0.0,
                name=f"storage-charge:{period_index}:{storage.storage_id}",
            )
            discharge_columns[(period_index, storage.storage_id)] = highs.addVariable(
                lb=0.0,
                ub=storage.power_mw,
                obj=0.0,
                name=f"storage-discharge:{period_index}:{storage.storage_id}",
            )
            mode_columns[(period_index, storage.storage_id)] = highs.addVariable(
                # HiGHS integer variables plus [0, 1] bounds provide a binary
                # mode without changing the public charge/discharge contract.
                lb=0.0,
                ub=1.0,
                type=HighsVarType.kInteger,
                name=f"storage-mode:{period_index}:{storage.storage_id}",
            )
            soc_columns[(period_index, storage.storage_id)] = highs.addVariable(
                lb=storage.energy_mwh * 0.5 if period_index == period_count - 1 else 0.0,
                ub=storage.energy_mwh,
                obj=0.0,
                name=f"storage-soc:{period_index}:{storage.storage_id}",
            )

    storage_by_bus: dict[str, tuple[StorageDispatchAsset, ...]] = {}
    for bus_id in {storage.bus_id for storage in storage_assets}:
        storage_by_bus[bus_id] = tuple(
            storage for storage in storage_assets if storage.bus_id == bus_id
        )

    for period_index, (_bus_by_id, reference, generators, branches, injections) in enumerate(model_inputs):
        generators_by_bus, branches_by_bus = _cached_network_index(branches, generators)
        for bus_id, bus in _bus_by_id.items():
            coefficients: dict[int, float] = {}
            for generator in generators_by_bus.get(bus_id, ()):
                add_coefficient(coefficients, generator_columns[(period_index, generator.generator_id)], 1.0)
            for storage in storage_by_bus.get(bus_id, ()):
                add_coefficient(coefficients, discharge_columns[(period_index, storage.storage_id)], 1.0)
                add_coefficient(coefficients, charge_columns[(period_index, storage.storage_id)], -1.0)
            for branch in branches_by_bus.get(bus_id, ()):
                susceptance_mw_per_rad = branch.susceptance_pu * base_mva
                if branch.from_bus_id == bus_id:
                    if branch.from_bus_id != reference:
                        add_coefficient(coefficients, angle_columns[(period_index, branch.from_bus_id)], -susceptance_mw_per_rad / angle_scale)
                    if branch.to_bus_id != reference:
                        add_coefficient(coefficients, angle_columns[(period_index, branch.to_bus_id)], susceptance_mw_per_rad / angle_scale)
                elif branch.to_bus_id == bus_id:
                    if branch.to_bus_id != reference:
                        add_coefficient(coefficients, angle_columns[(period_index, branch.to_bus_id)], -susceptance_mw_per_rad / angle_scale)
                    if branch.from_bus_id != reference:
                        add_coefficient(coefficients, angle_columns[(period_index, branch.from_bus_id)], susceptance_mw_per_rad / angle_scale)
            rhs = bus.demand_mw - injections.get(bus_id, 0.0)
            highs.addRow(rhs, rhs, len(coefficients), list(coefficients), list(coefficients.values()))
            balance_row_indices[(period_index, bus_id)] = len(balance_row_indices)

        for branch in branches:
            coefficients: dict[int, float] = {}
            susceptance_mw_per_rad = branch.susceptance_pu * base_mva
            if branch.from_bus_id != reference:
                add_coefficient(coefficients, angle_columns[(period_index, branch.from_bus_id)], susceptance_mw_per_rad / angle_scale)
            if branch.to_bus_id != reference:
                add_coefficient(coefficients, angle_columns[(period_index, branch.to_bus_id)], -susceptance_mw_per_rad / angle_scale)
            highs.addRow(-branch.thermal_limit_mw, branch.thermal_limit_mw, len(coefficients), list(coefficients), list(coefficients.values()))

        for storage in storage_assets:
            mode = mode_columns[(period_index, storage.storage_id)]
            charge_mode_coefficients = {
                int(charge_columns[(period_index, storage.storage_id)].index): 1.0,
                int(mode.index): storage.power_mw,
            }
            highs.addRow(
                -highs_inf,
                storage.power_mw,
                len(charge_mode_coefficients),
                list(charge_mode_coefficients),
                list(charge_mode_coefficients.values()),
            )
            discharge_mode_coefficients = {
                int(discharge_columns[(period_index, storage.storage_id)].index): 1.0,
                int(mode.index): -storage.power_mw,
            }
            highs.addRow(
                -highs_inf,
                0.0,
                len(discharge_mode_coefficients),
                list(discharge_mode_coefficients),
                list(discharge_mode_coefficients.values()),
            )
            coefficients = {
                int(soc_columns[(period_index, storage.storage_id)].index): 1.0,
                int(charge_columns[(period_index, storage.storage_id)].index): -time_step_hours,
                int(discharge_columns[(period_index, storage.storage_id)].index): time_step_hours,
            }
            if period_index > 0:
                add_coefficient(coefficients, soc_columns[(period_index - 1, storage.storage_id)], -1.0)
                rhs_lower = rhs_upper = 0.0
            else:
                rhs_lower = rhs_upper = storage.initial_soc_mwh
            highs.addRow(rhs_lower, rhs_upper, len(coefficients), list(coefficients), list(coefficients.values()))

            discharge_coefficients = {
                int(discharge_columns[(period_index, storage.storage_id)].index): time_step_hours,
            }
            if period_index > 0:
                add_coefficient(discharge_coefficients, soc_columns[(period_index - 1, storage.storage_id)], -1.0)
                discharge_rhs = 0.0
            else:
                discharge_rhs = storage.initial_soc_mwh
            highs.addRow(-highs_inf, discharge_rhs, len(discharge_coefficients), list(discharge_coefficients), list(discharge_coefficients.values()))

        for (team_id, bus_id), team_bus_storages in _group_storage_by_owner_bus(storage_assets).items():
            coefficients = {
                int(discharge_columns[(period_index, storage.storage_id)].index): 1.0
                for storage in team_bus_storages
            }
            owner_load = owner_loads[team_id][period_index].get(bus_id, 0.0)
            highs.addRow(-highs_inf, owner_load, len(coefficients), list(coefficients), list(coefficients.values()))

    highs.run()
    model_status = highs.getModelStatus()
    if model_status == getattr(HighsModelStatus, "kInfeasible", None):
        raise DCOPFInfeasible(f"DCOPF horizon physically infeasible: {model_status}")
    if model_status != HighsModelStatus.kOptimal:
        raise DCOPFUnresolved(f"DCOPF horizon solver unresolved: {model_status}")

    solution = highs.getSolution()

    def value(variable: object) -> float:
        return float(solution.col_value[int(variable.index)])

    results: list[DCOPFResult] = []
    for period_index, (_bus_by_id, reference, generators, branches, _injections) in enumerate(model_inputs):
        generation = {
            generator.generator_id: round(value(generator_columns[(period_index, generator.generator_id)]), 10)
            for generator in generators
        }
        raw_angles = {reference: 0.0}
        raw_angles.update({
            bus_id: value(angle_columns[(period_index, bus_id)]) / angle_scale
            for bus_id in _bus_by_id
            if bus_id != reference
        })
        angles = {bus_id: round(angle, 10) for bus_id, angle in raw_angles.items()}
        flows = {
            branch.branch_id: round(
                branch.susceptance_pu * base_mva * (raw_angles[branch.from_bus_id] - raw_angles[branch.to_bus_id]),
                10,
            )
            for branch in branches
        }
        balance_duals = {
            bus_id: round(solution.row_dual[row_index], 10)
            for (row_period, bus_id), row_index in balance_row_indices.items()
            if row_period == period_index
        }
        charge = {
            storage.storage_id: round(value(charge_columns[(period_index, storage.storage_id)]), 10)
            for storage in storage_assets
        }
        discharge = {
            storage.storage_id: round(value(discharge_columns[(period_index, storage.storage_id)]), 10)
            for storage in storage_assets
        }
        soc = {
            storage.storage_id: round(value(soc_columns[(period_index, storage.storage_id)]), 10)
            for storage in storage_assets
        }
        objective = sum(
            generation[generator.generator_id] * generator.marginal_cost_per_mwh
            for generator in generators
        )
        results.append(
            DCOPFResult(
                status="optimal",
                objective_cost=round(objective, 10),
                generation_mw=generation,
                angle_by_bus=angles,
                flow_mw_by_branch=flows,
                balance_dual_by_bus=balance_duals,
                storage_charge_mw=charge,
                storage_discharge_mw=discharge,
                storage_soc_mwh=soc,
            )
        )

    final_soc = {
        storage.storage_id: results[-1].storage_soc_mwh[storage.storage_id]
        for storage in storage_assets
    }
    return DCOPFHorizonResult(
        status="optimal",
        periods=tuple(results),
        final_soc_mwh_by_storage=final_soc,
    )


def _prepared_period_inputs(
    artifact_dir: str | Path,
    artifact: PreparedArtifact,
    *,
    period_index: int,
    additional_demand_mw_by_bus: Mapping[str, float] | None = None,
    additional_generators: Sequence[Generator] = (),
) -> tuple[tuple[Bus, ...], tuple[Generator, ...], tuple[Branch, ...], dict[str, float]]:
    """Build one prepared period without solving it."""
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
    return buses, source_generators + tuple(additional_generators), branches, fixed_injections


def solve_prepared_period(
    artifact_dir: str | Path,
    *,
    period_index: int = 0,
    additional_demand_mw_by_bus: Mapping[str, float] | None = None,
    additional_generators: Sequence[Generator] = (),
) -> DCOPFResult:
    """Solve one hourly period from a validated prepared scenario block."""
    artifact = load_prepared_artifact(str(Path(artifact_dir).resolve()))
    buses, generators, branches, fixed_injections = _prepared_period_inputs(
        artifact_dir,
        artifact,
        period_index=period_index,
        additional_demand_mw_by_bus=additional_demand_mw_by_bus,
        additional_generators=additional_generators,
    )
    return solve_dcopf(
        buses=buses,
        generators=generators,
        branches=branches,
        fixed_injection_mw_by_bus=fixed_injections,
        base_mva=100.0,
    )


def solve_prepared_horizon(
    artifact_dir: str | Path,
    *,
    period_indices: Sequence[int],
    additional_demand_mw_by_period: Sequence[Mapping[str, float]] | None = None,
    additional_generators_by_period: Sequence[Sequence[Generator]] | None = None,
    storage_assets: Sequence[StorageDispatchAsset] = (),
    owner_load_mw_by_team: Mapping[str, Sequence[Mapping[str, float]]] | None = None,
    time_step_hours: float = 1.0,
) -> DCOPFHorizonResult:
    """Solve prepared periods in one storage-coupled DCOPF model."""
    period_indices = tuple(period_indices)
    if not period_indices:
        raise ValueError("period_indices must not be empty")
    if any(not isinstance(index, int) or isinstance(index, bool) or index < 0 for index in period_indices):
        raise ValueError("period_indices must contain non-negative integers")
    period_count = len(period_indices)
    if additional_demand_mw_by_period is None:
        demands = ({},) * period_count
    else:
        demands = tuple(additional_demand_mw_by_period)
        if len(demands) != period_count:
            raise ValueError("additional demand must match the number of periods")
    if additional_generators_by_period is None:
        generators_by_period = ((),) * period_count
    else:
        generators_by_period = tuple(additional_generators_by_period)
        if len(generators_by_period) != period_count:
            raise ValueError("additional generators must match the number of periods")

    artifact = load_prepared_artifact(str(Path(artifact_dir).resolve()))
    prepared_periods = tuple(
        _prepared_period_inputs(
            artifact_dir,
            artifact,
            period_index=period_index,
            additional_demand_mw_by_bus=demands[offset],
            additional_generators=generators_by_period[offset],
        )
        for offset, period_index in enumerate(period_indices)
    )
    return solve_dcopf_horizon(
        periods=tuple((buses, generators, branches) for buses, generators, branches, _ in prepared_periods),
        storage_assets=storage_assets,
        fixed_injection_mw_by_period=tuple(fixed for _, _, _, fixed in prepared_periods),
        owner_load_mw_by_team=owner_load_mw_by_team,
        time_step_hours=time_step_hours,
        base_mva=100.0,
    )
