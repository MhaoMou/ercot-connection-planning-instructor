"""Data contracts and input validation for one-bus hosting-capacity solves."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
from pathlib import Path
import time
from collections.abc import Mapping, Sequence
from numbers import Real
from types import MappingProxyType
from typing import Callable, Literal

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
