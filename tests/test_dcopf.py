from __future__ import annotations

import pytest

pytest.importorskip("highspy")

from tools.activsg2000.dcopf import (
    Branch,
    Bus,
    DCOPFHorizonResult,
    Generator,
    StorageDispatchAsset,
    solve_dcopf_horizon,
)


def _single_bus_period(cost: float, demand: float = 5.0):
    return (
        (Bus("B1", demand, reference=True),),
        (Generator("grid", "B1", 0.0, 100.0, cost),),
        (),
    )


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
