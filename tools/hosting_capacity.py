"""Data contracts and input validation for one-bus hosting-capacity solves."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from collections.abc import Mapping, Sequence
from numbers import Real
from types import MappingProxyType
from typing import Literal

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

    def __post_init__(self) -> None:
        if self.status not in HOSTING_CAPACITY_STATUSES:
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
