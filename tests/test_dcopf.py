from __future__ import annotations

import pytest

pytest.importorskip("highspy")

from tools.activsg2000.dcopf import (
    Branch,
    Bus,
    DCOPFHorizonResult,
    Generator,
    StorageDispatchAsset,
    solve_dcopf,
    solve_dcopf_horizon,
)


def _single_bus_period(cost: float, demand: float = 5.0):
    return (
        (Bus("B1", demand, reference=True),),
        (Generator("grid", "B1", 0.0, 100.0, cost),),
        (),
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
