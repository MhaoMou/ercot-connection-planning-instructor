"""Data contracts and input validation for one-bus hosting-capacity solves."""

from __future__ import annotations

from dataclasses import dataclass, replace
import csv
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import pickle
import tempfile
import time
from collections.abc import Mapping, Sequence
from numbers import Real
from types import MappingProxyType
from typing import Callable, Literal

import numpy as np

import tools.activsg2000.dcopf as dcopf
from tools.activsg2000.dcopf import Branch, Bus, Generator


HostingCapacityStatus = Literal[
    "optimal",
    "infeasible_base_case",
    "unbounded",
    "numerical_failure",
    "invalid_input",
    "solver_error",
    "interrupted",
]

HOSTING_CAPACITY_STATUSES = frozenset(
    {
        "optimal",
        "infeasible_base_case",
        "unbounded",
        "numerical_failure",
        "invalid_input",
        "solver_error",
        "interrupted",
    }
)


@dataclass(frozen=True)
class HostingCapacityPeriod:
    period_index: int
    buses: Sequence[Bus]
    generators: Sequence[Generator]
    branches: Sequence[Branch]
    fixed_injection_mw_by_bus: Mapping[str, float]
    source_metadata: Mapping[str, object]


@dataclass(frozen=True)
class HostingCapacityPoint:
    period_index: int
    bus_id: str
    hosting_capacity_mw: float | None
    status: HostingCapacityStatus
    objective_value: float | None
    solve_time_s: float | None
    binding_resources: Sequence[str]
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, str) or self.status not in HOSTING_CAPACITY_STATUSES:
            raise ValueError(f"unknown hosting-capacity status: {self.status!r}")
        object.__setattr__(self, "binding_resources", tuple(self.binding_resources))

    def to_record(self) -> dict[str, object]:
        return {
            "period_index": self.period_index,
            "bus_id": self.bus_id,
            "hosting_capacity_mw": self.hosting_capacity_mw,
            "status": self.status,
            "objective_value": self.objective_value,
            "solve_time_s": self.solve_time_s,
        }


@dataclass(frozen=True)
class HostingCapacityMatrix:
    """Deterministic period-by-bus hosting-capacity results."""

    period_indices: tuple[int, ...]
    bus_ids: tuple[str, ...]
    capacity_mw: np.ndarray
    status: np.ndarray
    objective_value: np.ndarray
    solve_time_s: np.ndarray
    metadata: Mapping[str, object]


def _finite_real(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _unique_ids(items: Sequence[object], kind: str, attribute: str) -> set[str]:
    ids: set[str] = set()
    for item in items:
        identifier = getattr(item, attribute, None)
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"{kind} ID must be a non-empty string")
        if identifier in ids:
            raise ValueError(f"duplicate {kind} ID: {identifier}")
        ids.add(identifier)
    return ids


def validate_period(period: HostingCapacityPeriod) -> HostingCapacityPeriod:
    """Validate and return an immutable, deterministically ordered period copy."""
    if not isinstance(period, HostingCapacityPeriod):
        raise ValueError("period must be a HostingCapacityPeriod")
    if not isinstance(period.period_index, int) or isinstance(period.period_index, bool):
        raise ValueError("period_index must be an integer")

    buses = tuple(period.buses)
    generators = tuple(period.generators)
    branches = tuple(period.branches)
    if not buses:
        raise ValueError("period must contain at least one bus")
    bus_ids = _unique_ids(buses, "bus", "bus_id")
    generator_ids = _unique_ids(generators, "generator", "generator_id")
    branch_ids = _unique_ids(branches, "branch", "branch_id")

    references = [bus for bus in buses if bus.reference]
    if len(references) > 1:
        raise ValueError("at most one reference bus is allowed")
    if not references:
        buses = tuple(replace(bus, reference=index == 0) for index, bus in enumerate(buses))
    for bus in buses:
        if not _finite_real(bus.demand_mw) or bus.demand_mw < 0:
            raise ValueError(f"bus demand must be finite and nonnegative: {bus.bus_id}")

    for generator in generators:
        if generator.bus_id not in bus_ids:
            raise ValueError(f"generator {generator.generator_id} references unknown bus: {generator.bus_id}")
        if (
            not _finite_real(generator.p_min_mw)
            or not _finite_real(generator.p_max_mw)
            or generator.p_min_mw < 0
            or generator.p_max_mw < generator.p_min_mw
        ):
            raise ValueError(f"invalid generator limits: {generator.generator_id}")
        if not _finite_real(generator.marginal_cost_per_mwh):
            raise ValueError(f"generator cost must be finite: {generator.generator_id}")

    for branch in branches:
        if branch.from_bus_id not in bus_ids or branch.to_bus_id not in bus_ids:
            raise ValueError(f"branch {branch.branch_id} references an unknown bus")
        if not _finite_real(branch.susceptance_pu) or branch.susceptance_pu == 0:
            raise ValueError(f"branch susceptance must be finite and nonzero: {branch.branch_id}")
        if not _finite_real(branch.thermal_limit_mw) or branch.thermal_limit_mw < 0:
            raise ValueError(f"branch thermal limit must be finite and nonnegative: {branch.branch_id}")

    injections = period.fixed_injection_mw_by_bus
    if not isinstance(injections, Mapping):
        raise ValueError("fixed_injection_mw_by_bus must be a mapping")
    normalized_injections: dict[str, float] = {}
    for bus_id, value in injections.items():
        if bus_id not in bus_ids:
            raise ValueError(f"fixed injection references an unknown bus: {bus_id}")
        if not _finite_real(value):
            raise ValueError(f"fixed injection must be finite: {bus_id}")
        normalized_injections[bus_id] = float(value)

    metadata = period.source_metadata
    if not isinstance(metadata, Mapping):
        raise ValueError("source_metadata must be a mapping")

    return HostingCapacityPeriod(
        period_index=period.period_index,
        buses=buses,
        generators=generators,
        branches=branches,
        fixed_injection_mw_by_bus=MappingProxyType(normalized_injections),
        source_metadata=MappingProxyType(dict(metadata)),
    )


def _period_at_index(periods: tuple[HostingCapacityPeriod, ...], period_index: int) -> HostingCapacityPeriod:
    if not isinstance(period_index, int) or isinstance(period_index, bool):
        raise ValueError("period_index must be an integer")
    if not 0 <= period_index < len(periods):
        raise ValueError(f"period_index out of range: {period_index}")
    return periods[period_index]


def _plain_period(period: HostingCapacityPeriod) -> HostingCapacityPeriod:
    """Return a pickle-friendly copy of a normalized period."""
    return HostingCapacityPeriod(
        period_index=period.period_index,
        buses=tuple(period.buses),
        generators=tuple(period.generators),
        branches=tuple(period.branches),
        fixed_injection_mw_by_bus=dict(period.fixed_injection_mw_by_bus),
        source_metadata=dict(period.source_metadata),
    )


@dataclass(frozen=True)
class _StaticPeriodProvider:
    periods: tuple[HostingCapacityPeriod, ...]

    def __post_init__(self) -> None:
        normalized = tuple(validate_period(period) for period in self.periods)
        if not normalized:
            raise ValueError("periods must contain at least one HostingCapacityPeriod")
        object.__setattr__(self, "periods", normalized)

    def __call__(self, period_index: int) -> HostingCapacityPeriod:
        return _period_at_index(self.periods, period_index)

    def __getstate__(self) -> dict[str, object]:
        return {"periods": tuple(_plain_period(period) for period in self.periods)}

    def __setstate__(self, state: Mapping[str, object]) -> None:
        object.__setattr__(
            self,
            "periods",
            tuple(validate_period(period) for period in state["periods"]),
        )


def _artifact_path_identity(artifact_dir: Path) -> str:
    digest = hashlib.sha256(str(artifact_dir).encode("utf-8")).hexdigest()[:12]
    return f"{artifact_dir.name}:{digest}"


@dataclass(frozen=True)
class _PreparedArtifactPeriodProvider:
    artifact_dir: str

    def __call__(self, period_index: int) -> HostingCapacityPeriod:
        scenario_year, scenario_block = dcopf.prepared_artifact_scenario_metadata(self.artifact_dir)
        buses, generators, branches, fixed_injections = dcopf.prepared_period_inputs_from_path(
            self.artifact_dir,
            period_index,
        )
        period = HostingCapacityPeriod(
            period_index=period_index,
            buses=buses,
            generators=generators,
            branches=branches,
            fixed_injection_mw_by_bus=fixed_injections,
            source_metadata={
                "source": "prepared_artifact",
                "artifact_path_identity": _artifact_path_identity(Path(self.artifact_dir)),
                "artifact_dir": Path(self.artifact_dir).name,
                "scenario_year": scenario_year,
                "scenario_block": scenario_block,
                "period_index": period_index,
            },
        )
        return validate_period(period)


def static_period_provider(period: HostingCapacityPeriod) -> Callable[[int], HostingCapacityPeriod]:
    """Build a pickleable provider for one normalized static period."""
    return _StaticPeriodProvider((period,))


def prepared_artifact_period_provider(artifact_dir: str | Path) -> Callable[[int], HostingCapacityPeriod]:
    """Build a lazily materialized provider for a validated prepared block."""
    root = Path(artifact_dir).resolve()
    from ercot_game.scenario_package import require_validated_block

    require_validated_block(root)
    return _PreparedArtifactPeriodProvider(str(root))


def ieee14_period_provider(
    periods: Sequence[HostingCapacityPeriod] | None = None,
) -> Callable[[int], HostingCapacityPeriod]:
    """Build a static provider from caller-supplied normalized IEEE-14 periods."""
    if periods is None:
        raise ValueError("provide at least one normalized IEEE-14 period; no dataset is bundled")
    return _StaticPeriodProvider(tuple(periods))


def solve_hosting_capacity_at_bus(
    period: HostingCapacityPeriod,
    candidate_bus_id: str,
    *,
    solver_backend: str = "auto",
    solver_threads: int = 1,
    upper_bound_mw: float | None = None,
) -> HostingCapacityPoint:
    """Solve the single-period maximum additional demand at one bus.

    The Task 2 reusable model owns the LP construction and canonical solver
    status mapping.  This function only validates the public contract,
    selects the backend, and translates the probe result to a stable point
    record.  No generator, storage, or intertemporal state is introduced.
    """
    started = time.perf_counter()

    def exception_diagnostic(stage: str, error: BaseException) -> str:
        detail = str(error)
        suffix = f": {detail}" if detail else ""
        return f"{stage}: {type(error).__name__}{suffix}"

    def elapsed() -> float:
        return max(0.0, time.perf_counter() - started)

    raw_period_index = getattr(period, "period_index", -1)
    period_index = (
        raw_period_index
        if isinstance(raw_period_index, int) and not isinstance(raw_period_index, bool)
        else -1
    )

    def point(
        status: HostingCapacityStatus,
        *,
        capacity: float | None = None,
        objective_value: float | None = None,
        diagnostic: str | None = None,
    ) -> HostingCapacityPoint:
        return HostingCapacityPoint(
            period_index=period_index,
            bus_id=candidate_bus_id,
            hosting_capacity_mw=capacity,
            status=status,
            objective_value=objective_value,
            solve_time_s=elapsed(),
            binding_resources=(),
            diagnostic=diagnostic,
        )

    try:
        normalized_period = validate_period(period)
    except KeyboardInterrupt as error:
        return point("interrupted", diagnostic=exception_diagnostic("period validation interrupted", error))
    except Exception as error:
        return point("invalid_input", diagnostic=exception_diagnostic("period validation failed", error))

    period_index = normalized_period.period_index
    bus_ids = {bus.bus_id for bus in normalized_period.buses}
    if not isinstance(candidate_bus_id, str) or not candidate_bus_id or candidate_bus_id not in bus_ids:
        return point("invalid_input")
    if (
        not isinstance(solver_threads, int)
        or isinstance(solver_threads, bool)
        or solver_threads <= 0
    ):
        return point("invalid_input")
    if upper_bound_mw is not None and (
        not _finite_real(upper_bound_mw) or upper_bound_mw < 0
    ):
        return point("invalid_input")

    try:
        resolved_backend = dcopf.resolve_solver_backend(solver_backend)
    except KeyboardInterrupt as error:
        return point("interrupted", diagnostic=exception_diagnostic("backend resolution interrupted", error))
    except (TypeError, ValueError) as error:
        return point("invalid_input", diagnostic=exception_diagnostic("backend resolution failed", error))
    except Exception as error:
        return point("solver_error", diagnostic=exception_diagnostic("backend resolution failed", error))

    try:
        if resolved_backend == "highs":
            model = dcopf.ReusableDCOPFModel(
                buses=tuple(normalized_period.buses),
                generators=tuple(normalized_period.generators),
                branches=tuple(normalized_period.branches),
                enable_candidate_demand_probes=True,
                candidate_demand_bus_id=candidate_bus_id,
                solver_threads=solver_threads,
            )
        elif resolved_backend == "gurobi":
            model = dcopf.GurobiReusableDCOPFModel(
                buses=tuple(normalized_period.buses),
                generators=tuple(normalized_period.generators),
                branches=tuple(normalized_period.branches),
                enable_candidate_demand_probes=True,
                candidate_demand_bus_id=candidate_bus_id,
                solver_threads=solver_threads,
            )
        else:
            return point(
                "solver_error",
                diagnostic=f"backend construction returned unsupported backend: {resolved_backend!r}",
            )
    except KeyboardInterrupt as error:
        return point("interrupted", diagnostic=exception_diagnostic("backend construction interrupted", error))
    except (TypeError, ValueError) as error:
        return point("invalid_input", diagnostic=exception_diagnostic("backend construction failed", error))
    except Exception as error:
        return point("solver_error", diagnostic=exception_diagnostic("backend construction failed", error))

    try:
        capacity, status = model.max_additional_demand(
            tuple(normalized_period.buses),
            candidate_bus_id,
            upper_bound_mw=upper_bound_mw,
            fixed_injection_mw_by_bus=normalized_period.fixed_injection_mw_by_bus,
        )
    except KeyboardInterrupt as error:
        return point("interrupted", diagnostic=exception_diagnostic("solver execution interrupted", error))
    except (InterruptedError, TimeoutError) as error:
        return point("interrupted", diagnostic=exception_diagnostic("solver execution interrupted", error))
    except (ArithmeticError, FloatingPointError, OverflowError) as error:
        return point("numerical_failure", diagnostic=exception_diagnostic("solver execution failed", error))
    except Exception as error:
        return point("solver_error", diagnostic=exception_diagnostic("solver execution failed", error))

    if not isinstance(status, str):
        return point("solver_error", diagnostic=f"malformed backend status: {status!r}")
    if status not in HOSTING_CAPACITY_STATUSES:
        return point("solver_error", diagnostic=f"unsupported backend status: {status!r}")
    if status != "optimal":
        return point(status)  # type: ignore[arg-type]
    if not _finite_real(capacity) or capacity < 0:
        return point("numerical_failure", diagnostic=f"invalid optimal capacity returned by backend: {capacity!r}")

    capacity = float(capacity)
    return point("optimal", capacity=capacity, objective_value=capacity)


_HOSTING_CAPACITY_FORMULATION = "one_bus_additional_demand_v1"
_HOSTING_CAPACITY_SCHEMA_VERSION = 1


def _sorted_unique_ints(values: Sequence[int], *, kind: str) -> tuple[int, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{kind} must be a sequence of integers")
    normalized: list[int] = []
    for value in values:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{kind} must contain only integers")
        if value < 0:
            raise ValueError(f"{kind} must contain only nonnegative integers")
        normalized.append(value)
    return tuple(sorted(set(normalized)))


def _discover_period_indices(period_provider: Callable[[int], HostingCapacityPeriod]) -> tuple[int, ...]:
    """Discover provider call indices without materializing solver objects."""
    advertised = getattr(period_provider, "period_indices", None)
    if advertised is not None:
        values = advertised() if callable(advertised) else advertised
        return _sorted_unique_ints(tuple(values), kind="period_indices")

    periods = getattr(period_provider, "periods", None)
    if periods is not None:
        try:
            return tuple(range(len(periods)))
        except TypeError:
            pass

    period_count = getattr(period_provider, "period_count", None)
    if period_count is not None:
        if not isinstance(period_count, int) or isinstance(period_count, bool) or period_count < 0:
            raise ValueError("period_count must be a nonnegative integer")
        return tuple(range(period_count))

    artifact_dir = getattr(period_provider, "artifact_dir", None)
    if artifact_dir is not None:
        manifest_path = Path(artifact_dir) / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            horizon = manifest.get("horizon")
        except (OSError, TypeError, ValueError):
            horizon = None
        if isinstance(horizon, int) and not isinstance(horizon, bool) and horizon >= 0:
            return tuple(range(horizon))

    raise ValueError("period_indices must be provided when the provider cannot discover all periods")


def _period_for_requested_index(
    period_provider: Callable[[int], HostingCapacityPeriod],
    requested_period_index: int,
) -> HostingCapacityPeriod:
    if not isinstance(requested_period_index, int) or isinstance(requested_period_index, bool):
        raise ValueError("period_index must be an integer")
    if requested_period_index < 0:
        raise ValueError("period_index must be nonnegative")
    period = validate_period(period_provider(requested_period_index))
    if period.period_index != requested_period_index:
        raise ValueError(
            "provider returned period_index "
            f"{period.period_index} for requested period_index {requested_period_index}"
        )
    return period


def _json_safe(value: object, *, key: str = "") -> object:
    """Make metadata deterministic and remove absolute local paths."""
    if isinstance(value, Mapping):
        return {
            str(name): _json_safe(item, key=str(name))
            for name, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_json_safe(item, key=key) for item in value]
    if isinstance(value, Path):
        return value.name
    if isinstance(value, np.generic):
        return _json_safe(value.item(), key=key)
    if isinstance(value, str):
        if os.path.isabs(value) or "path" in key.lower() or key.lower().endswith("_dir"):
            return Path(value).name
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return repr(value)


def _canonical_json(value: object) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"))


def _normalized_period_inputs(period: HostingCapacityPeriod) -> dict[str, object]:
    return {
        "period_index": period.period_index,
        "buses": [
            {
                "bus_id": bus.bus_id,
                "demand_mw": float(bus.demand_mw),
                "reference": bool(bus.reference),
            }
            for bus in period.buses
        ],
        "generators": [
            {
                "generator_id": generator.generator_id,
                "bus_id": generator.bus_id,
                "p_min_mw": float(generator.p_min_mw),
                "p_max_mw": float(generator.p_max_mw),
                "marginal_cost_per_mwh": float(generator.marginal_cost_per_mwh),
            }
            for generator in period.generators
        ],
        "branches": [
            {
                "branch_id": branch.branch_id,
                "from_bus_id": branch.from_bus_id,
                "to_bus_id": branch.to_bus_id,
                "susceptance_pu": float(branch.susceptance_pu),
                "thermal_limit_mw": float(branch.thermal_limit_mw),
            }
            for branch in period.branches
        ],
        "fixed_injection_mw_by_bus": [
            [str(bus_id), float(value)]
            for bus_id, value in sorted(period.fixed_injection_mw_by_bus.items())
        ],
    }


def _fixed_topology_signature(period: HostingCapacityPeriod) -> dict[str, object]:
    """Describe fixed structure while allowing period-specific operating limits."""
    return {
        "bus_order": [
            {"bus_id": bus.bus_id, "reference": bool(bus.reference)}
            for bus in period.buses
        ],
        "generators": [
            {
                "generator_id": generator.generator_id,
                "bus_id": generator.bus_id,
            }
            for generator in period.generators
        ],
        "branches": [
            {
                "branch_id": branch.branch_id,
                "from_bus_id": branch.from_bus_id,
                "to_bus_id": branch.to_bus_id,
                "susceptance_pu": float(branch.susceptance_pu),
            }
            for branch in period.branches
        ],
    }


def _validate_fixed_topology(
    periods: Mapping[int, HostingCapacityPeriod],
    period_indices: tuple[int, ...],
) -> None:
    reference_period = periods[period_indices[0]]
    reference_bus_order = tuple(bus.bus_id for bus in reference_period.buses)
    reference_signature = _fixed_topology_signature(reference_period)
    for period_index in period_indices[1:]:
        period = periods[period_index]
        candidate_bus_order = tuple(bus.bus_id for bus in period.buses)
        if candidate_bus_order != reference_bus_order:
            raise ValueError(f"period {period_index} has a different bus order")
        if _fixed_topology_signature(period) != reference_signature:
            raise ValueError(f"period {period_index} has a different fixed topology")


def _source_identity(periods: Sequence[HostingCapacityPeriod]) -> tuple[str, object, object]:
    source_metadata = [_json_safe(period.source_metadata) for period in periods]
    period_inputs = [_normalized_period_inputs(period) for period in periods]
    identity_payload = {
        "period_inputs": period_inputs,
        "source_metadata": source_metadata,
    }
    encoded = _canonical_json(identity_payload).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    scenario_identity: dict[str, object] = {}
    for metadata in source_metadata:
        if not isinstance(metadata, Mapping):
            continue
        for key, value in metadata.items():
            lowered = str(key).lower()
            if (
                "scenario" in lowered
                or "artifact" in lowered
                or lowered in {"source", "year", "block", "package_version"}
            ):
                scenario_identity[str(key)] = value
    return f"source:{digest}", source_metadata, scenario_identity


def _period_fingerprint(period: HostingCapacityPeriod) -> str:
    """Return a deterministic identity for one normalized provider result."""
    payload = {
        "period_inputs": _normalized_period_inputs(period),
        "source_metadata": _json_safe(period.source_metadata),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _solver_version(resolved_backend: str) -> str:
    try:
        if resolved_backend == "highs":
            import highspy

            return str(getattr(highspy, "__version__", "unknown"))
        import gurobipy

        return str(gurobipy.gurobi.version())
    except Exception:
        return "unavailable"


def _new_period_arrays(bus_ids: tuple[str, ...]) -> dict[str, np.ndarray]:
    count = len(bus_ids)
    return {
        "capacity_mw": np.full(count, np.nan, dtype=float),
        "status": np.full(count, "", dtype="<U32"),
        "objective_value": np.full(count, np.nan, dtype=float),
        "solve_time_s": np.full(count, np.nan, dtype=float),
        "diagnostic": np.full(count, "", dtype="<U512"),
    }


def _valid_optimal_value(value: object) -> bool:
    return _finite_real(value) and float(value) >= 0.0


def _solve_matrix_period(
    period: HostingCapacityPeriod,
    bus_ids: tuple[str, ...],
    *,
    solver_backend: str,
    solver_threads: int,
) -> dict[str, np.ndarray]:
    """Solve all candidate buses against one reusable model."""
    normalized_period = validate_period(period)
    results = _new_period_arrays(bus_ids)
    started = time.perf_counter()

    def exception_diagnostic(stage: str, error: BaseException) -> str:
        detail = str(error)
        suffix = f": {detail}" if detail else ""
        return f"{stage}: {type(error).__name__}{suffix}"

    try:
        model = dcopf._reusable_dcopf_model(
            solver_backend=solver_backend,
            buses=tuple(normalized_period.buses),
            generators=tuple(normalized_period.generators),
            branches=tuple(normalized_period.branches),
            enable_candidate_demand_probes=True,
            candidate_demand_bus_id=None,
            solver_threads=solver_threads,
        )
    except (InterruptedError, TimeoutError) as error:
        results["status"][:] = "interrupted"
        results["diagnostic"][:] = exception_diagnostic("backend construction interrupted", error)
        results["solve_time_s"][:] = max(0.0, time.perf_counter() - started)
        return results
    except KeyboardInterrupt:
        raise
    except Exception as error:
        results["status"][:] = "solver_error"
        results["diagnostic"][:] = exception_diagnostic("backend construction failed", error)
        results["solve_time_s"][:] = max(0.0, time.perf_counter() - started)
        return results

    for position, bus_id in enumerate(bus_ids):
        point_started = time.perf_counter()
        diagnostic = ""
        try:
            capacity, status = model.max_additional_demand(
                tuple(normalized_period.buses),
                bus_id,
                fixed_injection_mw_by_bus=normalized_period.fixed_injection_mw_by_bus,
            )
        except (InterruptedError, TimeoutError) as error:
            status = "interrupted"
            capacity = math.nan
            diagnostic = exception_diagnostic("solver execution interrupted", error)
        except KeyboardInterrupt:
            raise
        except (ArithmeticError, FloatingPointError, OverflowError) as error:
            status = "numerical_failure"
            capacity = math.nan
            diagnostic = exception_diagnostic("solver execution failed", error)
        except Exception as error:
            status = "solver_error"
            capacity = math.nan
            diagnostic = exception_diagnostic("solver execution failed", error)

        if not isinstance(status, str) or status not in HOSTING_CAPACITY_STATUSES:
            diagnostic = f"unsupported backend status: {status!r}"
            status = "solver_error"
            capacity = math.nan
        elif status == "optimal" and not _valid_optimal_value(capacity):
            diagnostic = f"invalid optimal capacity returned by backend: {capacity!r}"
            status = "numerical_failure"
            capacity = math.nan
        results["status"][position] = status
        results["diagnostic"][position] = diagnostic
        results["solve_time_s"][position] = max(0.0, time.perf_counter() - point_started)
        if status == "optimal" and _finite_real(capacity) and float(capacity) >= 0:
            results["capacity_mw"][position] = float(capacity)
            results["objective_value"][position] = float(capacity)
    return results


def _matrix_period_worker(
    task: tuple[object, int, tuple[str, ...], str, int, str],
) -> tuple[int, dict[str, np.ndarray]]:
    """Spawn-safe worker; the provider and model are created in the child."""
    period_provider, period_index, bus_ids, solver_backend, solver_threads, expected_fingerprint = task
    period = _period_for_requested_index(period_provider, period_index)  # type: ignore[arg-type]
    if _period_fingerprint(period) != expected_fingerprint:
        raise ValueError(f"period identity mismatch for period {period_index}")
    return period_index, _solve_matrix_period(
        period,
        bus_ids,
        solver_backend=solver_backend,
        solver_threads=solver_threads,
    )


def _coerce_period_result(
    result: Mapping[str, object],
    bus_ids: tuple[str, ...],
) -> dict[str, np.ndarray]:
    expected = len(bus_ids)
    try:
        capacities = np.asarray(result["capacity_mw"], dtype=float).reshape(-1)
        statuses = np.asarray(result["status"], dtype="<U32").reshape(-1)
        objectives = np.asarray(result["objective_value"], dtype=float).reshape(-1)
        solve_times = np.asarray(result["solve_time_s"], dtype=float).reshape(-1)
        diagnostics = np.asarray(result.get("diagnostic", np.full(expected, "")), dtype="<U512").reshape(-1)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("period solver returned malformed arrays") from error
    if any(array.size != expected for array in (capacities, statuses, objectives, solve_times, diagnostics)):
        raise ValueError("period solver returned arrays with the wrong shape")
    normalized = _new_period_arrays(bus_ids)
    for position in range(expected):
        status = statuses[position]
        if not isinstance(status, str) or status not in HOSTING_CAPACITY_STATUSES:
            status = "solver_error"
            diagnostics[position] = "malformed period solver status"
        normalized["status"][position] = status
        if isinstance(diagnostics[position], str):
            normalized["diagnostic"][position] = diagnostics[position]
        if _finite_real(solve_times[position]):
            normalized["solve_time_s"][position] = float(solve_times[position])
        if status == "optimal" and (
            not _valid_optimal_value(capacities[position])
            or not _valid_optimal_value(objectives[position])
        ):
            normalized["status"][position] = "numerical_failure"
            normalized["diagnostic"][position] = "invalid optimal capacity or objective value"
            continue
        if status == "optimal":
            normalized["capacity_mw"][position] = float(capacities[position])
            normalized["objective_value"][position] = float(objectives[position])
    return normalized


def _atomic_save_npz(path: Path, **arrays: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            np.savez_compressed(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _atomic_write_bytes(path: Path, writer: Callable[[object], None], mode: str = "wb") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode=mode, prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary_path = Path(handle.name)
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _checkpoint_path(checkpoint_dir: Path, period_index: int) -> Path:
    return checkpoint_dir / f"period_{period_index}.npz"


def _write_period_checkpoint(
    path: Path,
    *,
    period_index: int,
    identity: Mapping[str, object],
    result: Mapping[str, np.ndarray],
) -> None:
    _atomic_save_npz(
        path,
        period_index=np.asarray(period_index, dtype=np.int64),
        identity=np.asarray(_canonical_json(identity)),
        capacity_mw=result["capacity_mw"],
        status=result["status"],
        objective_value=result["objective_value"],
        solve_time_s=result["solve_time_s"],
        diagnostic=result["diagnostic"],
        complete=np.asarray(True),
    )


def _load_period_checkpoint(
    path: Path,
    *,
    period_index: int,
    identity: Mapping[str, object],
    bus_count: int,
) -> dict[str, np.ndarray]:
    try:
        with np.load(path, allow_pickle=False) as checkpoint:
            saved_period = int(np.asarray(checkpoint["period_index"]).item())
            saved_identity = json.loads(str(np.asarray(checkpoint["identity"]).item()))
            if saved_period != period_index or saved_identity != _json_safe(identity):
                raise ValueError("checkpoint identity mismatch")
            if not bool(np.asarray(checkpoint["complete"]).item()):
                raise ValueError("checkpoint is not complete")
            result = {
                "capacity_mw": np.asarray(checkpoint["capacity_mw"], dtype=float),
                "status": np.asarray(checkpoint["status"], dtype="<U32"),
                "objective_value": np.asarray(checkpoint["objective_value"], dtype=float),
                "solve_time_s": np.asarray(checkpoint["solve_time_s"], dtype=float),
                "diagnostic": np.asarray(checkpoint["diagnostic"], dtype="<U512"),
            }
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"invalid checkpoint: {path.name}") from error
    if any(array.shape != (bus_count,) for array in result.values()):
        raise ValueError("checkpoint shape mismatch")
    return result


def _matrix_metadata(
    *,
    identity: Mapping[str, object],
    source_metadata: object,
    scenario_identity: object,
    resolved_backend: str,
    solver_threads: int,
    workers: int,
    period_indices: tuple[int, ...],
    bus_ids: tuple[str, ...],
    capacity_mw: np.ndarray,
    status: np.ndarray,
    diagnostics: np.ndarray,
    completed_periods: set[int],
    requested_backend: str,
) -> dict[str, object]:
    optimal_count = int(np.count_nonzero(status == "optimal"))
    total_count = int(status.size)
    completed_count = int(np.count_nonzero(status != ""))
    failed_count = int(np.count_nonzero((status != "") & (status != "optimal")))
    pending_count = total_count - completed_count
    diagnostic_records: dict[str, dict[str, str]] = {}
    for row_index, period_index in enumerate(period_indices):
        row_diagnostics = {
            bus_id: str(diagnostics[row_index, bus_index])
            for bus_index, bus_id in enumerate(bus_ids)
            if str(diagnostics[row_index, bus_index])
        }
        if row_diagnostics:
            diagnostic_records[str(period_index)] = row_diagnostics
    return {
        "schema_version": _HOSTING_CAPACITY_SCHEMA_VERSION,
        "formulation": _HOSTING_CAPACITY_FORMULATION,
        "identity": _json_safe(identity),
        "source_identity": identity.get("source_identity"),
        "scenario_artifact_identity": scenario_identity,
        "source_metadata": source_metadata,
        "solver_backend": requested_backend,
        "resolved_solver_backend": resolved_backend,
        "requested_solver_backend": requested_backend,
        "solver_threads": solver_threads,
        "backend": {
            "requested": requested_backend,
            "resolved": resolved_backend,
            "version": _solver_version(resolved_backend),
        },
        "settings": {"solver_threads": solver_threads, "workers": workers},
        "tolerances": {"capacity_mw": 1e-9},
        "counts": {
            "period_count": len(period_indices),
            "bus_count": len(bus_ids),
            "point_count": total_count,
            "completed_period_count": len(completed_periods),
            "completed_count": completed_count,
            "optimal_count": optimal_count,
            "failed_count": failed_count,
            "pending_count": pending_count,
        },
        "diagnostics": diagnostic_records,
        "complete": len(completed_periods) == len(period_indices),
        "warm_start_used": False,
        "ordering": {
            "period_indices": list(period_indices),
            "bus_ids": list(bus_ids),
            "array_axes": ["period_index", "bus_id"],
        },
    }


def _write_matrix_outputs(
    output_dir: Path,
    *,
    period_indices: tuple[int, ...],
    bus_ids: tuple[str, ...],
    capacity_mw: np.ndarray,
    status: np.ndarray,
    metadata: Mapping[str, object],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    def save_npy(path: Path, array: np.ndarray) -> None:
        _atomic_write_bytes(path, lambda handle: np.save(handle, array, allow_pickle=False))

    save_npy(output_dir / "hosting_capacity_mw.npy", capacity_mw)
    save_npy(output_dir / "hosting_capacity_status.npy", status)
    _atomic_write_bytes(
        output_dir / "hosting_capacity_metadata.json",
        lambda handle: json.dump(_json_safe(metadata), handle, sort_keys=True, indent=2),
        mode="w",
    )

    def write_summary(handle: object) -> None:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "period_index",
                "completed_count",
                "optimal_count",
                "failed_count",
                "pending_count",
                "min_hosting_capacity_mw",
                "median_hosting_capacity_mw",
                "max_hosting_capacity_mw",
            ),
        )
        writer.writeheader()
        for row_index, period_index in enumerate(period_indices):
            completed = status[row_index] != ""
            optimal = status[row_index] == "optimal"
            failed = completed & ~optimal
            pending = ~completed
            values = capacity_mw[row_index][optimal]
            writer.writerow(
                {
                    "period_index": period_index,
                    "completed_count": int(np.count_nonzero(completed)),
                    "optimal_count": int(np.count_nonzero(optimal)),
                    "failed_count": int(np.count_nonzero(failed)),
                    "pending_count": int(np.count_nonzero(pending)),
                    "min_hosting_capacity_mw": float(np.min(values)) if values.size else math.nan,
                    "median_hosting_capacity_mw": float(np.median(values)) if values.size else math.nan,
                    "max_hosting_capacity_mw": float(np.max(values)) if values.size else math.nan,
                }
            )

    _atomic_write_bytes(output_dir / "hosting_capacity_summary.csv", write_summary, mode="w")


def compute_hosting_capacity_matrix(
    period_provider: Callable[[int], HostingCapacityPeriod],
    *,
    bus_ids: Sequence[str] | None = None,
    period_indices: Sequence[int] | None = None,
    solver_backend: str = "auto",
    solver_threads: int = 1,
    workers: int = 1,
    checkpoint_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> HostingCapacityMatrix:
    """Compute a deterministic period-by-bus hosting-capacity matrix."""
    if not callable(period_provider):
        raise ValueError("period_provider must be callable")
    if not isinstance(solver_threads, int) or isinstance(solver_threads, bool) or solver_threads < 1:
        raise ValueError("solver_threads must be at least 1")
    if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
        raise ValueError("workers must be at least 1")
    if workers > 1 and solver_threads > 1:
        raise ValueError("workers > 1 cannot be combined with solver_threads > 1")
    try:
        resolved_backend = dcopf.resolve_solver_backend(solver_backend)
    except (TypeError, ValueError) as error:
        raise ValueError(str(error)) from error

    selected_indices = (
        _discover_period_indices(period_provider)
        if period_indices is None
        else _sorted_unique_ints(tuple(period_indices), kind="period_indices")
    )
    if not selected_indices:
        raise ValueError("at least one period must be selected")

    normalized_periods: dict[int, HostingCapacityPeriod] = {}
    for period_index in selected_indices:
        try:
            period = _period_for_requested_index(period_provider, period_index)
        except KeyboardInterrupt:
            raise
        except Exception as error:
            raise ValueError(f"invalid selected period {period_index}: {error}") from error
        normalized_periods[period_index] = period

    _validate_fixed_topology(normalized_periods, selected_indices)
    reference_bus_order = tuple(bus.bus_id for bus in normalized_periods[selected_indices[0]].buses)

    if bus_ids is None:
        selected_bus_ids = tuple(sorted(reference_bus_order))
    else:
        if isinstance(bus_ids, (str, bytes)):
            raise ValueError("bus_ids must be a sequence of strings")
        requested_bus_ids = tuple(bus_ids)
        if any(not isinstance(bus_id, str) or not bus_id for bus_id in requested_bus_ids):
            raise ValueError("bus_ids must contain non-empty strings")
        selected_bus_ids = tuple(sorted(set(requested_bus_ids)))
        missing = sorted(set(selected_bus_ids) - set(reference_bus_order))
        if missing:
            raise ValueError(f"requested bus does not exist: {missing[0]}")

    source_id, source_metadata, scenario_identity = _source_identity(
        [normalized_periods[index] for index in selected_indices]
    )
    identity = {
        "schema_version": _HOSTING_CAPACITY_SCHEMA_VERSION,
        "formulation": _HOSTING_CAPACITY_FORMULATION,
        "period_indices": list(selected_indices),
        "bus_ids": list(selected_bus_ids),
        "solver_backend": resolved_backend,
        "solver_threads": solver_threads,
        "source_identity": source_id,
    }

    if workers > 1:
        try:
            pickle.dumps(period_provider)
        except Exception as error:
            raise ValueError("period_provider must be pickleable when workers > 1") from error

    checkpoint_root = Path(checkpoint_dir) if checkpoint_dir is not None else None
    output_root = Path(output_dir) if output_dir is not None else None
    capacity_mw = np.full((len(selected_indices), len(selected_bus_ids)), np.nan, dtype=float)
    status = np.full((len(selected_indices), len(selected_bus_ids)), "", dtype="<U32")
    objective_value = np.full_like(capacity_mw, np.nan)
    solve_time_s = np.full_like(capacity_mw, np.nan)
    diagnostics = np.full((len(selected_indices), len(selected_bus_ids)), "", dtype="<U512")
    completed_periods: set[int] = set()

    if checkpoint_root is not None:
        checkpoint_root.mkdir(parents=True, exist_ok=True)
        for path in sorted(checkpoint_root.glob("period_*.npz")):
            try:
                checkpoint_period_index = int(path.stem.removeprefix("period_"))
            except ValueError as error:
                raise ValueError(f"invalid checkpoint name: {path.name}") from error
            if checkpoint_period_index not in normalized_periods:
                raise ValueError("checkpoint identity mismatch")
            period_result = _load_period_checkpoint(
                path,
                period_index=checkpoint_period_index,
                identity=identity,
                bus_count=len(selected_bus_ids),
            )
            row_index = selected_indices.index(checkpoint_period_index)
            for name, matrix in (
                ("capacity_mw", capacity_mw),
                ("status", status),
                ("objective_value", objective_value),
                ("solve_time_s", solve_time_s),
                ("diagnostic", diagnostics),
            ):
                matrix[row_index] = period_result[name]
            completed_periods.add(checkpoint_period_index)

    def publish() -> None:
        metadata = _matrix_metadata(
            identity=identity,
            source_metadata=source_metadata,
            scenario_identity=scenario_identity,
            resolved_backend=resolved_backend,
            solver_threads=solver_threads,
            workers=workers,
            period_indices=selected_indices,
            bus_ids=selected_bus_ids,
            capacity_mw=capacity_mw,
            status=status,
            diagnostics=diagnostics,
            completed_periods=completed_periods,
            requested_backend=solver_backend,
        )
        if output_root is not None:
            _write_matrix_outputs(
                output_root,
                period_indices=selected_indices,
                bus_ids=selected_bus_ids,
                capacity_mw=capacity_mw,
                status=status,
                metadata=metadata,
            )

    pending = [index for index in selected_indices if index not in completed_periods]

    def record_period(period_index: int, raw_result: Mapping[str, object]) -> None:
        period_result = _coerce_period_result(raw_result, selected_bus_ids)
        row_index = selected_indices.index(period_index)
        for name, matrix in (
            ("capacity_mw", capacity_mw),
            ("status", status),
            ("objective_value", objective_value),
            ("solve_time_s", solve_time_s),
            ("diagnostic", diagnostics),
        ):
            matrix[row_index] = period_result[name]
        completed_periods.add(period_index)
        if checkpoint_root is not None:
            _write_period_checkpoint(
                _checkpoint_path(checkpoint_root, period_index),
                period_index=period_index,
                identity=identity,
                result=period_result,
            )
        publish()

    if workers == 1:
        for period_index in pending:
            record_period(
                period_index,
                _solve_matrix_period(
                    normalized_periods[period_index],
                    selected_bus_ids,
                    solver_backend=resolved_backend,
                    solver_threads=solver_threads,
                ),
            )
    elif pending:
        context = multiprocessing.get_context("spawn")
        tasks = [
            (
                period_provider,
                period_index,
                selected_bus_ids,
                resolved_backend,
                solver_threads,
                _period_fingerprint(normalized_periods[period_index]),
            )
            for period_index in pending
        ]
        with context.Pool(processes=workers) as pool:
            for period_index, raw_result in pool.imap_unordered(_matrix_period_worker, tasks):
                record_period(period_index, raw_result)

    publish()
    metadata = _matrix_metadata(
        identity=identity,
        source_metadata=source_metadata,
        scenario_identity=scenario_identity,
        resolved_backend=resolved_backend,
        solver_threads=solver_threads,
        workers=workers,
        period_indices=selected_indices,
        bus_ids=selected_bus_ids,
        capacity_mw=capacity_mw,
        status=status,
        diagnostics=diagnostics,
        completed_periods=completed_periods,
        requested_backend=solver_backend,
    )
    return HostingCapacityMatrix(
        period_indices=selected_indices,
        bus_ids=selected_bus_ids,
        capacity_mw=capacity_mw,
        status=status,
        objective_value=objective_value,
        solve_time_s=solve_time_s,
        metadata=MappingProxyType(metadata),
    )
