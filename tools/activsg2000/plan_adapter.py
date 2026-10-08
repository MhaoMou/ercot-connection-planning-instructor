"""Bridge canonical student plans into the prepared Texas DCOPF."""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from pathlib import Path
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

from ercot_game.models import ConnectionPlan, GeneratorPlan, StoragePlan, TeamPlan
from ercot_game.csv_submission import TEAM_GENERATOR_COST_CURVE

from .dcopf import (
    DCOPFInfeasible,
    DCOPFResult,
    Generator,
    StorageDispatchAsset,
    solve_prepared_horizon,
    solve_prepared_period,
)


@dataclass(frozen=True)
class PlanPeriodResult:
    period_index: int
    dcopf: DCOPFResult
    submitted_load_mw: float


@dataclass(frozen=True)
class PlanDispatchResult:
    team_id: str
    periods: tuple[PlanPeriodResult, ...]
    time_step_hours: float = 1.0
    final_storage_soc_mwh_by_storage: dict[str, float] | None = None

    @property
    def total_submitted_load_mwh(self) -> float:
        return sum(item.submitted_load_mw for item in self.periods) * self.time_step_hours


@dataclass(frozen=True)
class BatchPeriodResult:
    period_index: int
    dcopf: DCOPFResult
    submitted_load_mw_by_team: dict[str, float]
    generator_dispatch_mw_by_team: dict[str, dict[str, float]]
    grid_purchase_mw_by_team: dict[str, float] | None = None
    grid_purchase_mw_by_team_by_bus: dict[str, dict[str, float]] | None = None
    storage_charge_mw_by_team: dict[str, dict[str, float]] | None = None
    storage_discharge_mw_by_team: dict[str, dict[str, float]] | None = None
    storage_soc_mwh_by_team: dict[str, dict[str, float]] | None = None


@dataclass(frozen=True)
class BatchDispatchResult:
    team_ids: tuple[str, ...]
    periods: tuple[BatchPeriodResult, ...]
    service_fraction_by_team: dict[str, float] | None = None
    final_storage_soc_mwh_by_team: dict[str, dict[str, float]] | None = None

    @property
    def final_soc_mwh_by_team(self) -> dict[str, dict[str, float]]:
        """Compatibility alias for persisted end-of-round storage state."""
        return self.final_storage_soc_mwh_by_team or {}


def _is_finite_number(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _validate_period_indices(period_indices: tuple[int, ...]) -> None:
    if not period_indices:
        raise ValueError("period_indices must not be empty")
    if any(not isinstance(index, int) or isinstance(index, bool) for index in period_indices):
        raise ValueError("period_indices must contain integers")
    if len(set(period_indices)) != len(period_indices):
        raise ValueError("period_indices must not contain duplicates")


def _student_generator_id(*parts: str) -> str:
    """Encode student generator identity without delimiter collisions."""
    return "student:" + "".join(f"{len(part)}:{part}" for part in parts)


def _student_generator_segment_id(*parts: str, segment: int) -> str:
    return _student_generator_id(*parts, f"segment-{segment}")


def _normalize_plan_for_adapter(
    plan: TeamPlan,
    *,
    horizon: int | None = None,
) -> TeamPlan:
    """Expand site-only plans before any adapter validation or evaluation.

    The public adapter accepts both the frozen site schema and the legacy
    expanded connection schema. Keeping this normalization here makes the
    adapter boundary safe for direct callers as well as the round coordinator.
    """
    if not isinstance(plan, TeamPlan):
        raise ValueError("plan must be a TeamPlan")
    if horizon is not None and (not isinstance(horizon, int) or horizon <= 0):
        raise ValueError("horizon must be a positive integer")
    if not plan.sites and not any(
        connection.profile == () for connection in plan.connections
    ):
        return plan

    connections = list(plan.connections)
    connection_by_id: dict[str, ConnectionPlan] = {}
    for connection in connections:
        if connection.connection_id in connection_by_id:
            raise ValueError(f"duplicate connection ID in team {plan.team_id}")
        connection_by_id[connection.connection_id] = connection

    site_ids: set[str] = set()
    for site in plan.sites:
        if site.site_id in site_ids:
            raise ValueError(f"duplicate site_id: {site.site_id}")
        site_ids.add(site.site_id)
        site_connection = ConnectionPlan(
            connection_id=site.site_id,
            bus_id=site.bus_id,
            capacity_mw=site.connection_capacity_mw,
            profile=() if horizon is None else (1.0,) * horizon,
        )
        previous = connection_by_id.get(site.site_id)
        if previous is not None:
            if (
                previous.bus_id != site_connection.bus_id
                or previous.capacity_mw != site_connection.capacity_mw
            ):
                raise ValueError(
                    f"site {site.site_id} conflicts with its connection specification"
                )
        else:
            connections.append(site_connection)
            connection_by_id[site.site_id] = site_connection

    if horizon is not None:
        connections = [
            ConnectionPlan(
                connection_id=connection.connection_id,
                bus_id=connection.bus_id,
                capacity_mw=connection.capacity_mw,
                profile=(1.0,) * horizon
                if not connection.profile
                else connection.profile,
            )
            for connection in connections
        ]

    generators = list(plan.generators)
    generator_sites = {generator.connection_id for generator in generators}
    storages = list(plan.storages)
    storage_sites = {storage.connection_id for storage in storages}
    for site in plan.sites:
        if site.site_id not in generator_sites and site.generator_capacity_mw != 0:
            generators.append(
                GeneratorPlan(
                    generator_id=f"generator:{plan.team_id}:{site.site_id}",
                    connection_id=site.site_id,
                    capacity_mw=site.generator_capacity_mw,
                    marginal_cost_per_mwh=0.0,
                )
            )
        if site.site_id not in storage_sites and (
            site.storage_power_mw != 0 or site.storage_energy_mwh != 0
        ):
            storages.append(
                StoragePlan(
                    storage_id=f"storage:{plan.team_id}:{site.site_id}",
                    connection_id=site.site_id,
                    power_mw=site.storage_power_mw,
                    energy_mwh=site.storage_energy_mwh,
                )
            )

    return TeamPlan(
        team_id=plan.team_id,
        connections=tuple(connections),
        generators=tuple(generators),
        storages=tuple(storages),
        sites=plan.sites,
    )


def _validate_plan(
    plan: TeamPlan,
    *,
    batch: bool = False,
) -> dict[str, ConnectionPlan]:
    connection_by_id: dict[str, ConnectionPlan] = {
        connection.connection_id: connection for connection in plan.connections
    }
    if len(connection_by_id) != len(plan.connections):
        if batch:
            raise ValueError(f"duplicate connection ID in team {plan.team_id}")
        raise ValueError("connection IDs must be unique")
    for connection in plan.connections:
        if not isinstance(connection.bus_id, str) or not connection.bus_id:
            raise ValueError(f"connection {connection.connection_id} must identify a source bus")
        if not math.isfinite(connection.capacity_mw) or connection.capacity_mw < 0:
            raise ValueError(f"connection {connection.connection_id} capacity must be finite and non-negative")
        if any(not math.isfinite(value) or value < 0 for value in connection.profile):
            raise ValueError(f"connection {connection.connection_id} profile must be finite and non-negative")
    generator_ids = {generator.generator_id for generator in plan.generators}
    if len(generator_ids) != len(plan.generators):
        raise ValueError("generator IDs must be unique")
    for generator in plan.generators:
        if generator.connection_id not in connection_by_id:
            raise ValueError(f"generator {generator.generator_id} references unknown connection")
        if not math.isfinite(generator.capacity_mw) or generator.capacity_mw < 0:
            raise ValueError(f"generator {generator.generator_id} capacity must be finite and non-negative")
        if not math.isfinite(generator.marginal_cost_per_mwh) or generator.marginal_cost_per_mwh < 0:
            raise ValueError(f"generator {generator.generator_id} cost must be finite and non-negative")
    storage_ids = {storage.storage_id for storage in plan.storages}
    if len(storage_ids) != len(plan.storages):
        raise ValueError("storage IDs must be unique")
    for storage in plan.storages:
        if storage.connection_id not in connection_by_id:
            raise ValueError(f"storage {storage.storage_id} references unknown connection")
        if not _is_finite_number(storage.power_mw) or storage.power_mw <= 0:
            raise ValueError(f"storage {storage.storage_id} power must be finite and positive")
        if not _is_finite_number(storage.energy_mwh) or storage.energy_mwh <= 0:
            raise ValueError(f"storage {storage.storage_id} energy must be finite and positive")
        if storage.initial_state_of_charge_mwh is not None:
            if (
                not _is_finite_number(storage.initial_state_of_charge_mwh)
                or storage.initial_state_of_charge_mwh < storage.energy_mwh * 0.5
                or storage.initial_state_of_charge_mwh > storage.energy_mwh
            ):
                raise ValueError(
                    f"storage {storage.storage_id} initial state of charge must be finite "
                    "and at least half of, and within, its energy capacity"
                )
    return connection_by_id


def evaluate_plan_with_prepared_dcopf(
    artifact_dir: str | Path,
    plan: TeamPlan,
    *,
    period_indices: tuple[int, ...],
    time_step_hours: float = 1.0,
) -> PlanDispatchResult:
    """Evaluate a canonical plan against the 2,000-bus dispatch artifact.

    This adapter models fixed load: no curtailment, no load shedding, and no
    integer recourse. With a prepared artifact, storage dispatch is continuous
    and team-owned inside one horizon DCOPF solve using ideal efficiency.
    The deterministic schedule below remains only as a compatibility path for
    lightweight callers that do not provide a prepared artifact.

    Connection ``bus_id`` values must be source-artifact bus IDs. A connection
    contributes ``capacity_mw * profile[t]`` to its bus demand, or constant
    ``capacity_mw`` when its legacy-compatible profile is empty. Student
    generators are local candidate generators at the corresponding connection
    bus. Their aggregate capacity is capped by the team's local net load at
    that bus in each requested period. This is an adapter-level contract; it
    does not change the existing browser evaluator's abstract planning-model
    semantics.
    """
    _validate_period_indices(period_indices)
    plan = _normalize_plan_for_adapter(plan, horizon=max(period_indices) + 1)
    if not math.isfinite(time_step_hours) or time_step_hours <= 0:
        raise ValueError("time_step_hours must be finite and positive")
    connection_by_id = _validate_plan(plan)
    ordered_connections = tuple(sorted(plan.connections, key=lambda item: item.connection_id))
    ordered_generators = tuple(sorted(plan.generators, key=lambda item: item.generator_id))

    if plan.storages and Path(artifact_dir).is_dir():
        base_demand_by_period = tuple(
            _connection_load_by_bus(ordered_connections, period_index)
            for period_index in period_indices
        )
        candidates_by_period = tuple(
            _student_generators(
                ordered_generators,
                connection_by_id,
                base_demand_by_period[offset],
            )
            for offset in range(len(period_indices))
        )
        storage_assets = tuple(
            StorageDispatchAsset(
                storage_id=storage.storage_id,
                team_id=plan.team_id,
                bus_id=connection_by_id[storage.connection_id].bus_id,
                power_mw=storage.power_mw,
                energy_mwh=storage.energy_mwh,
                initial_soc_mwh=_initial_storage_soc(storage),
            )
            for storage in sorted(plan.storages, key=lambda item: item.storage_id)
        )
        horizon_result = solve_prepared_horizon(
            artifact_dir=artifact_dir,
            period_indices=period_indices,
            additional_demand_mw_by_period=base_demand_by_period,
            additional_generators_by_period=candidates_by_period,
            storage_assets=storage_assets,
            owner_load_mw_by_team={plan.team_id: base_demand_by_period},
            time_step_hours=time_step_hours,
        )
        if len(horizon_result.periods) != len(period_indices):
            raise ValueError("horizon DCOPF returned a different number of periods")
        return PlanDispatchResult(
            team_id=plan.team_id,
            periods=tuple(
                PlanPeriodResult(
                    period_index=period_index,
                    dcopf=horizon_result.periods[offset],
                    submitted_load_mw=sum(base_demand_by_period[offset].values()),
                )
                for offset, period_index in enumerate(period_indices)
            ),
            time_step_hours=time_step_hours,
            final_storage_soc_mwh_by_storage=dict(horizon_result.final_soc_mwh_by_storage),
        )

    storage_schedule = _storage_schedule(
        plan,
        ordered_connections,
        period_indices,
        time_step_hours=time_step_hours,
    )

    def solve_with_schedule(schedule: dict[str, dict[str, object]]) -> tuple[PlanPeriodResult, ...]:
        results: list[PlanPeriodResult] = []
        for period_index in period_indices:
            if period_index < 0:
                raise ValueError("period indices must be non-negative")
            base_demand_by_bus = _connection_load_by_bus(ordered_connections, period_index)
            demand_by_bus = dict(base_demand_by_bus)
            for values in schedule.values():
                demand_by_bus[values["bus_id"]] = demand_by_bus.get(values["bus_id"], 0.0) + values["charge"][period_index] - values["discharge"][period_index]
            candidates = _student_generators(
                ordered_generators,
                connection_by_id,
                demand_by_bus,
            )
            dispatch = solve_prepared_period(
                artifact_dir,
                period_index=period_index,
                additional_demand_mw_by_bus=demand_by_bus,
                additional_generators=candidates,
            )
            results.append(
                PlanPeriodResult(
                    period_index=period_index,
                    dcopf=dispatch,
                    submitted_load_mw=sum(base_demand_by_bus.values()),
                )
            )
        return tuple(results)

    try:
        results = solve_with_schedule(storage_schedule)
    except DCOPFInfeasible:
        if not _storage_schedule_has_activity(storage_schedule):
            raise
        results = solve_with_schedule(_idle_storage_schedule(plan, ordered_connections, period_indices))
    return PlanDispatchResult(team_id=plan.team_id, periods=results, time_step_hours=time_step_hours)


def evaluate_plans_with_prepared_dcopf(
    artifact_dir: str | Path,
    plans: Sequence[TeamPlan],
    *,
    period_indices: tuple[int, ...],
    workers: int = 1,
    stop_on_infeasible: bool = False,
) -> BatchDispatchResult:
    """Evaluate all teams jointly on the shared prepared network.

    Base system demand and submitted data-center demand are both fixed. All
    teams are solved jointly on the shared network; if any period is
    infeasible, ``DCOPFInfeasible`` propagates to the batch orchestrator for
    failure isolation. The exception does not imply that every team receives
    an infeasible score.
    """
    if not plans:
        raise ValueError("at least one team plan is required")
    ordered_plans = tuple(
        sorted(
            (
                _normalize_plan_for_adapter(plan, horizon=max(period_indices) + 1)
                for plan in plans
            ),
            key=lambda plan: plan.team_id,
        )
    )
    team_ids = tuple(plan.team_id for plan in ordered_plans)
    if len(set(team_ids)) != len(team_ids):
        raise ValueError("team IDs must be unique")
    _validate_period_indices(period_indices)
    if not isinstance(workers, int) or isinstance(workers, bool) or workers <= 0:
        raise ValueError("workers must be a positive integer")
    connection_by_team: dict[str, dict[str, ConnectionPlan]] = {}
    ordered_connections_by_team: dict[str, tuple[ConnectionPlan, ...]] = {}
    for plan in ordered_plans:
        _validate_plan(plan, batch=True)
        ordered_connections = tuple(sorted(plan.connections, key=lambda item: item.connection_id))
        ordered_connections_by_team[plan.team_id] = ordered_connections
        connection_by_team[plan.team_id] = {
            connection.connection_id: connection for connection in ordered_connections
        }

    if any(plan.storages for plan in ordered_plans):
        return _evaluate_batch_with_central_storage(
            artifact_dir,
            ordered_plans=ordered_plans,
            team_ids=team_ids,
            connection_by_team=connection_by_team,
            ordered_connections_by_team=ordered_connections_by_team,
            period_indices=period_indices,
            time_step_hours=1.0,
        )

    storage_schedules = {
        plan.team_id: _storage_schedule(
            plan,
            ordered_connections_by_team[plan.team_id],
            period_indices,
            time_step_hours=1.0,
        )
        for plan in ordered_plans
    }

    def solve_period(
        period_index: int,
        schedules: dict[str, dict[str, dict[str, object]]],
    ) -> BatchPeriodResult:
        base_demand_by_team: dict[str, dict[str, float]] = {team_id: {} for team_id in team_ids}
        net_demand_by_team: dict[str, dict[str, float]] = {team_id: {} for team_id in team_ids}
        aggregate_demand: dict[str, float] = {}
        for plan in ordered_plans:
            for connection in ordered_connections_by_team[plan.team_id]:
                amount = _connection_load(connection, period_index)
                base_demand_by_team[plan.team_id][connection.bus_id] = (
                    base_demand_by_team[plan.team_id].get(connection.bus_id, 0.0) + amount
                )
            net_demand_by_team[plan.team_id] = dict(base_demand_by_team[plan.team_id])
            for bus_id, amount in base_demand_by_team[plan.team_id].items():
                aggregate_demand[bus_id] = aggregate_demand.get(bus_id, 0.0) + amount
            for values in schedules[plan.team_id].values():
                net_demand_by_team[plan.team_id][values["bus_id"]] = net_demand_by_team[plan.team_id].get(values["bus_id"], 0.0) + values["charge"][period_index] - values["discharge"][period_index]
                aggregate_demand[values["bus_id"]] = aggregate_demand.get(values["bus_id"], 0.0) + values["charge"][period_index] - values["discharge"][period_index]
        candidates = tuple(
            candidate
            for plan in ordered_plans
            for candidate in _student_generators(
                tuple(sorted(plan.generators, key=lambda item: item.generator_id)),
                connection_by_team[plan.team_id],
                net_demand_by_team[plan.team_id],
                id_prefix=(plan.team_id,),
            )
        )
        dispatch = solve_prepared_period(
            artifact_dir,
            period_index=period_index,
            additional_demand_mw_by_bus=aggregate_demand,
            additional_generators=candidates,
        )
        generator_dispatch_by_team = {}
        for plan in ordered_plans:
            team_dispatch = {}
            for generator in sorted(plan.generators, key=lambda item: item.generator_id):
                team_dispatch[generator.generator_id] = sum(
                    dispatch.generation_mw.get(
                        _student_generator_segment_id(
                            plan.team_id, generator.generator_id, segment=segment
                        ),
                        0.0,
                    )
                    for segment in range(1, len(TEAM_GENERATOR_COST_CURVE) + 1)
                )
            generator_dispatch_by_team[plan.team_id] = team_dispatch
        grid_purchase_by_team_by_bus = {}
        for plan in ordered_plans:
            generator_bus_by_id = {
                generator.generator_id: connection_by_team[plan.team_id][generator.connection_id].bus_id
                for generator in plan.generators
            }
            purchases_by_bus = {}
            for bus_id, net_load in net_demand_by_team[plan.team_id].items():
                local_generation = sum(
                    amount
                    for generator_id, amount in generator_dispatch_by_team[plan.team_id].items()
                    if generator_bus_by_id.get(generator_id) == bus_id
                )
                purchases_by_bus[bus_id] = max(net_load - local_generation, 0.0)
            grid_purchase_by_team_by_bus[plan.team_id] = purchases_by_bus
        return BatchPeriodResult(
                period_index=period_index,
                dcopf=dispatch,
                submitted_load_mw_by_team={
                    team_id: sum(base_demand_by_team[team_id].values()) for team_id in team_ids
                },
                generator_dispatch_mw_by_team=generator_dispatch_by_team,
                grid_purchase_mw_by_team={
                    team_id: max(
                        sum(net_demand_by_team[team_id].values())
                        - sum(generator_dispatch_by_team[team_id].values()),
                        0.0,
                    )
                    for team_id in team_ids
                },
                grid_purchase_mw_by_team_by_bus=grid_purchase_by_team_by_bus,
                storage_charge_mw_by_team={
                    team_id: {storage_id: values["charge"][period_index] for storage_id, values in schedules[team_id].items()}
                    for team_id in team_ids
                },
                storage_discharge_mw_by_team={
                    team_id: {storage_id: values["discharge"][period_index] for storage_id, values in schedules[team_id].items()}
                    for team_id in team_ids
                },
                storage_soc_mwh_by_team={
                    team_id: {storage_id: values["soc"][period_index] for storage_id, values in schedules[team_id].items()}
                    for team_id in team_ids
                },
            )

    def solve_all(schedules: dict[str, dict[str, dict[str, object]]]) -> tuple[BatchPeriodResult, ...]:
        if workers == 1:
            return tuple(solve_period(period_index, schedules) for period_index in period_indices)
        if stop_on_infeasible:
            raise ValueError("stop_on_infeasible requires workers=1")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            return tuple(executor.map(lambda index: solve_period(index, schedules), period_indices))

    try:
        periods = solve_all(storage_schedules)
    except DCOPFInfeasible:
        if not any(_storage_schedule_has_activity(schedule) for schedule in storage_schedules.values()):
            raise
        idle_schedules = {
            plan.team_id: _idle_storage_schedule(
                plan,
                ordered_connections_by_team[plan.team_id],
                period_indices,
            )
            for plan in ordered_plans
        }
        periods = solve_all(idle_schedules)
    return BatchDispatchResult(team_ids=team_ids, periods=tuple(periods))


def _evaluate_batch_with_central_storage(
    artifact_dir: str | Path,
    *,
    ordered_plans: tuple[TeamPlan, ...],
    team_ids: tuple[str, ...],
    connection_by_team: dict[str, dict[str, ConnectionPlan]],
    ordered_connections_by_team: dict[str, tuple[ConnectionPlan, ...]],
    period_indices: tuple[int, ...],
    time_step_hours: float,
) -> BatchDispatchResult:
    """Build one joint horizon solve and attribute storage only to its owner."""
    base_demand_by_team: dict[str, tuple[dict[str, float], ...]] = {
        plan.team_id: tuple(
            _connection_load_by_bus(ordered_connections_by_team[plan.team_id], period_index)
            for period_index in period_indices
        )
        for plan in ordered_plans
    }
    aggregate_demand_by_period = tuple(
        {
            bus_id: sum(
                base_demand_by_team[team_id][period_offset].get(bus_id, 0.0)
                for team_id in team_ids
            )
            for bus_id in {
                bus_id
                for team_id in team_ids
                for bus_id in base_demand_by_team[team_id][period_offset]
            }
        }
        for period_offset in range(len(period_indices))
    )
    candidates_by_period = tuple(
        tuple(
            candidate
            for plan in ordered_plans
            for candidate in _student_generators(
                tuple(sorted(plan.generators, key=lambda item: item.generator_id)),
                connection_by_team[plan.team_id],
                base_demand_by_team[plan.team_id][period_offset],
                id_prefix=(plan.team_id,),
            )
        )
        for period_offset in range(len(period_indices))
    )

    storage_records: list[tuple[str, str, str, str, float, float, float]] = []
    used_solver_storage_ids: set[str] = set()
    for plan in ordered_plans:
        for storage in sorted(plan.storages, key=lambda item: item.storage_id):
            solver_storage_id = storage.storage_id
            if solver_storage_id in used_solver_storage_ids:
                solver_storage_id = f"{plan.team_id}:{storage.storage_id}"
            while solver_storage_id in used_solver_storage_ids:
                solver_storage_id = f"{plan.team_id}:{solver_storage_id}"
            used_solver_storage_ids.add(solver_storage_id)
            connection = connection_by_team[plan.team_id][storage.connection_id]
            storage_records.append(
                (
                    plan.team_id,
                    storage.storage_id,
                    solver_storage_id,
                    connection.bus_id,
                    storage.power_mw,
                    storage.energy_mwh,
                    _initial_storage_soc(storage),
                )
            )

    storage_assets = tuple(
        StorageDispatchAsset(
            storage_id=solver_storage_id,
            team_id=team_id,
            bus_id=bus_id,
            power_mw=power_mw,
            energy_mwh=energy_mwh,
            initial_soc_mwh=initial_soc,
        )
        for team_id, _storage_id, solver_storage_id, bus_id, power_mw, energy_mwh, initial_soc in storage_records
    )
    owner_load_mw_by_team = {
        team_id: base_demand_by_team[team_id]
        for team_id in team_ids
    }
    horizon_result = solve_prepared_horizon(
        artifact_dir=artifact_dir,
        period_indices=period_indices,
        additional_demand_mw_by_period=aggregate_demand_by_period,
        additional_generators_by_period=candidates_by_period,
        storage_assets=storage_assets,
        owner_load_mw_by_team=owner_load_mw_by_team,
        time_step_hours=time_step_hours,
    )
    if len(horizon_result.periods) != len(period_indices):
        raise ValueError("horizon DCOPF returned a different number of periods")

    storage_by_solver_id = {
        solver_storage_id: (team_id, storage_id, bus_id)
        for team_id, storage_id, solver_storage_id, bus_id, _power_mw, _energy_mwh, _initial_soc in storage_records
    }
    periods: list[BatchPeriodResult] = []
    for period_offset, (period_index, dcopf) in enumerate(zip(period_indices, horizon_result.periods)):
        generator_dispatch_by_team: dict[str, dict[str, float]] = {}
        grid_purchase_by_team_by_bus: dict[str, dict[str, float]] = {}
        storage_charge_by_team: dict[str, dict[str, float]] = {team_id: {} for team_id in team_ids}
        storage_discharge_by_team: dict[str, dict[str, float]] = {team_id: {} for team_id in team_ids}
        storage_soc_by_team: dict[str, dict[str, float]] = {team_id: {} for team_id in team_ids}
        for solver_storage_id, (team_id, storage_id, _bus_id) in storage_by_solver_id.items():
            storage_charge_by_team[team_id][storage_id] = dcopf.storage_charge_mw.get(solver_storage_id, 0.0)
            storage_discharge_by_team[team_id][storage_id] = dcopf.storage_discharge_mw.get(solver_storage_id, 0.0)
            storage_soc_by_team[team_id][storage_id] = dcopf.storage_soc_mwh.get(solver_storage_id, 0.0)

        for plan in ordered_plans:
            team_dispatch: dict[str, float] = {}
            for generator in sorted(plan.generators, key=lambda item: item.generator_id):
                team_dispatch[generator.generator_id] = sum(
                    dcopf.generation_mw.get(
                        _student_generator_segment_id(
                            plan.team_id,
                            generator.generator_id,
                            segment=segment,
                        ),
                        0.0,
                    )
                    for segment in range(1, len(TEAM_GENERATOR_COST_CURVE) + 1)
                )
            generator_dispatch_by_team[plan.team_id] = team_dispatch

            net_load_by_bus = dict(base_demand_by_team[plan.team_id][period_offset])
            for solver_storage_id, (team_id, _storage_id, bus_id) in storage_by_solver_id.items():
                if team_id != plan.team_id:
                    continue
                net_load_by_bus[bus_id] = net_load_by_bus.get(bus_id, 0.0) + (
                    dcopf.storage_charge_mw.get(solver_storage_id, 0.0)
                    - dcopf.storage_discharge_mw.get(solver_storage_id, 0.0)
                )
            generator_bus_by_id = {
                generator.generator_id: connection_by_team[plan.team_id][generator.connection_id].bus_id
                for generator in plan.generators
            }
            grid_purchase_by_bus = {}
            for bus_id, net_load in net_load_by_bus.items():
                local_generation = sum(
                    amount
                    for generator_id, amount in team_dispatch.items()
                    if generator_bus_by_id.get(generator_id) == bus_id
                )
                grid_purchase_by_bus[bus_id] = max(net_load - local_generation, 0.0)
            grid_purchase_by_team_by_bus[plan.team_id] = grid_purchase_by_bus

        periods.append(
            BatchPeriodResult(
                period_index=period_index,
                dcopf=dcopf,
                submitted_load_mw_by_team={
                    team_id: sum(base_demand_by_team[team_id][period_offset].values())
                    for team_id in team_ids
                },
                generator_dispatch_mw_by_team=generator_dispatch_by_team,
                grid_purchase_mw_by_team={
                    team_id: sum(grid_purchase_by_team_by_bus[team_id].values())
                    for team_id in team_ids
                },
                grid_purchase_mw_by_team_by_bus=grid_purchase_by_team_by_bus,
                storage_charge_mw_by_team=storage_charge_by_team,
                storage_discharge_mw_by_team=storage_discharge_by_team,
                storage_soc_mwh_by_team=storage_soc_by_team,
            )
        )

    final_storage_soc_mwh_by_team: dict[str, dict[str, float]] = {team_id: {} for team_id in team_ids}
    for solver_storage_id, (team_id, storage_id, _bus_id) in storage_by_solver_id.items():
        final_storage_soc_mwh_by_team[team_id][storage_id] = horizon_result.final_soc_mwh_by_storage[solver_storage_id]
    return BatchDispatchResult(
        team_ids=team_ids,
        periods=tuple(periods),
        final_storage_soc_mwh_by_team=final_storage_soc_mwh_by_team,
    )


def _connection_load_by_bus(
    connections: Sequence[ConnectionPlan],
    period_index: int,
) -> dict[str, float]:
    demand_by_bus: dict[str, float] = {}
    for connection in connections:
        amount = _connection_load(connection, period_index)
        demand_by_bus[connection.bus_id] = demand_by_bus.get(connection.bus_id, 0.0) + amount
    return demand_by_bus


def _connection_load(connection: ConnectionPlan, period_index: int) -> float:
    if period_index < 0:
        raise ValueError(f"connection {connection.connection_id} profile is too short for period {period_index}")
    if not connection.profile:
        return connection.capacity_mw
    if period_index >= len(connection.profile):
        raise ValueError(f"connection {connection.connection_id} profile is too short for period {period_index}")
    return connection.capacity_mw * connection.profile[period_index]


def _student_generators(
    generators: Sequence[GeneratorPlan],
    connection_by_id: dict[str, ConnectionPlan],
    local_net_load_by_bus: dict[str, float],
    *,
    id_prefix: tuple[str, ...] = (),
) -> tuple[Generator, ...]:
    remaining_by_bus = {
        bus_id: max(load_mw, 0.0) for bus_id, load_mw in local_net_load_by_bus.items()
    }
    candidates: list[Generator] = []
    for generator in generators:
        connection = connection_by_id[generator.connection_id]
        bus_id = connection.bus_id
        remaining = remaining_by_bus.get(bus_id, 0.0)
        for segment, (fraction, cost) in enumerate(TEAM_GENERATOR_COST_CURVE, start=1):
            capacity_mw = min(generator.capacity_mw * fraction, remaining)
            remaining = max(remaining - capacity_mw, 0.0)
            candidates.append(
                Generator(
                    generator_id=_student_generator_segment_id(*id_prefix, generator.generator_id, segment=segment),
                    bus_id=bus_id,
                    p_min_mw=0.0,
                    p_max_mw=capacity_mw,
                    marginal_cost_per_mwh=cost,
                )
            )
        remaining_by_bus[bus_id] = remaining
    return tuple(candidates)


def _storage_schedule(
    plan: TeamPlan,
    connections: Sequence[ConnectionPlan],
    period_indices: tuple[int, ...],
    *,
    time_step_hours: float = 1.0,
) -> dict[str, dict[str, object]]:
    """Build a deterministic continuous peak-shaving schedule for owned storage.

    Storage is ideal-efficiency. It starts at half energy, never discharges
    more than its prior state, and ends at or above half energy. Charging is
    represented as additional owner load; storage cannot export to another team.
    """
    if not plan.storages:
        return {}
    if not math.isfinite(time_step_hours) or time_step_hours <= 0:
        raise ValueError("time_step_hours must be finite and positive")
    if tuple(period_indices) != tuple(range(min(period_indices), max(period_indices) + 1)):
        raise ValueError("storage-enabled evaluation requires contiguous period_indices")
    connection_by_id = {connection.connection_id: connection for connection in connections}
    load_by_connection = {
        connection.connection_id: {
            period: _connection_load(connection, period)
            for period in period_indices
        }
        for connection in connections
    }
    load_by_period = {
        period: sum(load_by_connection[connection_id][period] for connection_id in load_by_connection)
        for period in period_indices
    }
    inactive_connection_ids = {
        connection_id
        for connection_id, loads in load_by_connection.items()
        if not any(amount > 0.0 for amount in loads.values())
    }
    threshold = sorted(load_by_period.values())[len(load_by_period) // 2]
    storage_by_connection: dict[str, list[str]] = {}
    for storage in sorted(plan.storages, key=lambda item: item.storage_id):
        if storage.connection_id not in connection_by_id:
            raise ValueError(f"storage {storage.storage_id} references unknown connection")
        storage_by_connection.setdefault(storage.connection_id, []).append(storage.storage_id)
    result: dict[str, dict[str, object]] = {}
    for storage in sorted(plan.storages, key=lambda item: item.storage_id):
        connection = next(item for item in connections if item.connection_id == storage.connection_id)
        if storage.connection_id in inactive_connection_ids:
            result[storage.storage_id] = _idle_storage_entry(
                storage,
                connection,
                period_indices,
            )
            continue
        soc = _initial_storage_soc(storage)
        charge: dict[int, float] = {}
        discharge: dict[int, float] = {}
        soc_by_period: dict[int, float] = {}
        for period in period_indices:
            if load_by_period[period] < threshold:
                amount = min(storage.power_mw, max((storage.energy_mwh - soc) / time_step_hours, 0.0))
                charge[period] = max(amount, 0.0)
                discharge[period] = 0.0
            else:
                already_allocated = sum(
                    result[other_storage_id]["discharge"][period]
                    for other_storage_id in storage_by_connection[storage.connection_id]
                    if other_storage_id in result
                )
                residual_load = max(
                    load_by_connection[storage.connection_id][period] - already_allocated,
                    0.0,
                )
                amount = min(
                    storage.power_mw,
                    residual_load,
                    max((soc - storage.energy_mwh * 0.5) / time_step_hours, 0.0),
                )
                discharge[period] = max(amount, 0.0)
                charge[period] = 0.0
            soc += (charge[period] - discharge[period]) * time_step_hours
            soc = min(max(soc, 0.0), storage.energy_mwh)
            soc_by_period[period] = soc
        result[storage.storage_id] = {
            "bus_id": connection.bus_id,
            "charge": charge,
            "discharge": discharge,
            "soc": soc_by_period,
        }
    return result


def _idle_storage_schedule(
    plan: TeamPlan,
    connections: Sequence[ConnectionPlan],
    period_indices: tuple[int, ...],
) -> dict[str, dict[str, object]]:
    connection_by_id = {connection.connection_id: connection for connection in connections}
    return {
        storage.storage_id: _idle_storage_entry(
            storage,
            connection_by_id[storage.connection_id],
            period_indices,
        )
        for storage in sorted(plan.storages, key=lambda item: item.storage_id)
    }


def _idle_storage_entry(
    storage,
    connection: ConnectionPlan,
    period_indices: tuple[int, ...],
) -> dict[str, object]:
    return {
        "bus_id": connection.bus_id,
        "charge": {period: 0.0 for period in period_indices},
        "discharge": {period: 0.0 for period in period_indices},
        "soc": {
            period: _initial_storage_soc(storage)
            for period in period_indices
        },
    }


def _initial_storage_soc(storage) -> float:
    if not _is_finite_number(storage.power_mw) or storage.power_mw <= 0:
        raise ValueError(f"storage {storage.storage_id} power must be finite and positive")
    if not _is_finite_number(storage.energy_mwh) or storage.energy_mwh <= 0:
        raise ValueError(f"storage {storage.storage_id} energy must be finite and positive")
    initial = storage.initial_state_of_charge_mwh
    if initial is None:
        return storage.energy_mwh * 0.5
    if (
        not _is_finite_number(initial)
        or initial < storage.energy_mwh * 0.5
        or initial > storage.energy_mwh
    ):
        raise ValueError(
            f"storage {storage.storage_id} initial state of charge must be finite "
            "and at least half of, and within, its energy capacity"
        )
    return initial


def _storage_schedule_has_activity(schedule: dict[str, dict[str, object]]) -> bool:
    return any(
        charge != 0.0 or discharge != 0.0
        for values in schedule.values()
        for charge, discharge in zip(values["charge"].values(), values["discharge"].values())
    )
