"""Scalable linear DC optimal power flow backed by HiGHS.

The model includes a single-period LP and a horizon entry point that adds
team-dedicated storage while retaining the same per-period network balance
equations.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import math
from pathlib import Path
import csv
import threading
import time
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
class DCOPFRecourseSummary:
    """Minimal solved-period data needed by decomposition algorithms."""

    status: str
    objective_cost: float
    balance_dual_by_bus: dict[str, float]


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
    diagnostics: "DCOPFBendersDiagnostics | None" = None

    @property
    def objective_cost(self) -> float:
        return sum(period.objective_cost for period in self.periods)


@dataclass(frozen=True)
class DCOPFBendersDiagnostics:
    """Instructor-facing diagnostics for an exact Benders horizon solve."""

    route: str = "benders"
    solver_backend: str = "highs"
    iterations: int = 0
    recourse_solves: int = 0
    optimality_cuts: int = 0
    feasibility_cuts: int = 0
    lower_bound: float | None = None
    upper_bound: float | None = None
    relative_gap: float | None = None
    elapsed_seconds: float = 0.0
    preparation_seconds: float = 0.0
    initial_recourse_seconds: float = 0.0
    candidate_recourse_seconds: float = 0.0
    finalization_seconds: float = 0.0
    recourse_cache_hits: int = 0


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
        enable_injection_probes: bool = False,
        injection_probe_bus_id: str | None = None,
        enable_candidate_demand_probes: bool = False,
        candidate_demand_bus_id: str | None = None,
        solver_threads: int = 1,
    ) -> None:
        if not math.isfinite(base_mva) or base_mva <= 0:
            raise ValueError("base_mva must be finite and positive")
        if not isinstance(solver_threads, int) or isinstance(solver_threads, bool) or solver_threads <= 0:
            raise ValueError("solver_threads must be a positive integer")
        self._buses, self._reference = _validate(buses, generators, branches)
        self._bus_order = tuple(bus.bus_id for bus in buses)
        if injection_probe_bus_id is not None and injection_probe_bus_id not in self._bus_order:
            raise ValueError(f"unknown injection probe bus: {injection_probe_bus_id}")
        if injection_probe_bus_id is not None:
            enable_injection_probes = True
        if candidate_demand_bus_id is not None and candidate_demand_bus_id not in self._bus_order:
            raise ValueError(f"unknown candidate demand bus: {candidate_demand_bus_id}")
        if candidate_demand_bus_id is not None:
            enable_candidate_demand_probes = True
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
        self._highs.setOptionValue("parallel", "on" if solver_threads > 1 else "off")
        if solver_threads > 1:
            self._highs.setOptionValue("threads", solver_threads)
        self._generator_columns: dict[str, object] = {}
        self._angle_columns: dict[str, object] = {self._reference: None}
        self._balance_row_indices: dict[str, int] = {}
        self._injection_probe_columns: dict[str, object] = {}
        self._candidate_demand_columns: dict[str, object] = {}

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
        candidate_bus_ids = ()
        if enable_candidate_demand_probes:
            candidate_bus_ids = (
                (candidate_demand_bus_id,)
                if candidate_demand_bus_id is not None
                else self._bus_order
            )
        for bus_id in candidate_bus_ids:
            self._candidate_demand_columns[bus_id] = self._highs.addVariable(
                lb=0.0,
                ub=0.0,
                obj=0.0,
                name=f"candidate-demand:{bus_id}",
            )

        def add_coefficient(coefficients: dict[int, float], variable: object, value: float) -> None:
            index = int(variable.index)
            coefficients[index] = coefficients.get(index, 0.0) + value

        for bus_id in self._bus_order:
            coefficients: dict[int, float] = {}
            if enable_injection_probes and (
                injection_probe_bus_id is None or bus_id == injection_probe_bus_id
            ):
                probe = self._highs.addVariable(
                    lb=0.0, ub=0.0, obj=0.0, name=f"injection-probe:{bus_id}"
                )
                self._injection_probe_columns[bus_id] = probe
                add_coefficient(coefficients, probe, 1.0)
            candidate = self._candidate_demand_columns.get(bus_id)
            if candidate is not None:
                add_coefficient(coefficients, candidate, -1.0)
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

    def max_additional_demand(
        self,
        buses: tuple[Bus, ...],
        bus_id: str,
        *,
        upper_bound_mw: float | None = None,
        fixed_injection_mw_by_bus: Mapping[str, float] | None = None,
    ) -> tuple[float, str]:
        """Return the exact maximum additional load at one bus.

        Candidate demand is represented by a dedicated nonnegative LP column
        with coefficient ``-1`` in the selected balance equation.  The base
        case is checked first, then the candidate column is maximized while
        every other probe column remains fixed at zero.
        """
        if tuple(bus.bus_id for bus in buses) != self._bus_order:
            raise ValueError("topology inputs must match the reusable model")
        if bus_id not in self._balance_row_indices:
            raise ValueError(f"unknown candidate demand bus: {bus_id}")
        candidate = self._candidate_demand_columns.get(bus_id)
        if candidate is None:
            raise ValueError("candidate demand probes were not enabled for this reusable model")
        if upper_bound_mw is not None and (
            not _is_finite_real(upper_bound_mw) or upper_bound_mw < 0
        ):
            raise ValueError("upper_bound_mw must be finite and non-negative")
        demand_values = {bus.bus_id: bus.demand_mw for bus in buses}
        if any(not _is_finite_real(value) or value < 0 for value in demand_values.values()):
            raise ValueError("invalid demand in reusable DCOPF solve")
        if fixed_injection_mw_by_bus is not None and not isinstance(fixed_injection_mw_by_bus, Mapping):
            raise ValueError("fixed injections must be a mapping")
        injections = dict(fixed_injection_mw_by_bus or {})
        if set(injections) - set(self._bus_order):
            raise ValueError("fixed injection references an unknown bus")
        if any(not _is_finite_real(value) for value in injections.values()):
            raise ValueError("fixed injections must be finite")
        for model_bus_id in self._bus_order:
            rhs = demand_values[model_bus_id] - injections.get(model_bus_id, 0.0)
            self._highs.changeRowBounds(self._balance_row_indices[model_bus_id], rhs, rhs)

        candidate_indices = {
            model_bus_id: int(variable.index)
            for model_bus_id, variable in self._candidate_demand_columns.items()
        }
        generator_costs = {
            int(variable.index): generator.marginal_cost_per_mwh
            for generator in self._generators
            for variable in (self._generator_columns[generator.generator_id],)
        }
        _Highs, HighsModelStatus, _HighsVarType, highs_inf = _highs()

        def candidate_status(model_status: object, *, base_case: bool) -> str:
            if model_status == HighsModelStatus.kOptimal:
                return "optimal"
            status_name = getattr(model_status, "name", str(model_status)).lower()
            if "infeasible" in status_name:
                return "infeasible_base_case" if base_case else "solver_error"
            if "unbounded" in status_name:
                return "unbounded"
            if "numerical" in status_name:
                return "numerical_failure"
            return "solver_error"

        try:
            for index in candidate_indices.values():
                self._highs.changeColBounds(index, 0.0, 0.0)
                self._highs.changeColCost(index, 0.0)
            self._highs.run()
            base_status = self._highs.getModelStatus()
            base_result = candidate_status(base_status, base_case=True)
            if base_result != "optimal":
                return math.nan, base_result

            for index in generator_costs:
                self._highs.changeColCost(index, 0.0)
            candidate_index = candidate_indices[bus_id]
            self._highs.changeColBounds(
                candidate_index,
                0.0,
                highs_inf if upper_bound_mw is None else float(upper_bound_mw),
            )
            self._highs.changeColCost(candidate_index, -1.0)
            self._highs.run()
            probe_status = self._highs.getModelStatus()
            result_status = candidate_status(probe_status, base_case=False)
            if result_status != "optimal":
                return math.nan, result_status
            return float(self._highs.getSolution().col_value[candidate_index]), "optimal"
        finally:
            for index, cost in generator_costs.items():
                self._highs.changeColCost(index, cost)
            for index in candidate_indices.values():
                self._highs.changeColCost(index, 0.0)
                self._highs.changeColBounds(index, 0.0, 0.0)

    def feasible_injection_interval(
        self,
        buses: tuple[Bus, ...],
        bus_id: str,
        *,
        lower_bound: float,
        upper_bound: float,
        fixed_injection_mw_by_bus: Mapping[str, float] | None = None,
    ) -> tuple[float, float]:
        """Return the feasible interval for one variable bus injection.

        The probe is added as a column to the fixed network LP and enters the
        selected bus's balance equation with coefficient ``+1``.  Two direct
        LP solves minimize and maximize that probe, replacing the repeated
        feasibility bisection previously used by Benders decomposition.  The
        probe is fixed back to zero and has zero objective cost before this
        method returns, so subsequent ordinary dispatch solves are unchanged.
        """
        if tuple(bus.bus_id for bus in buses) != self._bus_order:
            raise ValueError("topology inputs must match the reusable model")
        if bus_id not in self._balance_row_indices:
            raise ValueError(f"unknown injection bus: {bus_id}")
        if not _is_finite_real(lower_bound) or not _is_finite_real(upper_bound):
            raise ValueError("injection bounds must be finite")
        if lower_bound > upper_bound:
            raise ValueError("lower_bound must not exceed upper_bound")
        if any(not math.isfinite(bus.demand_mw) or bus.demand_mw < 0 for bus in buses):
            raise ValueError("invalid demand in reusable DCOPF solve")
        injections = dict(fixed_injection_mw_by_bus or {})
        if set(injections) - set(self._bus_order):
            raise ValueError("fixed injection references an unknown bus")
        if any(not _is_finite_real(value) for value in injections.values()):
            raise ValueError("fixed injections must be finite")
        for bus in buses:
            rhs = bus.demand_mw - injections.get(bus.bus_id, 0.0)
            self._highs.changeRowBounds(self._balance_row_indices[bus.bus_id], rhs, rhs)

        probe = self._injection_probe_columns.get(bus_id)
        if probe is None:
            raise ValueError("injection probes were not enabled for this reusable model")

        probe_index = int(probe.index)
        self._highs.changeColBounds(probe_index, lower_bound, upper_bound)
        generator_costs = {
            int(variable.index): generator.marginal_cost_per_mwh
            for generator in self._generators
            for variable in (self._generator_columns[generator.generator_id],)
        }
        for column_index in generator_costs:
            self._highs.changeColCost(column_index, 0.0)

        def optimize_probe(cost: float) -> float:
            self._highs.changeColCost(probe_index, cost)
            self._highs.run()
            _Highs, HighsModelStatus, _HighsVarType, _highs_inf = _highs()
            model_status = self._highs.getModelStatus()
            if model_status == getattr(HighsModelStatus, "kInfeasible", None):
                raise DCOPFInfeasible(f"DCOPF injection interval is infeasible: {model_status}")
            if model_status != HighsModelStatus.kOptimal:
                raise DCOPFUnresolved(f"DCOPF injection interval unresolved: {model_status}")
            return float(self._highs.getSolution().col_value[probe_index])

        try:
            minimum = optimize_probe(1.0)
            maximum = optimize_probe(-1.0)
        finally:
            for column_index, cost in generator_costs.items():
                self._highs.changeColCost(column_index, cost)
            self._highs.changeColCost(probe_index, 0.0)
            self._highs.changeColBounds(probe_index, 0.0, 0.0)
        return minimum, maximum

    def set_time_limit_seconds(self, seconds: float) -> None:
        """Set the HiGHS limit for the next solve on this reusable model."""
        if not _is_finite_real(seconds) or seconds < 0:
            raise ValueError("solver time limit must be finite and non-negative")
        self._highs.setOptionValue("time_limit", float(seconds))

    def solve(
        self,
        buses: tuple[Bus, ...] | None,
        fixed_injection_mw_by_bus: Mapping[str, float] | None = None,
        *,
        return_result: bool = True,
        return_summary: bool = False,
        demand_mw_by_bus: Mapping[str, float] | None = None,
    ) -> DCOPFResult | DCOPFRecourseSummary | str:
        if demand_mw_by_bus is None:
            if buses is None or tuple(bus.bus_id for bus in buses) != self._bus_order:
                raise ValueError("topology inputs must match the reusable model")
            demand_values = {bus.bus_id: float(bus.demand_mw) for bus in buses}
        else:
            demand_values = dict(demand_mw_by_bus)
            if set(demand_values) != set(self._bus_order):
                raise ValueError("demand must reference exactly the reusable model buses")
        if any(not math.isfinite(value) or value < 0 for value in demand_values.values()):
            raise ValueError("invalid demand in reusable DCOPF solve")
        injections = dict(fixed_injection_mw_by_bus or {})
        if set(injections) - set(self._bus_order):
            raise ValueError("fixed injection references an unknown bus")
        if any(not math.isfinite(value) for value in injections.values()):
            raise ValueError("fixed injections must be finite")
        for bus_id in self._bus_order:
            rhs = demand_values[bus_id] - injections.get(bus_id, 0.0)
            self._highs.changeRowBounds(self._balance_row_indices[bus_id], rhs, rhs)

        self._highs.run()
        model_status = self._highs.getModelStatus()
        _Highs, HighsModelStatus, _HighsVarType, _highs_inf = _highs()
        if model_status == getattr(HighsModelStatus, "kInfeasible", None):
            raise DCOPFInfeasible(f"DCOPF physically infeasible: {model_status}")
        if model_status != HighsModelStatus.kOptimal:
            raise DCOPFUnresolved(f"DCOPF solver unresolved: {model_status}")
        if not return_result and not return_summary:
            return "optimal"

        solution = self._highs.getSolution()

        if return_summary:
            return DCOPFRecourseSummary(
                status="optimal",
                objective_cost=round(float(self._highs.getObjectiveValue()), 10),
                balance_dual_by_bus={
                    bus_id: round(solution.row_dual[row_index], 10)
                    for bus_id, row_index in self._balance_row_indices.items()
                },
            )

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


class GurobiReusableDCOPFModel:
    """Optional Gurobi equivalent of :class:`ReusableDCOPFModel`.

    The class is imported only when explicitly selected.  It mirrors the
    HiGHS reusable-model contract so the exact Benders decomposition can be
    benchmarked without changing its mathematical formulation.
    """

    def __init__(
        self,
        *,
        buses: tuple[Bus, ...],
        generators: tuple[Generator, ...],
        branches: tuple[Branch, ...],
        base_mva: float = 1.0,
        enable_injection_probes: bool = False,
        injection_probe_bus_id: str | None = None,
        enable_candidate_demand_probes: bool = False,
        candidate_demand_bus_id: str | None = None,
        solver_threads: int = 1,
    ) -> None:
        try:
            import gurobipy as gp
        except ImportError as exc:
            raise RuntimeError("Gurobi backend requires the optional 'gurobipy' package") from exc
        if not math.isfinite(base_mva) or base_mva <= 0:
            raise ValueError("base_mva must be finite and positive")
        if not isinstance(solver_threads, int) or isinstance(solver_threads, bool) or solver_threads <= 0:
            raise ValueError("solver_threads must be a positive integer")
        self._gp = gp
        self._buses, self._reference = _validate(buses, generators, branches)
        self._bus_order = tuple(bus.bus_id for bus in buses)
        if injection_probe_bus_id is not None and injection_probe_bus_id not in self._bus_order:
            raise ValueError(f"unknown injection probe bus: {injection_probe_bus_id}")
        if injection_probe_bus_id is not None:
            enable_injection_probes = True
        if candidate_demand_bus_id is not None and candidate_demand_bus_id not in self._bus_order:
            raise ValueError(f"unknown candidate demand bus: {candidate_demand_bus_id}")
        if candidate_demand_bus_id is not None:
            enable_candidate_demand_probes = True
        self._generators = tuple(generators)
        self._branches = tuple(branches)
        self._base_mva = base_mva
        self._angle_scale = 1000.0
        self._model = gp.Model("ercot_dcopf_reusable")
        self._model.Params.OutputFlag = 0
        self._model.Params.Threads = solver_threads
        self._generator_columns = {
            generator.generator_id: self._model.addVar(
                lb=generator.p_min_mw,
                ub=generator.p_max_mw,
                obj=generator.marginal_cost_per_mwh,
                name=f"gen:{generator.generator_id}",
            )
            for generator in self._generators
        }
        self._angle_columns = {self._reference: None}
        for bus_id in self._bus_order:
            if bus_id != self._reference:
                self._angle_columns[bus_id] = self._model.addVar(
                    lb=-gp.GRB.INFINITY,
                    ub=gp.GRB.INFINITY,
                    obj=0.0,
                    name=f"theta:{bus_id}",
                )
        self._injection_probe_columns = {}
        if enable_injection_probes:
            for bus_id in self._bus_order:
                if injection_probe_bus_id is None or bus_id == injection_probe_bus_id:
                    self._injection_probe_columns[bus_id] = self._model.addVar(
                        lb=0.0, ub=0.0, obj=0.0, name=f"injection-probe:{bus_id}"
                    )
        candidate_bus_ids = ()
        if enable_candidate_demand_probes:
            candidate_bus_ids = (
                (candidate_demand_bus_id,)
                if candidate_demand_bus_id is not None
                else self._bus_order
            )
        self._candidate_demand_columns = {
            bus_id: self._model.addVar(
                lb=0.0,
                ub=0.0,
                obj=0.0,
                name=f"candidate-demand:{bus_id}",
            )
            for bus_id in candidate_bus_ids
        }
        generators_by_bus, branches_by_bus = _cached_network_index(self._branches, self._generators)
        self._balance_rows = {}
        for bus_id in self._bus_order:
            expression = gp.LinExpr()
            for generator in generators_by_bus.get(bus_id, ()):
                expression += self._generator_columns[generator.generator_id]
            probe = self._injection_probe_columns.get(bus_id)
            if probe is not None:
                expression += probe
            candidate = self._candidate_demand_columns.get(bus_id)
            if candidate is not None:
                expression += -candidate
            for branch in branches_by_bus.get(bus_id, ()):
                susceptance = branch.susceptance_pu * self._base_mva
                if branch.from_bus_id == bus_id:
                    if branch.from_bus_id != self._reference:
                        expression += -susceptance / self._angle_scale * self._angle_columns[branch.from_bus_id]
                    if branch.to_bus_id != self._reference:
                        expression += susceptance / self._angle_scale * self._angle_columns[branch.to_bus_id]
                elif branch.to_bus_id == bus_id:
                    if branch.to_bus_id != self._reference:
                        expression += -susceptance / self._angle_scale * self._angle_columns[branch.to_bus_id]
                    if branch.from_bus_id != self._reference:
                        expression += susceptance / self._angle_scale * self._angle_columns[branch.from_bus_id]
            self._balance_rows[bus_id] = self._model.addConstr(expression == 0.0, name=f"balance:{bus_id}")
        for branch in self._branches:
            expression = gp.LinExpr()
            susceptance = branch.susceptance_pu * self._base_mva
            if branch.from_bus_id != self._reference:
                expression += susceptance / self._angle_scale * self._angle_columns[branch.from_bus_id]
            if branch.to_bus_id != self._reference:
                expression += -susceptance / self._angle_scale * self._angle_columns[branch.to_bus_id]
            self._model.addConstr(expression <= branch.thermal_limit_mw, name=f"line-upper:{branch.branch_id}")
            self._model.addConstr(expression >= -branch.thermal_limit_mw, name=f"line-lower:{branch.branch_id}")
        self._model.update()
        self._base_objective = gp.LinExpr()
        for generator in self._generators:
            self._base_objective += (
                generator.marginal_cost_per_mwh
                * self._generator_columns[generator.generator_id]
            )
        self._model.setObjective(self._base_objective, gp.GRB.MINIMIZE)

    def max_additional_demand(
        self,
        buses: tuple[Bus, ...],
        bus_id: str,
        *,
        upper_bound_mw: float | None = None,
        fixed_injection_mw_by_bus: Mapping[str, float] | None = None,
    ) -> tuple[float, str]:
        """Return the exact maximum additional load at one bus."""
        if tuple(bus.bus_id for bus in buses) != self._bus_order:
            raise ValueError("topology inputs must match the reusable model")
        if bus_id not in self._balance_rows:
            raise ValueError(f"unknown candidate demand bus: {bus_id}")
        candidate = self._candidate_demand_columns.get(bus_id)
        if candidate is None:
            raise ValueError("candidate demand probes were not enabled for this reusable model")
        if upper_bound_mw is not None and (
            not _is_finite_real(upper_bound_mw) or upper_bound_mw < 0
        ):
            raise ValueError("upper_bound_mw must be finite and non-negative")
        if fixed_injection_mw_by_bus is not None and not isinstance(fixed_injection_mw_by_bus, Mapping):
            raise ValueError("fixed injections must be a mapping")
        demand_values = {bus.bus_id: bus.demand_mw for bus in buses}
        if any(not _is_finite_real(value) or value < 0 for value in demand_values.values()):
            raise ValueError("invalid demand in reusable DCOPF solve")
        injections = dict(fixed_injection_mw_by_bus or {})
        if set(injections) - set(self._bus_order):
            raise ValueError("fixed injection references an unknown bus")
        if any(not _is_finite_real(value) for value in injections.values()):
            raise ValueError("fixed injections must be finite")

        self._set_rhs(buses, injections)
        original_objective = self._model.getObjective()
        original_sense = self._model.ModelSense
        candidate_bounds = tuple(
            (variable, variable.LB, variable.UB)
            for variable in self._candidate_demand_columns.values()
        )

        def status_for(code: int, *, base_case: bool) -> str:
            if code == self._gp.GRB.OPTIMAL:
                return "optimal"
            if code == self._gp.GRB.INFEASIBLE:
                return "infeasible_base_case" if base_case else "solver_error"
            if code == self._gp.GRB.UNBOUNDED:
                return "unbounded"
            if code == getattr(self._gp.GRB, "NUMERIC", object()):
                return "numerical_failure"
            return "solver_error"

        try:
            for variable in self._candidate_demand_columns.values():
                variable.LB = 0.0
                variable.UB = 0.0
            self._model.setObjective(original_objective, original_sense)
            self._model.optimize()
            base_status = status_for(self._model.Status, base_case=True)
            if base_status != "optimal":
                return math.nan, base_status

            candidate.LB = 0.0
            candidate.UB = self._gp.GRB.INFINITY if upper_bound_mw is None else float(upper_bound_mw)
            self._model.setObjective(candidate, self._gp.GRB.MAXIMIZE)
            self._model.optimize()
            probe_status = status_for(self._model.Status, base_case=False)
            if probe_status != "optimal":
                return math.nan, probe_status
            return float(candidate.X), "optimal"
        finally:
            self._model.setObjective(original_objective, original_sense)
            for variable, lower_bound, upper_bound in candidate_bounds:
                variable.LB = lower_bound
                variable.UB = upper_bound

    def set_time_limit_seconds(self, seconds: float) -> None:
        if not _is_finite_real(seconds) or seconds < 0:
            raise ValueError("solver time limit must be finite and non-negative")
        self._model.Params.TimeLimit = float(seconds)

    def _set_rhs(
        self,
        buses: tuple[Bus, ...] | None,
        fixed_injection_mw_by_bus: Mapping[str, float] | None,
        demand_mw_by_bus: Mapping[str, float] | None = None,
    ) -> None:
        if demand_mw_by_bus is None:
            if buses is None or tuple(bus.bus_id for bus in buses) != self._bus_order:
                raise ValueError("topology inputs must match the reusable model")
            demand_values = {bus.bus_id: float(bus.demand_mw) for bus in buses}
        else:
            demand_values = dict(demand_mw_by_bus)
            if set(demand_values) != set(self._bus_order):
                raise ValueError("demand must reference exactly the reusable model buses")
        if any(not math.isfinite(value) or value < 0 for value in demand_values.values()):
            raise ValueError("invalid demand in reusable DCOPF solve")
        injections = dict(fixed_injection_mw_by_bus or {})
        if set(injections) - set(self._bus_order) or any(not _is_finite_real(value) for value in injections.values()):
            raise ValueError("fixed injections must be finite and reference known buses")
        for bus_id in self._bus_order:
            self._balance_rows[bus_id].RHS = demand_values[bus_id] - injections.get(bus_id, 0.0)

    def _optimize(self) -> None:
        self._model.optimize()
        status = self._model.Status
        if status == self._gp.GRB.INFEASIBLE:
            raise DCOPFInfeasible(f"DCOPF physically infeasible: {status}")
        if status != self._gp.GRB.OPTIMAL:
            raise DCOPFUnresolved(f"DCOPF solver unresolved: {status}")

    def feasible_injection_interval(
        self,
        buses: tuple[Bus, ...],
        bus_id: str,
        *,
        lower_bound: float,
        upper_bound: float,
        fixed_injection_mw_by_bus: Mapping[str, float] | None = None,
    ) -> tuple[float, float]:
        self._set_rhs(buses, fixed_injection_mw_by_bus)
        probe = self._injection_probe_columns.get(bus_id)
        if probe is None:
            raise ValueError("injection probes were not enabled for this reusable model")
        probe.LB = lower_bound
        probe.UB = upper_bound
        try:
            self._model.setObjective(self._base_objective + probe, self._gp.GRB.MINIMIZE)
            self._optimize()
            minimum = float(probe.X)
            self._model.setObjective(self._base_objective - probe, self._gp.GRB.MINIMIZE)
            self._optimize()
            maximum = float(probe.X)
        finally:
            self._model.setObjective(self._base_objective, self._gp.GRB.MINIMIZE)
            probe.LB = 0.0
            probe.UB = 0.0
        return minimum, maximum

    def solve(
        self,
        buses: tuple[Bus, ...] | None,
        fixed_injection_mw_by_bus: Mapping[str, float] | None = None,
        *,
        return_result: bool = True,
        return_summary: bool = False,
        demand_mw_by_bus: Mapping[str, float] | None = None,
    ) -> DCOPFResult | DCOPFRecourseSummary | str:
        self._set_rhs(buses, fixed_injection_mw_by_bus, demand_mw_by_bus)
        self._model.setObjective(self._base_objective, self._gp.GRB.MINIMIZE)
        self._optimize()
        if not return_result and not return_summary:
            return "optimal"
        if return_summary:
            return DCOPFRecourseSummary(
                status="optimal",
                objective_cost=round(float(self._model.ObjVal), 10),
                balance_dual_by_bus={bus_id: round(float(row.Pi), 10) for bus_id, row in self._balance_rows.items()},
            )
        generation = {
            generator_id: round(float(variable.X), 10)
            for generator_id, variable in self._generator_columns.items()
        }
        raw_angles = {self._reference: 0.0}
        raw_angles.update({bus_id: float(variable.X) / self._angle_scale for bus_id, variable in self._angle_columns.items() if bus_id != self._reference})
        angles = {bus_id: round(angle, 10) for bus_id, angle in raw_angles.items()}
        flows = {
            branch.branch_id: round(branch.susceptance_pu * self._base_mva * (raw_angles[branch.from_bus_id] - raw_angles[branch.to_bus_id]), 10)
            for branch in self._branches
        }
        return DCOPFResult(
            status="optimal",
            objective_cost=round(float(self._model.ObjVal), 10),
            generation_mw=generation,
            angle_by_bus=angles,
            flow_mw_by_branch=flows,
            balance_dual_by_bus={bus_id: round(float(row.Pi), 10) for bus_id, row in self._balance_rows.items()},
        )


def gurobi_is_available() -> bool:
    """Return whether the optional Gurobi package and license can start.

    The probe uses a quiet, short-lived environment and never creates a model.
    This keeps ``auto`` safe on machines where Gurobi is not installed or its
    license is unavailable, while avoiding a solver fallback after a partial
    evaluation has already started.
    """
    try:
        import gurobipy as gp
    except ImportError:
        return False
    environment = None
    try:
        environment = gp.Env(empty=True)
        environment.setParam("OutputFlag", 0)
        environment.start()
        return True
    except Exception:
        return False
    finally:
        if environment is not None:
            try:
                environment.dispose()
            except Exception:
                pass


def resolve_solver_backend(solver_backend: str) -> str:
    """Resolve ``auto`` to Gurobi when licensed, otherwise HiGHS."""
    if solver_backend not in {"auto", "highs", "gurobi"}:
        raise ValueError("solver_backend must be 'auto', 'highs', or 'gurobi'")
    if solver_backend == "auto":
        return "gurobi" if gurobi_is_available() else "highs"
    return solver_backend


def _reusable_dcopf_model(*, solver_backend: str, **kwargs):
    if solver_backend == "highs":
        return ReusableDCOPFModel(**kwargs)
    if solver_backend == "gurobi":
        return GurobiReusableDCOPFModel(**kwargs)
    raise ValueError("solver_backend must be 'highs' or 'gurobi'")


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


class _ReusableHorizonModelState:
    """Built HiGHS horizon model whose mutable rows can be updated and reused."""

    def __init__(
        self,
        *,
        highs: object,
        highs_model_status: object,
        highs_inf: float,
        model_inputs: Sequence[tuple[dict[str, Bus], str, tuple[Generator, ...], tuple[Branch, ...], dict[str, float]]],
        storage_assets: tuple[StorageDispatchAsset, ...],
        owner_loads: Mapping[str, tuple[Mapping[str, float], ...]],
        base_mva: float,
        angle_scale: float,
        generator_columns: Mapping[tuple[int, str], object],
        angle_columns: Mapping[tuple[int, str], object],
        storage_power_columns: Mapping[tuple[int, str], object],
        soc_columns: Mapping[tuple[int, str], object],
        balance_row_indices: Mapping[tuple[int, str], int],
        owner_load_row_indices: Mapping[tuple[int, str, str], int],
    ) -> None:
        self._highs = highs
        self._highs_model_status = highs_model_status
        self._highs_inf = highs_inf
        self._model_inputs = tuple(model_inputs)
        self._storage_assets = storage_assets
        self._owner_loads = owner_loads
        self._base_mva = base_mva
        self._angle_scale = angle_scale
        self._generator_columns = generator_columns
        self._angle_columns = angle_columns
        self._storage_power_columns = storage_power_columns
        self._soc_columns = soc_columns
        self._balance_row_indices = balance_row_indices
        self._owner_load_row_indices = owner_load_row_indices
        self._last_solve_timing = {
            "input_update_s": 0.0,
            "solver_run_s": 0.0,
            "result_extraction_s": 0.0,
            "total_s": 0.0,
        }

    @property
    def last_solve_timing(self) -> dict[str, float]:
        """Return phase timings for the most recent reusable solve."""
        return dict(self._last_solve_timing)

    def _validate_solve_inputs(
        self,
        fixed_injections: Sequence[Mapping[str, float]],
        owner_loads: Mapping[str, Sequence[Mapping[str, float]]] | None,
    ) -> tuple[tuple[Mapping[str, float], ...], dict[str, tuple[Mapping[str, float], ...]]]:
        period_count = len(self._model_inputs)
        fixed = tuple(fixed_injections)
        if len(fixed) != period_count or any(not isinstance(values, Mapping) for values in fixed):
            raise ValueError("fixed injections must match the number of periods and contain mappings")
        normalized_fixed: list[Mapping[str, float]] = []
        for period_index, values in enumerate(fixed):
            bus_ids = set(self._model_inputs[period_index][0])
            if set(values) - bus_ids:
                raise ValueError("fixed injection references an unknown bus")
            if any(not _is_finite_real(value) for value in values.values()):
                raise ValueError("fixed injections must be finite")
            normalized_fixed.append(dict(values))

        if owner_loads is None:
            normalized_owner = dict(self._owner_loads)
        else:
            if set(owner_loads) != set(self._owner_loads):
                raise ValueError("owner load teams must match the reusable model")
            normalized_owner = {}
            for team_id, values in owner_loads.items():
                values = tuple(values)
                if len(values) != period_count:
                    raise ValueError(f"owner load for {team_id} must match the number of periods")
                checked_periods: list[Mapping[str, float]] = []
                for period_index, period_load in enumerate(values):
                    if not isinstance(period_load, Mapping):
                        raise ValueError(f"owner load for {team_id} must contain mappings")
                    bus_ids = set(self._model_inputs[period_index][0])
                    if set(period_load) - bus_ids:
                        raise ValueError(f"owner load for {team_id} references an unknown bus")
                    if any(not _is_finite_real(value) or value < 0 for value in period_load.values()):
                        raise ValueError(f"owner load for {team_id} must be finite and non-negative")
                    checked_periods.append(dict(period_load))
                normalized_owner[team_id] = tuple(checked_periods)
        return tuple(normalized_fixed), normalized_owner

    def solve(
        self,
        *,
        fixed_injection_mw_by_period: Sequence[Mapping[str, float]] | None = None,
        owner_load_mw_by_team: Mapping[str, Sequence[Mapping[str, float]]] | None = None,
        return_result: bool = True,
        wall_time_limit_seconds: float | None = None,
    ) -> DCOPFHorizonResult | str:
        if wall_time_limit_seconds is not None and (
            not _is_finite_real(wall_time_limit_seconds) or wall_time_limit_seconds < 0
        ):
            raise ValueError("wall_time_limit_seconds must be finite and non-negative")
        total_started = time.perf_counter()
        input_started = total_started
        fixed, owner_loads = self._validate_solve_inputs(
            fixed_injection_mw_by_period or ({},) * len(self._model_inputs),
            owner_load_mw_by_team,
        )
        for period_index, (bus_by_id, _reference, _generators, _branches, _initial_injections) in enumerate(self._model_inputs):
            for bus_id, bus in bus_by_id.items():
                rhs = bus.demand_mw - fixed[period_index].get(bus_id, 0.0)
                row_index = self._balance_row_indices[(period_index, bus_id)]
                self._highs.changeRowBounds(row_index, rhs, rhs)
        for (team_id, bus_id, period_index), row_index in self._owner_load_row_indices.items():
            upper = owner_loads[team_id][period_index].get(bus_id, 0.0)
            self._highs.changeRowBounds(row_index, -self._highs_inf, upper)
        input_update_s = time.perf_counter() - input_started

        solver_started = time.perf_counter()
        if wall_time_limit_seconds is not None:
            self._highs.setOptionValue("time_limit", float(wall_time_limit_seconds))
        self._highs.run()
        solver_run_s = time.perf_counter() - solver_started
        model_status = self._highs.getModelStatus()
        if model_status == getattr(self._highs_model_status, "kInfeasible", None):
            raise DCOPFInfeasible(f"DCOPF horizon physically infeasible: {model_status}")
        if model_status != self._highs_model_status.kOptimal:
            raise DCOPFUnresolved(f"DCOPF horizon solver unresolved: {model_status}")

        if not return_result:
            self._last_solve_timing = {
                "input_update_s": input_update_s,
                "solver_run_s": solver_run_s,
                "result_extraction_s": 0.0,
                "total_s": time.perf_counter() - total_started,
            }
            return "optimal"

        extraction_started = time.perf_counter()
        solution = self._highs.getSolution()

        def value(variable: object) -> float:
            return float(solution.col_value[int(variable.index)])

        results: list[DCOPFResult] = []
        for period_index, (bus_by_id, reference, generators, branches, _injections) in enumerate(self._model_inputs):
            generation = {
                generator.generator_id: round(value(self._generator_columns[(period_index, generator.generator_id)]), 10)
                for generator in generators
            }
            raw_angles = {reference: 0.0}
            raw_angles.update({
                bus_id: value(self._angle_columns[(period_index, bus_id)]) / self._angle_scale
                for bus_id in bus_by_id
                if bus_id != reference
            })
            angles = {bus_id: round(angle, 10) for bus_id, angle in raw_angles.items()}
            flows = {
                branch.branch_id: round(
                    branch.susceptance_pu * self._base_mva
                    * (raw_angles[branch.from_bus_id] - raw_angles[branch.to_bus_id]),
                    10,
                )
                for branch in branches
            }
            balance_duals = {
                bus_id: round(solution.row_dual[row_index], 10)
                for (row_period, bus_id), row_index in self._balance_row_indices.items()
                if row_period == period_index
            }
            net_power = {
                storage.storage_id: value(self._storage_power_columns[(period_index, storage.storage_id)])
                for storage in self._storage_assets
            }
            charge = {storage_id: round(max(-power, 0.0), 10) for storage_id, power in net_power.items()}
            discharge = {storage_id: round(max(power, 0.0), 10) for storage_id, power in net_power.items()}
            soc = {
                storage.storage_id: round(value(self._soc_columns[(period_index, storage.storage_id)]), 10)
                for storage in self._storage_assets
            }
            objective = sum(
                generation[generator.generator_id] * generator.marginal_cost_per_mwh
                for generator in generators
            )
            results.append(DCOPFResult(
                status="optimal",
                objective_cost=round(objective, 10),
                generation_mw=generation,
                angle_by_bus=angles,
                flow_mw_by_branch=flows,
                balance_dual_by_bus=balance_duals,
                storage_charge_mw=charge,
                storage_discharge_mw=discharge,
                storage_soc_mwh=soc,
            ))
        result = DCOPFHorizonResult(
            status="optimal",
            periods=tuple(results),
            final_soc_mwh_by_storage={
                storage.storage_id: results[-1].storage_soc_mwh[storage.storage_id]
                for storage in self._storage_assets
            },
        )
        self._last_solve_timing = {
            "input_update_s": input_update_s,
            "solver_run_s": solver_run_s,
            "result_extraction_s": time.perf_counter() - extraction_started,
            "total_s": time.perf_counter() - total_started,
        }
        return result


class ReusableDCOPFHorizonModel:
    """Reusable fixed-structure horizon LP for repeated dispatch evaluations."""

    def __init__(
        self,
        *,
        periods,
        storage_assets=(),
        fixed_injection_mw_by_period=None,
        owner_load_mw_by_team=None,
        time_step_hours=1.0,
        base_mva=1.0,
    ) -> None:
        self._state = solve_dcopf_horizon(
            periods=periods,
            storage_assets=storage_assets,
            fixed_injection_mw_by_period=fixed_injection_mw_by_period,
            owner_load_mw_by_team=owner_load_mw_by_team,
            time_step_hours=time_step_hours,
            base_mva=base_mva,
            _return_model=True,
        )

    def solve(
        self,
        *,
        fixed_injection_mw_by_period=None,
        owner_load_mw_by_team=None,
        return_result=True,
        wall_time_limit_seconds=None,
    ):
        return self._state.solve(
            fixed_injection_mw_by_period=fixed_injection_mw_by_period,
            owner_load_mw_by_team=owner_load_mw_by_team,
            return_result=return_result,
            wall_time_limit_seconds=wall_time_limit_seconds,
        )

    @property
    def last_solve_timing(self) -> dict[str, float]:
        return self._state.last_solve_timing


def solve_dcopf_horizon(
    *,
    periods: Sequence[tuple[tuple[Bus, ...], tuple[Generator, ...], tuple[Branch, ...]]],
    storage_assets: Sequence[StorageDispatchAsset] = (),
    fixed_injection_mw_by_period: Sequence[Mapping[str, float]] | None = None,
    owner_load_mw_by_team: Mapping[str, Sequence[Mapping[str, float]]] | None = None,
    time_step_hours: float = 1.0,
    base_mva: float = 1.0,
    wall_time_limit_seconds: float | None = None,
    _return_model: bool = False,
) -> DCOPFHorizonResult:
    """Solve a coupled multi-period DCOPF with team-dedicated storage.

    The network and generator constraints are the same linear DCOPF used by
    :func:`solve_dcopf`.  Storage is represented by one signed net-power
    variable per asset/period: positive values discharge and negative values
    charge.  This keeps the coupled horizon a linear program while preserving
    the economically relevant storage behavior under the current assumptions
    (unit efficiency, no storage operating cost, and no value for simultaneous
    charge and discharge).  Persisted initial SOC and terminal
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
    if wall_time_limit_seconds is not None and (
        not _is_finite_real(wall_time_limit_seconds) or wall_time_limit_seconds < 0
    ):
        raise ValueError("wall_time_limit_seconds must be finite and non-negative")

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
    compiled_topology: tuple[tuple[str, ...], str, tuple[tuple[object, ...], ...], tuple[tuple[object, ...], ...]] | None = None
    for period_index, (buses, generators, branches) in enumerate(period_data):
        if period_index == 0:
            bus_by_id, reference = _validate(buses, generators, branches)
            compiled_topology = (
                tuple(bus_by_id),
                reference,
                tuple(
                        (generator.generator_id, generator.bus_id, generator.p_min_mw, generator.p_max_mw)
                    for generator in generators
                ),
                tuple(
                    (branch.branch_id, branch.from_bus_id, branch.to_bus_id, branch.susceptance_pu, branch.thermal_limit_mw)
                    for branch in branches
                ),
            )
        else:
            assert compiled_topology is not None
            current_topology = (
                tuple(bus.bus_id for bus in buses),
                next((bus.bus_id for bus in buses if bus.reference), buses[0].bus_id),
                tuple(
                        (generator.generator_id, generator.bus_id, generator.p_min_mw, generator.p_max_mw)
                    for generator in generators
                ),
                tuple(
                    (branch.branch_id, branch.from_bus_id, branch.to_bus_id, branch.susceptance_pu, branch.thermal_limit_mw)
                    for branch in branches
                ),
            )
            if current_topology != compiled_topology:
                raise ValueError("all horizon periods must share one fixed network topology")
            bus_by_id = {bus.bus_id: bus for bus in buses}
            reference = compiled_topology[1]
            for bus in buses:
                if not math.isfinite(bus.demand_mw) or bus.demand_mw < 0:
                    raise ValueError(f"invalid demand for bus {bus.bus_id}")
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
    storage_power_columns: dict[tuple[int, str], object] = {}
    soc_columns: dict[tuple[int, str], object] = {}
    balance_row_indices: dict[tuple[int, str], int] = {}
    owner_load_row_indices: dict[tuple[str, str, int], int] = {}

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
            storage_power_columns[(period_index, storage.storage_id)] = highs.addVariable(
                lb=-storage.power_mw,
                ub=storage.power_mw,
                obj=0.0,
                name=f"storage-net-power:{period_index}:{storage.storage_id}",
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
                add_coefficient(coefficients, storage_power_columns[(period_index, storage.storage_id)], 1.0)
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
            balance_row_indices[(period_index, bus_id)] = highs.getNumRow() - 1

        for branch in branches:
            coefficients: dict[int, float] = {}
            susceptance_mw_per_rad = branch.susceptance_pu * base_mva
            if branch.from_bus_id != reference:
                add_coefficient(coefficients, angle_columns[(period_index, branch.from_bus_id)], susceptance_mw_per_rad / angle_scale)
            if branch.to_bus_id != reference:
                add_coefficient(coefficients, angle_columns[(period_index, branch.to_bus_id)], -susceptance_mw_per_rad / angle_scale)
            highs.addRow(-branch.thermal_limit_mw, branch.thermal_limit_mw, len(coefficients), list(coefficients), list(coefficients.values()))

        for storage in storage_assets:
            storage_power = storage_power_columns[(period_index, storage.storage_id)]
            coefficients = {
                int(soc_columns[(period_index, storage.storage_id)].index): 1.0,
                int(storage_power.index): time_step_hours,
            }
            if period_index > 0:
                add_coefficient(coefficients, soc_columns[(period_index - 1, storage.storage_id)], -1.0)
                rhs_lower = rhs_upper = 0.0
            else:
                rhs_lower = rhs_upper = storage.initial_soc_mwh
            highs.addRow(rhs_lower, rhs_upper, len(coefficients), list(coefficients), list(coefficients.values()))

            discharge_coefficients = {
                int(storage_power.index): time_step_hours,
            }
            if period_index > 0:
                add_coefficient(discharge_coefficients, soc_columns[(period_index - 1, storage.storage_id)], -1.0)
                discharge_rhs = 0.0
            else:
                discharge_rhs = storage.initial_soc_mwh
            highs.addRow(-highs_inf, discharge_rhs, len(discharge_coefficients), list(discharge_coefficients), list(discharge_coefficients.values()))

        for (team_id, bus_id), team_bus_storages in _group_storage_by_owner_bus(storage_assets).items():
            coefficients = {
                int(storage_power_columns[(period_index, storage.storage_id)].index): 1.0
                for storage in team_bus_storages
            }
            owner_load = owner_loads[team_id][period_index].get(bus_id, 0.0)
            highs.addRow(-highs_inf, owner_load, len(coefficients), list(coefficients), list(coefficients.values()))
            owner_load_row_indices[(team_id, bus_id, period_index)] = highs.getNumRow() - 1

    reusable_state = _ReusableHorizonModelState(
        highs=highs,
        highs_model_status=HighsModelStatus,
        highs_inf=highs_inf,
        model_inputs=model_inputs,
        storage_assets=storage_assets,
        owner_loads=owner_loads,
        base_mva=base_mva,
        angle_scale=angle_scale,
        generator_columns=generator_columns,
        angle_columns=angle_columns,
        storage_power_columns=storage_power_columns,
        soc_columns=soc_columns,
        balance_row_indices=balance_row_indices,
        owner_load_row_indices=owner_load_row_indices,
    )
    if _return_model:
        return reusable_state

    if wall_time_limit_seconds is not None:
        highs.setOptionValue("time_limit", float(wall_time_limit_seconds))
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
        net_power = {
            storage.storage_id: value(storage_power_columns[(period_index, storage.storage_id)])
            for storage in storage_assets
        }
        charge = {
            storage_id: round(max(-power, 0.0), 10)
            for storage_id, power in net_power.items()
        }
        discharge = {
            storage_id: round(max(power, 0.0), 10)
            for storage_id, power in net_power.items()
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


def solve_dcopf_horizon_benders(
    *,
    periods: Sequence[tuple[tuple[Bus, ...], tuple[Generator, ...], tuple[Branch, ...]]],
    demand_mw_by_period: Sequence[Mapping[str, float]] | None = None,
    storage_assets: Sequence[StorageDispatchAsset] = (),
    fixed_injection_mw_by_period: Sequence[Mapping[str, float]] | None = None,
    owner_load_mw_by_team: Mapping[str, Sequence[Mapping[str, float]]] | None = None,
    time_step_hours: float = 1.0,
    base_mva: float = 1.0,
    tolerance: float = 1e-7,
    max_iterations: int = 200,
    return_result: bool = True,
    parallel_workers: int = 1,
    solver_threads: int = 1,
    solver_backend: str = "highs",
    wall_time_limit_seconds: float | None = None,
    tighten_feasible_bounds: bool = False,
) -> DCOPFHorizonResult | str:
    """Solve a storage horizon through exact Benders decomposition.

    One or more storage assets are supported on a fixed network topology. The
    master contains storage power, state of charge, and one recourse-cost
    variable per period. Each hourly recourse problem is a reusable
    single-period DCOPF. Balance-row duals generate supporting cuts for the
    hourly value functions, so the returned optimum is equivalent to the
    coupled horizon LP up to ``tolerance``.

    By default, the master starts with physical storage-power bounds and
    tightens individual hourly bounds only when a recourse solve proves that
    a proposed point is infeasible.  This avoids two auxiliary LPs for every
    hour on the common case while preserving exactness.  Set
    ``tighten_feasible_bounds=True`` to restore the conservative interval
    prepass.  A wall-clock budget is checked between batches and raises
    :class:`DCOPFUnresolved` rather than allowing a remote notebook to run
    indefinitely.
    """
    storage_assets = tuple(storage_assets)
    solver_backend = resolve_solver_backend(solver_backend)
    if len(storage_assets) > 1:
        return _solve_dcopf_horizon_benders_multi(
            periods=periods,
            demand_mw_by_period=demand_mw_by_period,
            storage_assets=storage_assets,
            fixed_injection_mw_by_period=fixed_injection_mw_by_period,
            owner_load_mw_by_team=owner_load_mw_by_team,
            time_step_hours=time_step_hours,
            base_mva=base_mva,
            tolerance=tolerance,
            max_iterations=max_iterations,
            return_result=return_result,
            parallel_workers=parallel_workers,
            solver_threads=solver_threads,
            solver_backend=solver_backend,
            wall_time_limit_seconds=wall_time_limit_seconds,
            tighten_feasible_bounds=tighten_feasible_bounds,
        )
    if len(storage_assets) != 1:
        raise ValueError("Benders storage decomposition requires at least one storage asset")
    storage = tuple(storage_assets)[0]
    if not _is_finite_real(tolerance) or tolerance <= 0:
        raise ValueError("tolerance must be finite and positive")
    if not isinstance(max_iterations, int) or max_iterations <= 0:
        raise ValueError("max_iterations must be a positive integer")
    if not isinstance(parallel_workers, int) or parallel_workers <= 0:
        raise ValueError("parallel_workers must be a positive integer")
    if not isinstance(solver_threads, int) or isinstance(solver_threads, bool) or solver_threads <= 0:
        raise ValueError("solver_threads must be a positive integer")
    if wall_time_limit_seconds is not None and (
        not _is_finite_real(wall_time_limit_seconds) or wall_time_limit_seconds < 0
    ):
        raise ValueError("wall_time_limit_seconds must be finite and non-negative")
    if not isinstance(tighten_feasible_bounds, bool):
        raise ValueError("tighten_feasible_bounds must be a boolean")
    period_data = tuple(periods)
    if not period_data:
        raise ValueError("periods must not be empty")
    if not _is_finite_real(time_step_hours) or time_step_hours <= 0:
        raise ValueError("time_step_hours must be finite and positive")

    horizon = len(period_data)
    if demand_mw_by_period is None:
        demand_values = tuple(
            {bus.bus_id: float(bus.demand_mw) for bus in period[0]}
            for period in period_data
        )
    else:
        demand_values = tuple(dict(values) for values in demand_mw_by_period)
        if len(demand_values) != horizon:
            raise ValueError("demand must match the number of periods")
    def period_buses(period_index: int) -> tuple[Bus, ...]:
        return tuple(
            Bus(bus.bus_id, demand_values[period_index][bus.bus_id], reference=bus.reference)
            for bus in period_data[period_index][0]
        )
    reference_bus_order = tuple(bus.bus_id for bus in period_data[0][0])
    reference_generators = tuple(period_data[0][1])
    reference_branches = tuple(period_data[0][2])
    for period_index, (buses, generators, branches) in enumerate(period_data[1:], start=1):
        if tuple(bus.bus_id for bus in buses) != reference_bus_order:
            raise ValueError(
                "Benders requires a fixed bus topology across periods; "
                f"period {period_index} has a different bus order"
            )
        if tuple(generators) != reference_generators or tuple(branches) != reference_branches:
            raise ValueError(
                "Benders requires fixed generators and branches across periods; "
                f"period {period_index} changes the network topology"
            )
        if set(demand_values[period_index]) != set(reference_bus_order):
            raise ValueError("demand must reference exactly the fixed bus topology")
        if any(not _is_finite_real(value) or value < 0 for value in demand_values[period_index].values()):
            raise ValueError("demand must be finite and non-negative")
    fixed = tuple(fixed_injection_mw_by_period or ({},) * horizon)
    if len(fixed) != horizon:
        raise ValueError("fixed injections must match the number of periods")
    if any(not isinstance(values, Mapping) for values in fixed):
        raise ValueError("fixed injections must contain mappings")
    if owner_load_mw_by_team is None or storage.team_id not in owner_load_mw_by_team:
        raise ValueError("owner load is required for the storage team")
    owner_loads = tuple(owner_load_mw_by_team[storage.team_id])
    if len(owner_loads) != horizon:
        raise ValueError("owner load must match the number of periods")

    started_at = time.perf_counter()
    deadline = (
        None if wall_time_limit_seconds is None
        else started_at + float(wall_time_limit_seconds)
    )

    def check_deadline(stage: str, iteration: int | None = None) -> None:
        if deadline is not None and time.perf_counter() >= deadline:
            suffix = "" if iteration is None else f" at iteration {iteration}"
            elapsed = time.perf_counter() - started_at
            raise DCOPFUnresolved(
                f"Benders wall-time budget exceeded during {stage}{suffix} "
                f"after {elapsed:.2f}s"
            )

    check_deadline("initialization")

    executor = (
        ThreadPoolExecutor(
            max_workers=parallel_workers,
            thread_name_prefix="dcopf-recourse",
        )
        if parallel_workers > 1 else None
    )

    def map_periods(function):
        try:
            if executor is None:
                return [function(index) for index in range(horizon)]
            return list(executor.map(function, range(horizon)))
        except BaseException:
            close_executor()
            raise

    def close_executor() -> None:
        nonlocal executor
        if executor is not None:
            executor.shutdown(wait=True)
            executor = None

    # Storage is represented as a fixed injection in each hourly subproblem;
    # the intertemporal state remains exclusively in the master.  Network
    # models are created lazily per worker rather than once per period.  The
    # previous period-indexed model list multiplied a 2,000-bus HiGHS model by
    # 4,000+ hours and exhausted ordinary Colab runtimes.
    worker_state = threading.local()

    def recourse_model(period_index: int):
        model = getattr(worker_state, "model", None)
        if model is None:
            buses, generators, branches = period_data[0]
            model = _reusable_dcopf_model(
                buses=tuple(buses), generators=tuple(generators),
                branches=tuple(branches), base_mva=base_mva,
                enable_injection_probes=True,
                injection_probe_bus_id=storage.bus_id,
                solver_threads=solver_threads,
                solver_backend=solver_backend,
            )
            worker_state.model = model
        return model

    def solve_recourse(period_index: int, power_mw: float) -> DCOPFResult:
        check_deadline(f"recourse period {period_index}")
        injections = dict(fixed[period_index])
        injections[storage.bus_id] = injections.get(storage.bus_id, 0.0) + power_mw
        model = recourse_model(period_index)
        if deadline is not None:
            model.set_time_limit_seconds(max(0.001, deadline - time.perf_counter()))
        try:
            result = model.solve(
                None,
                fixed_injection_mw_by_bus=injections,
                demand_mw_by_bus=demand_values[period_index],
            )
        except DCOPFUnresolved as exc:
            if deadline is not None and (
                time.perf_counter() >= deadline or "kTimeLimit" in str(exc)
            ):
                raise DCOPFUnresolved(
                    f"Benders wall-time budget exceeded during recourse period {period_index}"
                ) from exc
            raise
        assert isinstance(result, DCOPFResult)
        return result

    def solve_recourse_summary(period_index: int, power_mw: float) -> DCOPFRecourseSummary:
        check_deadline(f"recourse period {period_index}")
        injections = dict(fixed[period_index])
        injections[storage.bus_id] = injections.get(storage.bus_id, 0.0) + power_mw
        model = recourse_model(period_index)
        if deadline is not None:
            model.set_time_limit_seconds(max(0.001, deadline - time.perf_counter()))
        try:
            result = model.solve(
                None,
                fixed_injection_mw_by_bus=injections,
                return_result=False,
                return_summary=True,
                demand_mw_by_bus=demand_values[period_index],
            )
        except DCOPFUnresolved as exc:
            if deadline is not None and (
                time.perf_counter() >= deadline or "kTimeLimit" in str(exc)
            ):
                raise DCOPFUnresolved(
                    f"Benders wall-time budget exceeded during recourse period {period_index}"
                ) from exc
            raise
        assert isinstance(result, DCOPFRecourseSummary)
        return result

    def owner_discharge_limit(period_index: int) -> float:
        load = owner_loads[period_index].get(storage.bus_id, 0.0)
        if not _is_finite_real(load) or load < 0:
            raise ValueError("owner load must be finite and non-negative")
        return min(storage.power_mw, float(load))

    def feasible_interval(period_index: int) -> tuple[float, float]:
        check_deadline(f"feasible-bound tightening for period {period_index}")
        injections = dict(fixed[period_index])
        model = recourse_model(period_index)
        if deadline is not None:
            model.set_time_limit_seconds(max(0.001, deadline - time.perf_counter()))
        try:
            return model.feasible_injection_interval(
                period_buses(period_index),
                storage.bus_id,
                lower_bound=-storage.power_mw,
                upper_bound=owner_discharge_limit(period_index),
                fixed_injection_mw_by_bus=injections,
            )
        except DCOPFUnresolved as exc:
            if deadline is not None and time.perf_counter() >= deadline:
                raise DCOPFUnresolved(
                    f"Benders wall-time budget exceeded during feasible-bound tightening "
                    f"for period {period_index}"
                ) from exc
            raise

    if tighten_feasible_bounds:
        feasible_bounds = tuple(map_periods(feasible_interval))
    else:
        feasible_bounds = tuple(
            (-storage.power_mw, owner_discharge_limit(index))
            for index in range(horizon)
        )

    Highs, HighsModelStatus, _HighsVarType, highs_inf = _highs()
    master = Highs()
    master.setOptionValue("output_flag", False)
    master.setOptionValue("presolve", "on")
    master.setOptionValue("parallel", "on" if solver_threads > 1 else "off")
    if solver_threads > 1:
        master.setOptionValue("threads", solver_threads)
    power_columns = [master.addVariable(
        lb=feasible_bounds[index][0], ub=feasible_bounds[index][1],
        obj=0.0, name=f"storage-power:{index}") for index in range(horizon)]
    soc_columns = [master.addVariable(
        lb=storage.energy_mwh * 0.5 if index == horizon - 1 else 0.0,
        ub=storage.energy_mwh, obj=0.0, name=f"storage-soc:{index}")
        for index in range(horizon)
    ]
    theta_columns = [master.addVariable(
        lb=-highs_inf, ub=highs_inf, obj=1.0, name=f"recourse-cost:{index}")
        for index in range(horizon)
    ]

    # SOC_t + dt * p_t - SOC_{t-1} = 0, with the initial state fixed.
    for index in range(horizon):
        coefficients = {
            int(soc_columns[index].index): 1.0,
            int(power_columns[index].index): time_step_hours,
        }
        if index == 0:
            lower = upper = storage.initial_soc_mwh
        else:
            coefficients[int(soc_columns[index - 1].index)] = -1.0
            lower = upper = 0.0
        master.addRow(lower, upper, len(coefficients), list(coefficients), list(coefficients.values()))

    def add_optimality_cut(
        period_index: int,
        point: float,
        result: DCOPFResult | DCOPFRecourseSummary,
    ) -> None:
        # The balance equation has RHS demand - injection, so increasing
        # storage discharge changes recourse cost by minus the nodal dual.
        gradient = -float(result.balance_dual_by_bus[storage.bus_id])
        coefficients = {
            int(theta_columns[period_index].index): 1.0,
            int(power_columns[period_index].index): -gradient,
        }
        rhs = result.objective_cost - gradient * point
        master.addRow(rhs, highs_inf, len(coefficients), list(coefficients), list(coefficients.values()))

    check_deadline("initial recourse batch")
    current_results = map_periods(lambda index: solve_recourse_summary(index, 0.0))
    current_points = [0.0] * horizon
    for index, result in enumerate(current_results):
        add_optimality_cut(index, 0.0, result)

    for _iteration in range(max_iterations):
        check_deadline("master/recourse iteration", _iteration)
        if deadline is not None:
            master.setOptionValue("time_limit", max(0.001, deadline - time.perf_counter()))
        master.run()
        status = master.getModelStatus()
        if status == getattr(HighsModelStatus, "kInfeasible", None):
            raise DCOPFInfeasible("Benders master became infeasible")
        if status != HighsModelStatus.kOptimal:
            raise DCOPFUnresolved(f"Benders master unresolved: {status}")
        solution = master.getSolution()
        points = [float(solution.col_value[int(column.index)]) for column in power_columns]
        def summary_or_infeasible(index: int) -> DCOPFRecourseSummary | DCOPFInfeasible:
            try:
                return solve_recourse_summary(index, points[index])
            except DCOPFInfeasible as exc:
                return exc

        results_or_errors = map_periods(summary_or_infeasible)
        check_deadline("recourse batch", _iteration)
        infeasible_periods = [
            index for index, value in enumerate(results_or_errors)
            if isinstance(value, DCOPFInfeasible)
        ]
        if infeasible_periods:
            if tighten_feasible_bounds:
                close_executor()
                raise DCOPFInfeasible(
                    "hourly recourse remained infeasible after feasible-bound tightening: "
                    + ", ".join(map(str, infeasible_periods))
                )
            for index in infeasible_periods:
                lower, upper = feasible_interval(index)
                master.changeColBounds(int(power_columns[index].index), lower, upper)
            continue
        results = [value for value in results_or_errors if isinstance(value, DCOPFRecourseSummary)]
        actual_objective = sum(result.objective_cost for result in results)
        lower_bound = float(master.getObjectiveValue())
        if actual_objective - lower_bound <= tolerance * max(1.0, abs(actual_objective)):
            check_deadline("convergence check", _iteration)
            if not return_result:
                close_executor()
                return "optimal"
            check_deadline("final result extraction", _iteration)
            full_results = map_periods(lambda index: solve_recourse(index, points[index]))
            periods_result = []
            final_soc = {}
            for index, result in enumerate(full_results):
                soc = storage.initial_soc_mwh - time_step_hours * sum(points[: index + 1])
                periods_result.append(replace(result, storage_charge_mw={
                    storage.storage_id: max(-points[index], 0.0)}, storage_discharge_mw={
                    storage.storage_id: max(points[index], 0.0)}, storage_soc_mwh={
                    storage.storage_id: soc}))
            final_soc[storage.storage_id] = periods_result[-1].storage_soc_mwh[storage.storage_id]
            result = DCOPFHorizonResult(
                status="optimal", periods=tuple(periods_result),
                final_soc_mwh_by_storage=final_soc,
            )
            close_executor()
            return result
        for index, result in enumerate(results):
            if abs(points[index] - current_points[index]) > tolerance or not current_results[index].objective_cost == result.objective_cost:
                add_optimality_cut(index, points[index], result)
        current_points = points
        current_results = results

    close_executor()
    raise DCOPFUnresolved(
        f"Benders decomposition did not converge within {max_iterations} iterations"
    )


def _solve_dcopf_horizon_benders_multi(
    *,
    periods: Sequence[tuple[tuple[Bus, ...], tuple[Generator, ...], tuple[Branch, ...]]],
    demand_mw_by_period: Sequence[Mapping[str, float]] | None,
    storage_assets: Sequence[StorageDispatchAsset],
    fixed_injection_mw_by_period: Sequence[Mapping[str, float]] | None,
    owner_load_mw_by_team: Mapping[str, Sequence[Mapping[str, float]]] | None,
    time_step_hours: float,
    base_mva: float,
    tolerance: float,
    max_iterations: int,
    return_result: bool,
    parallel_workers: int,
    solver_threads: int,
    solver_backend: str,
    wall_time_limit_seconds: float | None,
    tighten_feasible_bounds: bool,
) -> DCOPFHorizonResult | str:
    """Generalized exact Benders solve for multiple team-owned storages.

    The master contains only storage trajectories and hourly recourse lower
    bounds.  The network LP is solved independently for each hour, with one
    reusable model per worker.  This is deliberately kept separate from the
    legacy one-storage body above so its parity surface can be tested before
    replacing any older callers.
    """
    period_data = tuple(periods)
    assets = tuple(sorted(storage_assets, key=lambda item: item.storage_id))
    if not assets:
        raise ValueError("multi-storage Benders requires at least one storage asset")
    if not isinstance(max_iterations, int) or max_iterations <= 0:
        raise ValueError("max_iterations must be a positive integer")
    if not isinstance(parallel_workers, int) or parallel_workers <= 0:
        raise ValueError("parallel_workers must be a positive integer")
    if not isinstance(solver_threads, int) or isinstance(solver_threads, bool) or solver_threads <= 0:
        raise ValueError("solver_threads must be a positive integer")
    if solver_backend not in {"highs", "gurobi"}:
        raise ValueError("solver_backend must be 'highs' or 'gurobi'")
    if not _is_finite_real(tolerance) or tolerance <= 0:
        raise ValueError("tolerance must be finite and positive")
    if not period_data:
        raise ValueError("periods must not be empty")
    if not _is_finite_real(time_step_hours) or time_step_hours <= 0:
        raise ValueError("time_step_hours must be finite and positive")
    if not _is_finite_real(base_mva) or base_mva <= 0:
        raise ValueError("base_mva must be finite and positive")
    horizon = len(period_data)
    if demand_mw_by_period is None:
        demand_values = tuple(
            {bus.bus_id: float(bus.demand_mw) for bus in buses}
            for buses, _generators, _branches in period_data
        )
    else:
        demand_values = tuple(dict(values) for values in demand_mw_by_period)
        if len(demand_values) != horizon:
            raise ValueError("demand must match the number of periods")
    if wall_time_limit_seconds is not None and (
        not _is_finite_real(wall_time_limit_seconds) or wall_time_limit_seconds < 0
    ):
        raise ValueError("wall_time_limit_seconds must be finite and non-negative")
    if len({asset.storage_id for asset in assets}) != len(assets):
        raise ValueError("storage IDs must be unique")
    if any(
        not asset.storage_id or not asset.team_id or not asset.bus_id
        or not all(_is_finite_real(value) for value in (asset.power_mw, asset.energy_mwh, asset.initial_soc_mwh))
        or asset.power_mw <= 0
        or asset.energy_mwh <= 0
        or asset.initial_soc_mwh < 0.5 * asset.energy_mwh
        or asset.initial_soc_mwh > asset.energy_mwh
        for asset in assets
    ):
        raise ValueError("invalid storage dimensions or initial SOC")

    fixed = tuple(fixed_injection_mw_by_period or ({},) * horizon)
    if len(fixed) != horizon or any(not isinstance(item, Mapping) for item in fixed):
        raise ValueError("fixed injections must match the number of periods")
    if owner_load_mw_by_team is None:
        raise ValueError("owner load is required when storage assets are present")
    owner_loads: dict[str, tuple[Mapping[str, float], ...]] = {}
    for team_id in {asset.team_id for asset in assets}:
        if team_id not in owner_load_mw_by_team:
            raise ValueError(f"owner load is required for storage team {team_id}")
        values = tuple(owner_load_mw_by_team[team_id])
        if len(values) != horizon or any(not isinstance(item, Mapping) for item in values):
            raise ValueError(f"owner load for {team_id} must match the number of periods")
        normalized_values = []
        for period_load in values:
            normalized = {}
            for bus_id, amount in period_load.items():
                if not isinstance(bus_id, str) or not _is_finite_real(amount) or amount < 0:
                    raise ValueError(f"owner load for {team_id} must be finite and non-negative")
                normalized[bus_id] = float(amount)
            normalized_values.append(normalized)
        owner_loads[team_id] = tuple(normalized_values)

    reference_bus_order = tuple(bus.bus_id for bus in period_data[0][0])
    reference_generators = tuple(period_data[0][1])
    reference_branches = tuple(period_data[0][2])
    for period_index, (buses, generators, branches) in enumerate(period_data):
        if tuple(bus.bus_id for bus in buses) != reference_bus_order:
            raise ValueError(f"Benders requires fixed bus topology at period {period_index}")
        if tuple(generators) != reference_generators or tuple(branches) != reference_branches:
            raise ValueError(f"Benders requires fixed generators and branches at period {period_index}")
        bus_ids = set(reference_bus_order)
        if set(fixed[period_index]) - bus_ids:
            raise ValueError("fixed injection references an unknown bus")
        for asset in assets:
            if asset.bus_id not in bus_ids:
                raise ValueError(f"storage {asset.storage_id} references an unknown bus")
        for values in owner_loads.values():
            if set(values[period_index]) - bus_ids:
                raise ValueError("owner load references an unknown bus")

    started_at = time.perf_counter()
    deadline = None if wall_time_limit_seconds is None else started_at + float(wall_time_limit_seconds)

    def check_deadline(stage: str) -> None:
        if deadline is not None and time.perf_counter() >= deadline:
            raise DCOPFUnresolved(
                f"Benders wall-time budget exceeded during {stage} after "
                f"{time.perf_counter() - started_at:.2f}s"
            )

    executor = ThreadPoolExecutor(max_workers=parallel_workers, thread_name_prefix="dcopf-recourse") if parallel_workers > 1 else None
    worker_state = threading.local()

    def close_executor() -> None:
        nonlocal executor
        if executor is not None:
            executor.shutdown(wait=True)
            executor = None

    def recourse_model():
        model = getattr(worker_state, "model", None)
        if model is None:
            model = _reusable_dcopf_model(
                buses=tuple(period_data[0][0]),
                generators=tuple(period_data[0][1]),
                branches=tuple(period_data[0][2]),
                base_mva=base_mva,
                # Probe columns support valid network-feasibility bounds when
                # the master encounters an infeasible candidate. They are
                # fixed at zero during ordinary recourse solves.
                enable_injection_probes=True,
                solver_threads=solver_threads,
                solver_backend=solver_backend,
            )
            worker_state.model = model
        return model

    recourse_solves = 0
    recourse_counter_lock = threading.Lock()
    recourse_cache: dict[tuple[int, tuple[float, ...]], DCOPFRecourseSummary | DCOPFResult] = {}
    recourse_cache_lock = threading.Lock()
    recourse_cache_hits = 0

    def count_recourse_solve() -> None:
        nonlocal recourse_solves
        with recourse_counter_lock:
            recourse_solves += 1

    def period_buses(period_index: int) -> tuple[Bus, ...]:
        return tuple(
            Bus(bus.bus_id, demand_values[period_index][bus.bus_id], reference=bus.reference)
            for bus in period_data[period_index][0]
        )

    def solve_summary(period_index: int, point: Mapping[str, float]) -> DCOPFRecourseSummary:
        check_deadline(f"recourse period {period_index}")
        injections = dict(fixed[period_index])
        for asset in assets:
            injections[asset.bus_id] = injections.get(asset.bus_id, 0.0) + float(point[asset.storage_id])
        model = recourse_model()
        if deadline is not None:
            model.set_time_limit_seconds(max(0.001, deadline - time.perf_counter()))
        summary = model.solve(
            None,
            fixed_injection_mw_by_bus=injections,
            return_result=False,
            return_summary=True,
            demand_mw_by_bus=demand_values[period_index],
        )
        count_recourse_solve()
        assert isinstance(summary, DCOPFRecourseSummary)
        return summary

    def solve_full(period_index: int, point: Mapping[str, float]) -> DCOPFResult:
        check_deadline(f"final recourse period {period_index}")
        injections = dict(fixed[period_index])
        for asset in assets:
            injections[asset.bus_id] = injections.get(asset.bus_id, 0.0) + float(point[asset.storage_id])
        model = recourse_model()
        if deadline is not None:
            model.set_time_limit_seconds(max(0.001, deadline - time.perf_counter()))
        result = model.solve(
            None,
            fixed_injection_mw_by_bus=injections,
            demand_mw_by_bus=demand_values[period_index],
        )
        count_recourse_solve()
        assert isinstance(result, DCOPFResult)
        return result

    def feasible_interval(asset: StorageDispatchAsset, period_index: int) -> tuple[float, float]:
        check_deadline(f"feasible-bound tightening for period {period_index}")
        model = recourse_model()
        if deadline is not None:
            model.set_time_limit_seconds(max(0.001, deadline - time.perf_counter()))
        return model.feasible_injection_interval(
            period_buses(period_index),
            asset.bus_id,
            lower_bound=-asset.power_mw,
            upper_bound=min(
                asset.power_mw,
                owner_loads[asset.team_id][period_index].get(asset.bus_id, 0.0),
            ),
            fixed_injection_mw_by_bus=fixed[period_index],
        )

    def map_periods(function):
        try:
            if executor is None:
                return [function(index) for index in range(horizon)]
            return list(executor.map(function, range(horizon)))
        except BaseException:
            close_executor()
            raise

    Highs, HighsModelStatus, _HighsVarType, highs_inf = _highs()
    master = Highs()
    master.setOptionValue("output_flag", False)
    master.setOptionValue("presolve", "on")
    if solver_threads > 1:
        master.setOptionValue("parallel", "on")
        master.setOptionValue("threads", solver_threads)
    power_columns: dict[tuple[str, int], object] = {}
    soc_columns: dict[tuple[str, int], object] = {}
    theta_columns = [master.addVariable(lb=-highs_inf, ub=highs_inf, obj=1.0, name=f"recourse-cost:{index}") for index in range(horizon)]
    owner_groups = _group_storage_by_owner_bus(assets)
    physical_bounds: dict[tuple[str, int], tuple[float, float]] = {}
    for asset in assets:
        for period_index in range(horizon):
            owner_load = owner_loads[asset.team_id][period_index].get(asset.bus_id, 0.0)
            physical_bounds[(asset.storage_id, period_index)] = (
                -asset.power_mw,
                min(asset.power_mw, float(owner_load)),
            )
            if tighten_feasible_bounds:
                physical_bounds[(asset.storage_id, period_index)] = feasible_interval(asset, period_index)
            power_columns[(asset.storage_id, period_index)] = master.addVariable(
                lb=physical_bounds[(asset.storage_id, period_index)][0],
                ub=physical_bounds[(asset.storage_id, period_index)][1],
                obj=0.0,
                name=f"storage-power:{asset.storage_id}:{period_index}",
            )
            soc_columns[(asset.storage_id, period_index)] = master.addVariable(
                lb=0.5 * asset.energy_mwh if period_index == horizon - 1 else 0.0,
                ub=asset.energy_mwh,
                obj=0.0,
                name=f"storage-soc:{asset.storage_id}:{period_index}",
            )
            coefficients = {
                int(soc_columns[(asset.storage_id, period_index)].index): 1.0,
                int(power_columns[(asset.storage_id, period_index)].index): time_step_hours,
            }
            if period_index == 0:
                lower = upper = asset.initial_soc_mwh
                previous_soc = asset.initial_soc_mwh
            else:
                coefficients[int(soc_columns[(asset.storage_id, period_index - 1)].index)] = -1.0
                lower = upper = 0.0
                previous_soc = None
            master.addRow(lower, upper, len(coefficients), list(coefficients), list(coefficients.values()))
            discharge_coefficients = {int(power_columns[(asset.storage_id, period_index)].index): time_step_hours}
            if period_index == 0:
                discharge_upper = asset.initial_soc_mwh
            else:
                discharge_coefficients[int(soc_columns[(asset.storage_id, period_index - 1)].index)] = -1.0
                discharge_upper = 0.0
            master.addRow(-highs_inf, discharge_upper, len(discharge_coefficients), list(discharge_coefficients), list(discharge_coefficients.values()))

    for (team_id, bus_id), grouped in owner_groups.items():
        for period_index in range(horizon):
            coefficients = {int(power_columns[(asset.storage_id, period_index)].index): 1.0 for asset in grouped}
            upper = owner_loads[team_id][period_index].get(bus_id, 0.0)
            master.addRow(-highs_inf, float(upper), len(coefficients), list(coefficients), list(coefficients.values()))

    optimality_cuts = 0
    feasibility_cuts = 0
    seen_cuts: set[tuple[object, ...]] = set()

    def add_cut(period_index: int, point: Mapping[str, float], result: DCOPFRecourseSummary) -> None:
        nonlocal optimality_cuts
        gradients = {
            asset.storage_id: -float(result.balance_dual_by_bus[asset.bus_id])
            for asset in assets
        }
        coefficients = {int(theta_columns[period_index].index): 1.0}
        rhs = float(result.objective_cost)
        key_parts: list[object] = [period_index]
        for asset in assets:
            gradient = gradients[asset.storage_id]
            coefficients[int(power_columns[(asset.storage_id, period_index)].index)] = -gradient
            rhs -= gradient * float(point[asset.storage_id])
            key_parts.extend((asset.storage_id, round(gradient, 9), round(float(point[asset.storage_id]), 9)))
        key_parts.append(round(rhs, 9))
        key = tuple(key_parts)
        if key in seen_cuts:
            return
        seen_cuts.add(key)
        master.addRow(rhs, highs_inf, len(coefficients), list(coefficients), list(coefficients.values()))
        optimality_cuts += 1

    zero_point = {asset.storage_id: 0.0 for asset in assets}
    try:
        initial_started = time.perf_counter()
        initial_results = map_periods(lambda index: solve_summary(index, zero_point))
        initial_recourse_seconds = time.perf_counter() - initial_started
        for index, summary in enumerate(initial_results):
            add_cut(index, zero_point, summary)
            if not return_result:
                with recourse_cache_lock:
                    recourse_cache[(index, tuple(0.0 for _ in assets))] = summary

        iterations = 0
        lower_bound = -math.inf
        upper_bound = math.inf
        candidate_recourse_seconds = 0.0
        while iterations < max_iterations:
            check_deadline("master solve")
            iterations += 1
            if deadline is not None:
                master.setOptionValue("time_limit", max(0.001, deadline - time.perf_counter()))
            master.run()
            status = master.getModelStatus()
            if status == getattr(HighsModelStatus, "kInfeasible", None):
                raise DCOPFInfeasible("Benders master became infeasible")
            if status != HighsModelStatus.kOptimal:
                raise DCOPFUnresolved(f"Benders master unresolved: {status}")
            solution = master.getSolution()
            point = {
                asset.storage_id: {
                    index: float(solution.col_value[int(power_columns[(asset.storage_id, index)].index)])
                    for index in range(horizon)
                }
                for asset in assets
            }
            def solve_candidate(index: int):
                candidate = {asset.storage_id: point[asset.storage_id][index] for asset in assets}
                cache_key = (index, tuple(float(candidate[asset.storage_id]) for asset in assets))
                nonlocal recourse_cache_hits
                with recourse_cache_lock:
                    cached = recourse_cache.get(cache_key)
                if cached is not None:
                    with recourse_cache_lock:
                        recourse_cache_hits += 1
                    return cached
                try:
                    result = solve_full(index, candidate) if return_result else solve_summary(index, candidate)
                    with recourse_cache_lock:
                        recourse_cache[cache_key] = result
                    return result
                except DCOPFInfeasible as exc:
                    return exc

            candidate_started = time.perf_counter()
            candidate_results = map_periods(solve_candidate)
            candidate_recourse_seconds += time.perf_counter() - candidate_started
            infeasible_periods = [
                index for index, candidate in enumerate(candidate_results)
                if isinstance(candidate, DCOPFInfeasible)
            ]
            if infeasible_periods:
                changed = False
                for period_index in infeasible_periods:
                    for asset in assets:
                        bounds = feasible_interval(asset, period_index)
                        key = (asset.storage_id, period_index)
                        old_bounds = physical_bounds[key]
                        new_bounds = (
                            max(old_bounds[0], bounds[0]),
                            min(old_bounds[1], bounds[1]),
                        )
                        if new_bounds[0] > new_bounds[1] + tolerance:
                            raise DCOPFInfeasible(
                                f"no valid injection interval for {asset.storage_id} at period {period_index}"
                            )
                        if new_bounds != old_bounds:
                            physical_bounds[key] = new_bounds
                            master.changeColBounds(int(power_columns[key].index), *new_bounds)
                            changed = True
                            feasibility_cuts += 1
                if not changed:
                    raise DCOPFInfeasible(
                        "hourly recourse remained infeasible after valid storage bound tightening: "
                        + ", ".join(map(str, infeasible_periods))
                    )
                continue

            if return_result:
                full_results = tuple(candidate_results)
                summaries = tuple(
                    DCOPFRecourseSummary(
                        status=result.status,
                        objective_cost=result.objective_cost,
                        balance_dual_by_bus=result.balance_dual_by_bus,
                    )
                    for result in full_results
                )
                actual_objective = sum(result.objective_cost for result in full_results)
            else:
                full_results = None
                summaries = tuple(candidate_results)
                actual_objective = sum(summary.objective_cost for summary in summaries)
            lower_bound = float(master.getObjectiveValue())
            upper_bound = actual_objective
            if actual_objective - lower_bound <= tolerance * max(1.0, abs(actual_objective)):
                if not return_result:
                    return "optimal"
                assert full_results is not None
                period_results = []
                for index, result in enumerate(full_results):
                    charge = {}
                    discharge = {}
                    soc = {}
                    for asset in assets:
                        power = point[asset.storage_id][index]
                        previous_soc = asset.initial_soc_mwh if index == 0 else (
                            asset.initial_soc_mwh - time_step_hours * sum(point[asset.storage_id][j] for j in range(index))
                        )
                        soc[asset.storage_id] = previous_soc - time_step_hours * power
                        charge[asset.storage_id] = max(-power, 0.0)
                        discharge[asset.storage_id] = max(power, 0.0)
                    period_results.append(replace(result, storage_charge_mw=charge, storage_discharge_mw=discharge, storage_soc_mwh=soc))
                final_soc = {asset.storage_id: period_results[-1].storage_soc_mwh[asset.storage_id] for asset in assets}
                finalization_started = time.perf_counter()
                elapsed = time.perf_counter() - started_at
                gap = max(0.0, (upper_bound - lower_bound) / max(1.0, abs(upper_bound)))
                finalization_seconds = time.perf_counter() - finalization_started
                return DCOPFHorizonResult(
                    status="optimal",
                    periods=tuple(period_results),
                    final_soc_mwh_by_storage=final_soc,
                    diagnostics=DCOPFBendersDiagnostics(
                        iterations=iterations,
                        solver_backend=solver_backend,
                        recourse_solves=recourse_solves,
                        optimality_cuts=optimality_cuts,
                        feasibility_cuts=feasibility_cuts,
                        lower_bound=lower_bound,
                        upper_bound=upper_bound,
                        relative_gap=gap,
                        elapsed_seconds=elapsed,
                        initial_recourse_seconds=initial_recourse_seconds,
                        candidate_recourse_seconds=candidate_recourse_seconds,
                        finalization_seconds=finalization_seconds,
                        recourse_cache_hits=recourse_cache_hits,
                    ),
                )
            for index, summary in enumerate(summaries):
                add_cut(index, {asset.storage_id: point[asset.storage_id][index] for asset in assets}, summary)
        raise DCOPFUnresolved(f"Benders decomposition did not converge within {max_iterations} iterations")
    finally:
        close_executor()


def _prepared_period_inputs(
    artifact_dir: str | Path,
    artifact: PreparedArtifact,
    *,
    period_index: int,
    additional_demand_mw_by_bus: Mapping[str, float] | None = None,
    additional_generators: Sequence[Generator] = (),
    validate_artifact: bool = True,
) -> tuple[tuple[Bus, ...], tuple[Generator, ...], tuple[Branch, ...], dict[str, float]]:
    """Build one prepared period without solving it."""
    if artifact.scenario_year is not None:
        from ercot_game.scenario_profiles import hourly_state
        from ercot_game.scenario_package import require_validated_block

        if validate_artifact:
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


def _prepared_compact_horizon_inputs(
    artifact_dir: str | Path,
    artifact: PreparedArtifact,
    *,
    period_indices: tuple[int, ...],
    additional_demand_by_period: Sequence[Mapping[str, float]],
    additional_generators_by_period: Sequence[Sequence[Generator]],
) -> tuple[
    tuple[tuple[Bus, ...], tuple[Generator, ...], tuple[Branch, ...]],
    tuple[Mapping[str, float], ...],
    tuple[Mapping[str, float], ...],
] | None:
    """Prepare one static network plus compact hourly demand mappings.

    The exact Benders route only needs hourly demand and fixed injections to
    change.  This helper deliberately falls back when generator topology or
    limits vary between periods, preserving the older fully materialized
    representation for unsupported artifacts.
    """
    static_period = None
    demand_by_period: list[Mapping[str, float]] = []
    fixed_by_period: list[Mapping[str, float]] = []
    for offset, period_index in enumerate(period_indices):
        buses, generators, branches, fixed = _prepared_period_inputs(
            artifact_dir,
            artifact,
            period_index=period_index,
            additional_demand_mw_by_bus=additional_demand_by_period[offset],
            additional_generators=additional_generators_by_period[offset],
            validate_artifact=False,
        )
        if static_period is None:
            static_period = (buses, generators, branches)
        elif generators != static_period[1] or branches != static_period[2]:
            return None
        demand_by_period.append({bus.bus_id: bus.demand_mw for bus in buses})
        fixed_by_period.append(fixed)
    if static_period is None:
        return None
    static_buses = tuple(
        Bus(bus.bus_id, 0.0, reference=bus.reference)
        for bus in static_period[0]
    )
    return (static_buses, static_period[1], static_period[2]), tuple(demand_by_period), tuple(fixed_by_period)


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

    artifact_root = str(Path(artifact_dir).resolve())
    artifact = load_prepared_artifact(artifact_root)
    if artifact.scenario_year is not None:
        from ercot_game.scenario_package import require_validated_block

        require_validated_block(artifact_root)
    prepared_periods = tuple(
        _prepared_period_inputs(
            artifact_root,
            artifact,
            period_index=period_index,
            additional_demand_mw_by_bus=demands[offset],
            additional_generators=generators_by_period[offset],
            validate_artifact=False,
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


def solve_prepared_horizon_benders(
    artifact_dir: str | Path,
    *,
    period_indices: Sequence[int],
    additional_demand_mw_by_period: Sequence[Mapping[str, float]] | None = None,
    additional_generators_by_period: Sequence[Sequence[Generator]] | None = None,
    storage_assets: Sequence[StorageDispatchAsset] = (),
    owner_load_mw_by_team: Mapping[str, Sequence[Mapping[str, float]]] | None = None,
    time_step_hours: float = 1.0,
    max_iterations: int = 200,
    parallel_workers: int = 1,
    solver_threads: int = 1,
    solver_backend: str = "highs",
    wall_time_limit_seconds: float | None = None,
    tighten_feasible_bounds: bool = False,
) -> DCOPFHorizonResult | str:
    """Solve prepared periods with the exact storage-aware Benders route."""
    period_indices = tuple(period_indices)
    if not period_indices:
        raise ValueError("period_indices must not be empty")
    period_count = len(period_indices)
    demands = ({},) * period_count if additional_demand_mw_by_period is None else tuple(additional_demand_mw_by_period)
    generators_by_period = ((),) * period_count if additional_generators_by_period is None else tuple(additional_generators_by_period)
    if len(demands) != period_count:
        raise ValueError("additional demand must match the number of periods")
    if len(generators_by_period) != period_count:
        raise ValueError("additional generators must match the number of periods")
    artifact_root = str(Path(artifact_dir).resolve())
    artifact = load_prepared_artifact(artifact_root)
    if artifact.scenario_year is not None:
        from ercot_game.scenario_package import require_validated_block

        require_validated_block(artifact_root)
    preparation_started = time.perf_counter()
    compact = _prepared_compact_horizon_inputs(
        artifact_root,
        artifact,
        period_indices=period_indices,
        additional_demand_by_period=demands,
        additional_generators_by_period=generators_by_period,
    )
    if compact is None:
        prepared_periods = tuple(
            _prepared_period_inputs(
                artifact_root,
                artifact,
                period_index=period_index,
                additional_demand_mw_by_bus=demands[offset],
                additional_generators=generators_by_period[offset],
                validate_artifact=False,
            )
            for offset, period_index in enumerate(period_indices)
        )
        compact_periods = tuple((buses, generators, branches) for buses, generators, branches, _ in prepared_periods)
        compact_demands = None
        compact_fixed = tuple(fixed for _, _, _, fixed in prepared_periods)
    else:
        static_period, compact_demands, compact_fixed = compact
        compact_periods = (static_period,) * len(period_indices)
    preparation_seconds = time.perf_counter() - preparation_started
    result = solve_dcopf_horizon_benders(
        periods=compact_periods,
        demand_mw_by_period=compact_demands,
        storage_assets=storage_assets,
        fixed_injection_mw_by_period=compact_fixed,
        owner_load_mw_by_team=owner_load_mw_by_team,
        time_step_hours=time_step_hours,
        base_mva=100.0,
        max_iterations=max_iterations,
        parallel_workers=parallel_workers,
        solver_threads=solver_threads,
        solver_backend=solver_backend,
        wall_time_limit_seconds=wall_time_limit_seconds,
        tighten_feasible_bounds=tighten_feasible_bounds,
    )
    if isinstance(result, DCOPFHorizonResult) and result.diagnostics is not None:
        result = replace(
            result,
            diagnostics=replace(
                result.diagnostics,
                preparation_seconds=preparation_seconds,
            ),
        )
    return result
