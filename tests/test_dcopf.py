from __future__ import annotations

import pytest

pytest.importorskip("highspy")

from tools.activsg2000.dcopf import (
    Branch,
    Bus,
    DCOPFRecourseSummary,
    DCOPFHorizonResult,
    DCOPFUnresolved,
    Generator,
    ReusableDCOPFModel,
    ReusableDCOPFHorizonModel,
    StorageDispatchAsset,
    solve_dcopf,
    solve_dcopf_horizon,
    solve_dcopf_horizon_benders,
)
import tools.activsg2000.dcopf as dcopf_module


def _single_bus_period(cost: float, demand: float = 5.0):
    return (
        (Bus("B1", demand, reference=True),),
        (Generator("grid", "B1", 0.0, 100.0, cost),),
        (),
    )


def _two_storage_periods():
    return tuple(_single_bus_period(cost=10.0, demand=demand) for demand in (3.0, 4.0, 5.0, 6.0))


def _two_storage_assets():
    return (
        StorageDispatchAsset("storage-a", "team-a", "B1", 1.0, 2.0, 2.0),
        StorageDispatchAsset("storage-b", "team-b", "B1", 1.0, 2.0, 2.0),
    )


def _two_storage_owner_loads():
    return {
        "team-a": tuple({"B1": 3.0} for _ in range(4)),
        "team-b": tuple({"B1": 3.0} for _ in range(4)),
    }


def test_multi_storage_benders_matches_coupled_horizon_lp() -> None:
    periods = _two_storage_periods()
    assets = _two_storage_assets()
    owners = _two_storage_owner_loads()

    reference = solve_dcopf_horizon(
        periods=periods,
        storage_assets=assets,
        owner_load_mw_by_team=owners,
    )
    result = solve_dcopf_horizon_benders(
        periods=periods,
        storage_assets=assets,
        owner_load_mw_by_team=owners,
    )

    assert result.status == reference.status == "optimal"
    assert result.objective_cost == pytest.approx(reference.objective_cost)
    assert result.final_soc_mwh_by_storage == pytest.approx(reference.final_soc_mwh_by_storage)
    assert all(
        period.storage_soc_mwh[storage.storage_id] >= storage.energy_mwh * 0.5 - 1e-7
        for period in result.periods
        for storage in assets
        if period is result.periods[-1]
    )


def test_multi_storage_benders_preserves_owner_load_limits() -> None:
    periods = _two_storage_periods()
    assets = _two_storage_assets()
    owners = _two_storage_owner_loads()
    owners["team-a"] = tuple({"B1": 0.25} for _ in range(4))

    result = solve_dcopf_horizon_benders(
        periods=periods,
        storage_assets=assets,
        owner_load_mw_by_team=owners,
    )

    assert result.status == "optimal"
    assert all(period.storage_discharge_mw["storage-a"] <= 0.25 + 1e-7 for period in result.periods)


def test_multi_storage_benders_returns_unresolved_on_iteration_limit() -> None:
    with pytest.raises(DCOPFUnresolved, match="did not converge|unresolved|wall-time"):
        solve_dcopf_horizon_benders(
            periods=_two_storage_periods(),
            storage_assets=_two_storage_assets(),
            owner_load_mw_by_team=_two_storage_owner_loads(),
            wall_time_limit_seconds=0.0,
        )


def test_storage_free_horizon_matches_single_period_dcopf() -> None:
    periods = (
        (
            (Bus("B1", 0.0, reference=True), Bus("B2", 4.0)),
            (Generator("grid-b1", "B1", 0.0, 10.0, 2.0),),
            (Branch("line", "B1", "B2", 1.0, 10.0),),
        ),
        (
            (Bus("B1", 0.0, reference=True), Bus("B2", 6.0)),
            (Generator("grid-b1", "B1", 0.0, 10.0, 3.0),),
            (Branch("line", "B1", "B2", 1.0, 10.0),),
        ),
    )
    fixed_injections = ({"B2": 1.0}, {"B1": 0.5})

    horizon = solve_dcopf_horizon(
        periods=periods,
        fixed_injection_mw_by_period=fixed_injections,
    )
    individual = tuple(
        solve_dcopf(
            buses=buses,
            generators=generators,
            branches=branches,
            fixed_injection_mw_by_bus=injections,
        )
        for (buses, generators, branches), injections in zip(periods, fixed_injections)
    )

    assert horizon.status == "optimal"
    assert horizon.final_soc_mwh_by_storage == {}
    assert len(horizon.periods) == len(individual)
    for actual, expected in zip(horizon.periods, individual):
        assert actual.status == expected.status
        assert actual.objective_cost == pytest.approx(expected.objective_cost)
        assert actual.generation_mw == pytest.approx(expected.generation_mw)
        assert actual.angle_by_bus == pytest.approx(expected.angle_by_bus)
        assert actual.flow_mw_by_branch == pytest.approx(expected.flow_mw_by_branch)
        assert actual.balance_dual_by_bus == pytest.approx(expected.balance_dual_by_bus)
        assert actual.storage_charge_mw == {}
        assert actual.storage_discharge_mw == {}
        assert actual.storage_soc_mwh == {}


def test_horizon_solver_validates_repeated_topology_once(monkeypatch: pytest.MonkeyPatch) -> None:
    periods = (
        _single_bus_period(2.0, demand=5.0),
        _single_bus_period(2.0, demand=6.0),
        _single_bus_period(2.0, demand=7.0),
    )
    original_validate = dcopf_module._validate
    calls = 0

    def counted_validate(buses, generators, branches):
        nonlocal calls
        calls += 1
        return original_validate(buses, generators, branches)

    monkeypatch.setattr(dcopf_module, "_validate", counted_validate)
    result = solve_dcopf_horizon(periods=periods)

    assert result.status == "optimal"
    assert calls == 1


def test_reusable_horizon_model_solves_multiple_rhs_updates() -> None:
    periods = (
        _single_bus_period(2.0, demand=5.0),
        _single_bus_period(2.0, demand=6.0),
    )
    model = ReusableDCOPFHorizonModel(
        periods=periods,
        fixed_injection_mw_by_period=({}, {}),
    )

    first = model.solve(fixed_injection_mw_by_period=({}, {}))
    second = model.solve(fixed_injection_mw_by_period=({"B1": 1.0}, {"B1": 2.0}))

    assert first.status == "optimal"
    assert second.status == "optimal"
    assert first.periods[0].generation_mw["grid"] == pytest.approx(5.0)
    assert second.periods[0].generation_mw["grid"] == pytest.approx(4.0)


def test_reusable_horizon_model_supports_status_only_solves() -> None:
    periods = (
        _single_bus_period(2.0, demand=5.0),
        _single_bus_period(2.0, demand=6.0),
    )
    model = ReusableDCOPFHorizonModel(
        periods=periods,
        fixed_injection_mw_by_period=({}, {}),
    )

    assert model.solve(
        fixed_injection_mw_by_period=({}, {}),
        return_result=False,
    ) == "optimal"


def test_reusable_single_period_model_supports_objective_dual_summary() -> None:
    buses = (Bus("B1", 0.0, reference=True), Bus("B2", 5.0))
    generators = (Generator("grid", "B1", 0.0, 20.0, 3.0),)
    branches = (Branch("line", "B1", "B2", 1.0, 20.0),)
    model = ReusableDCOPFModel(
        buses=buses,
        generators=generators,
        branches=branches,
    )

    full = model.solve(buses, fixed_injection_mw_by_bus={"B2": 1.0})
    summary = model.solve(
        buses,
        fixed_injection_mw_by_bus={"B2": 1.0},
        return_summary=True,
    )

    assert isinstance(summary, DCOPFRecourseSummary)
    assert summary.status == full.status == "optimal"
    assert summary.objective_cost == pytest.approx(full.objective_cost)
    assert summary.balance_dual_by_bus == pytest.approx(full.balance_dual_by_bus)


def test_reusable_model_finds_injection_interval_with_two_lp_solves() -> None:
    model = ReusableDCOPFModel(
        buses=(Bus("B1", 0.0, reference=True), Bus("B2", 5.0)),
        generators=(Generator("grid", "B1", 0.0, 10.0, 3.0),),
        branches=(Branch("line", "B1", "B2", 1.0, 4.0),),
        enable_injection_probes=True,
    )

    lower, upper = model.feasible_injection_interval(
        (Bus("B1", 0.0, reference=True), Bus("B2", 5.0)),
        "B2",
        lower_bound=-5.0,
        upper_bound=5.0,
    )

    assert lower == pytest.approx(1.0)
    assert upper == pytest.approx(5.0)
    # The probe is fixed back to zero after the bound calculation, so normal
    # dispatch still uses the caller-provided fixed injection only.
    result = model.solve(
        (Bus("B1", 0.0, reference=True), Bus("B2", 5.0)),
        fixed_injection_mw_by_bus={"B2": 1.0},
    )
    assert result.status == "optimal"
    assert result.objective_cost == pytest.approx(12.0)


def test_reusable_horizon_model_exposes_last_solve_phase_timing() -> None:
    model = ReusableDCOPFHorizonModel(
        periods=(_single_bus_period(2.0), _single_bus_period(1.0)),
        storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 1.0, 2.0, 1.0),),
        owner_load_mw_by_team={"team-a": ({"B1": 5.0}, {"B1": 5.0})},
    )

    assert model.solve(
        owner_load_mw_by_team={"team-a": ({"B1": 5.0}, {"B1": 5.0})},
        return_result=False,
    ) == "optimal"
    timing = model.last_solve_timing
    assert set(timing) == {"input_update_s", "solver_run_s", "result_extraction_s", "total_s"}
    assert all(value >= 0.0 for value in timing.values())
    assert timing["total_s"] >= timing["solver_run_s"]


def test_benders_storage_decomposition_matches_coupled_horizon_lp() -> None:
    from tools.activsg2000.dcopf import (
        StorageDispatchAsset,
        solve_dcopf_horizon,
        solve_dcopf_horizon_benders,
    )

    periods = tuple(
        (
            (Bus("B1", 0.0, reference=True), Bus("B2", 5.0 + period)),
            (Generator("g1", "B1", 0.0, 20.0, 10.0),),
            (Branch("br1", "B1", "B2", 1.0, 20.0),),
        )
        for period in range(4)
    )
    storage = StorageDispatchAsset("store-a", "team-a", "B2", 2.0, 4.0, 2.0)
    owner_loads = {"team-a": tuple({"B2": 20.0} for _ in periods)}

    coupled = solve_dcopf_horizon(
        periods=periods,
        storage_assets=(storage,),
        owner_load_mw_by_team=owner_loads,
        base_mva=1.0,
    )
    decomposed = solve_dcopf_horizon_benders(
        periods=periods,
        storage_assets=(storage,),
        owner_load_mw_by_team=owner_loads,
        base_mva=1.0,
    )

    assert decomposed.status == coupled.status == "optimal"
    assert decomposed.objective_cost == pytest.approx(coupled.objective_cost, abs=1e-6)
    assert decomposed.final_soc_mwh_by_storage == pytest.approx(
        coupled.final_soc_mwh_by_storage, abs=1e-6
    )
    for period in decomposed.periods:
        assert period.status == "optimal"


def test_benders_parallel_workers_matches_serial_solution() -> None:
    from tools.activsg2000.dcopf import solve_dcopf_horizon_benders

    periods = tuple(
        (
            (Bus("B1", 0.0, reference=True), Bus("B2", 5.0 + period)),
            (Generator("g1", "B1", 0.0, 20.0, 10.0),),
            (Branch("br1", "B1", "B2", 1.0, 20.0),),
        )
        for period in range(4)
    )
    storage = StorageDispatchAsset("store-a", "team-a", "B2", 2.0, 4.0, 2.0)
    owner_loads = {"team-a": tuple({"B2": 20.0} for _ in periods)}

    serial = solve_dcopf_horizon_benders(
        periods=periods,
        storage_assets=(storage,),
        owner_load_mw_by_team=owner_loads,
        parallel_workers=1,
    )
    parallel = solve_dcopf_horizon_benders(
        periods=periods,
        storage_assets=(storage,),
        owner_load_mw_by_team=owner_loads,
        parallel_workers=2,
    )

    assert parallel.status == serial.status == "optimal"
    assert [period.objective_cost for period in parallel.periods] == pytest.approx(
        [period.objective_cost for period in serial.periods]
    )
    assert parallel.final_soc_mwh_by_storage == pytest.approx(
        serial.final_soc_mwh_by_storage
    )


def test_benders_parallel_reuses_one_network_model_per_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.activsg2000.dcopf import solve_dcopf_horizon_benders

    periods = tuple(
        (
            (Bus("B1", 0.0, reference=True), Bus("B2", 5.0 + period)),
            (Generator("g1", "B1", 0.0, 20.0, 10.0),),
            (Branch("br1", "B1", "B2", 1.0, 20.0),),
        )
        for period in range(8)
    )
    storage = StorageDispatchAsset("store-a", "team-a", "B2", 2.0, 4.0, 2.0)
    owner_loads = {"team-a": tuple({"B2": 20.0} for _ in periods)}
    original = dcopf_module.ReusableDCOPFModel
    created = []

    class CountingModel(original):
        def __init__(self, *args, **kwargs):
            created.append(threading.current_thread().name)
            super().__init__(*args, **kwargs)

    import threading
    monkeypatch.setattr(dcopf_module, "ReusableDCOPFModel", CountingModel)
    result = solve_dcopf_horizon_benders(
        periods=periods,
        storage_assets=(storage,),
        owner_load_mw_by_team=owner_loads,
        parallel_workers=2,
        return_result=False,
    )

    assert result == "optimal"
    assert 1 <= len(created) <= 2


def test_benders_honors_zero_wall_time_budget() -> None:
    from tools.activsg2000.dcopf import DCOPFUnresolved, solve_dcopf_horizon_benders

    periods = tuple(
        (
            (Bus("B1", 0.0, reference=True), Bus("B2", 5.0)),
            (Generator("g1", "B1", 0.0, 20.0, 10.0),),
            (Branch("br1", "B1", "B2", 1.0, 20.0),),
        )
        for _ in range(2)
    )
    storage = StorageDispatchAsset("store-a", "team-a", "B2", 2.0, 4.0, 2.0)

    with pytest.raises(DCOPFUnresolved, match="wall-time budget"):
        solve_dcopf_horizon_benders(
            periods=periods,
            storage_assets=(storage,),
            owner_load_mw_by_team={"team-a": ({"B2": 20.0}, {"B2": 20.0})},
            wall_time_limit_seconds=0.0,
        )


def test_benders_fast_bounds_preserve_small_exact_solution() -> None:
    from tools.activsg2000.dcopf import solve_dcopf_horizon_benders

    periods = tuple(
        (
            (Bus("B1", 0.0, reference=True), Bus("B2", 5.0 + period)),
            (Generator("g1", "B1", 0.0, 20.0, 10.0),),
            (Branch("br1", "B1", "B2", 1.0, 20.0),),
        )
        for period in range(3)
    )
    storage = StorageDispatchAsset("store-a", "team-a", "B2", 2.0, 4.0, 2.0)
    owner_loads = {"team-a": tuple({"B2": 20.0} for _ in periods)}

    result = solve_dcopf_horizon_benders(
        periods=periods,
        storage_assets=(storage,),
        owner_load_mw_by_team=owner_loads,
        tighten_feasible_bounds=False,
        return_result=False,
    )

    assert result == "optimal"


def test_reusable_horizon_model_updates_storage_owner_load() -> None:
    periods = (_single_bus_period(100.0, demand=4.0), _single_bus_period(1.0, demand=4.0))
    storage = StorageDispatchAsset("store-a", "team-a", "B1", 3.0, 8.0, 4.0)
    model = ReusableDCOPFHorizonModel(
        periods=periods,
        storage_assets=(storage,),
        owner_load_mw_by_team={"team-a": ({"B1": 3.0}, {"B1": 3.0})},
    )

    result = model.solve(
        owner_load_mw_by_team={"team-a": ({"B1": 1.0}, {"B1": 3.0})},
    )

    assert result.status == "optimal"
    assert result.periods[0].storage_discharge_mw["store-a"] <= 1.0 + 1e-9


def test_horizon_dcopf_dispatches_storage_and_returns_final_soc() -> None:
    result = solve_dcopf_horizon(
        periods=(_single_bus_period(100.0), _single_bus_period(1.0)),
        storage_assets=(
            StorageDispatchAsset("store-a", "team-a", "B1", 3.0, 8.0, 4.0),
        ),
        owner_load_mw_by_team={
            "team-a": ({"B1": 5.0}, {"B1": 5.0}),
        },
    )

    assert isinstance(result, DCOPFHorizonResult)
    assert result.status == "optimal"
    assert result.periods[0].storage_discharge_mw["store-a"] == pytest.approx(3.0)
    assert result.periods[0].storage_charge_mw["store-a"] == pytest.approx(0.0)
    assert result.periods[0].storage_soc_mwh["store-a"] == pytest.approx(1.0)
    assert result.periods[1].storage_charge_mw["store-a"] == pytest.approx(3.0)
    assert result.periods[1].storage_soc_mwh["store-a"] == pytest.approx(4.0)
    assert result.final_soc_mwh_by_storage == {"store-a": pytest.approx(4.0)}


def test_same_bus_storage_is_limited_to_its_own_team_load() -> None:
    result = solve_dcopf_horizon(
        periods=(_single_bus_period(100.0, demand=4.0), _single_bus_period(1.0, demand=4.0)),
        storage_assets=(
            StorageDispatchAsset("store-a", "team-a", "B1", 5.0, 10.0, 5.0),
            StorageDispatchAsset("store-b", "team-b", "B1", 5.0, 10.0, 5.0),
        ),
        owner_load_mw_by_team={
            "team-a": ({"B1": 2.0}, {"B1": 2.0}),
            "team-b": ({"B1": 2.0}, {"B1": 2.0}),
        },
    )

    first_period = result.periods[0]
    assert first_period.storage_discharge_mw["store-a"] <= 2.0 + 1e-8
    assert first_period.storage_discharge_mw["store-b"] <= 2.0 + 1e-8
    assert first_period.storage_discharge_mw["store-a"] == pytest.approx(2.0)
    assert first_period.storage_discharge_mw["store-b"] == pytest.approx(2.0)

    previous_soc = {"store-a": 5.0, "store-b": 5.0}
    for period in result.periods:
        for storage_id in previous_soc:
            charge = period.storage_charge_mw[storage_id]
            discharge = period.storage_discharge_mw[storage_id]
            soc = period.storage_soc_mwh[storage_id]
            assert 0.0 <= charge <= 5.0
            assert 0.0 <= discharge <= 5.0
            assert discharge <= previous_soc[storage_id] + 1e-8
            assert 0.0 <= soc <= 10.0
            assert soc == pytest.approx(previous_soc[storage_id] + charge - discharge)
            previous_soc[storage_id] = soc
    assert result.final_soc_mwh_by_storage["store-a"] >= 5.0
    assert result.final_soc_mwh_by_storage["store-b"] >= 5.0


def test_terminal_soc_stays_at_least_half_full_under_discharge_pressure() -> None:
    result = solve_dcopf_horizon(
        periods=(
            _single_bus_period(100.0, demand=5.0),
            _single_bus_period(1.0, demand=5.0),
        ),
        storage_assets=(
            StorageDispatchAsset("store-a", "team-a", "B1", 5.0, 10.0, 8.0),
        ),
        owner_load_mw_by_team={"team-a": ({"B1": 5.0}, {"B1": 5.0})},
    )

    assert result.final_soc_mwh_by_storage["store-a"] >= 5.0 - 1e-8


def test_storage_discharge_cannot_cross_team_or_bus_boundary() -> None:
    result = solve_dcopf_horizon(
        periods=(
            (
                (Bus("B1", 5.0, reference=True), Bus("B2", 5.0)),
                (
                    Generator("grid-b1", "B1", 0.0, 100.0, 100.0),
                    Generator("grid-b2", "B2", 0.0, 100.0, 1.0),
                ),
                (),
            ),
            (
                (Bus("B1", 5.0, reference=True), Bus("B2", 5.0)),
                (
                    Generator("grid-b1", "B1", 0.0, 100.0, 1.0),
                    Generator("grid-b2", "B2", 0.0, 100.0, 1.0),
                ),
                (),
            ),
        ),
        storage_assets=(
            StorageDispatchAsset("store-a", "team-a", "B1", 5.0, 10.0, 10.0),
        ),
        owner_load_mw_by_team={
            "team-a": ({"B2": 5.0}, {"B2": 5.0}),
        },
    )

    assert result.periods[0].storage_discharge_mw["store-a"] == pytest.approx(0.0)


def test_storage_never_reports_simultaneous_charge_and_discharge() -> None:
    result = solve_dcopf_horizon(
        periods=(_single_bus_period(100.0), _single_bus_period(1.0)),
        storage_assets=(
            StorageDispatchAsset("store-a", "team-a", "B1", 5.0, 10.0, 8.0),
        ),
        owner_load_mw_by_team={"team-a": ({"B1": 5.0}, {"B1": 5.0})},
    )

    for period in result.periods:
        charge = period.storage_charge_mw["store-a"]
        discharge = period.storage_discharge_mw["store-a"]
        assert not (charge > 1e-8 and discharge > 1e-8)


def test_storage_discharge_availability_uses_mw_to_mwh_time_step_conversion() -> None:
    result = solve_dcopf_horizon(
        periods=(_single_bus_period(100.0), _single_bus_period(1.0)),
        storage_assets=(
            StorageDispatchAsset("store-a", "team-a", "B1", 5.0, 8.0, 4.0),
        ),
        owner_load_mw_by_team={
            "team-a": ({"B1": 5.0}, {"B1": 5.0}),
        },
        time_step_hours=0.5,
    )

    first, second = result.periods
    assert first.storage_discharge_mw["store-a"] == pytest.approx(5.0)
    assert first.storage_discharge_mw["store-a"] * 0.5 <= 4.0 + 1e-8
    assert first.storage_soc_mwh["store-a"] == pytest.approx(1.5)
    assert second.storage_soc_mwh["store-a"] == pytest.approx(
        first.storage_soc_mwh["store-a"]
        + second.storage_charge_mw["store-a"] * 0.5
        - second.storage_discharge_mw["store-a"] * 0.5
    )


def test_same_team_same_bus_storage_uses_one_aggregate_discharge_cap() -> None:
    result = solve_dcopf_horizon(
        periods=(_single_bus_period(100.0), _single_bus_period(1.0)),
        storage_assets=(
            StorageDispatchAsset("store-a", "team-a", "B1", 5.0, 10.0, 10.0),
            StorageDispatchAsset("store-b", "team-a", "B1", 5.0, 10.0, 10.0),
        ),
        owner_load_mw_by_team={
            "team-a": ({"B1": 3.0}, {"B1": 3.0}),
        },
    )

    first = result.periods[0]
    assert sum(first.storage_discharge_mw.values()) == pytest.approx(3.0)


def test_storage_cannot_use_another_teams_load_at_the_same_bus() -> None:
    result = solve_dcopf_horizon(
        periods=(_single_bus_period(100.0), _single_bus_period(1.0)),
        storage_assets=(
            StorageDispatchAsset("store-a", "team-a", "B1", 5.0, 10.0, 10.0),
            StorageDispatchAsset("store-b", "team-b", "B1", 5.0, 10.0, 10.0),
        ),
        owner_load_mw_by_team={
            "team-a": ({"B1": 0.0}, {"B1": 0.0}),
            "team-b": ({"B1": 5.0}, {"B1": 5.0}),
        },
    )

    first = result.periods[0]
    assert first.storage_discharge_mw["store-a"] == pytest.approx(0.0)
    assert first.storage_discharge_mw["store-b"] == pytest.approx(5.0)
