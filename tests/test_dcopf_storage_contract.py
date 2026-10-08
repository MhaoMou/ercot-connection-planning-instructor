from __future__ import annotations

import math

import pytest

from tools.activsg2000.dcopf import (
    Branch,
    Bus,
    Generator,
    StorageDispatchAsset,
    _group_storage_by_owner_bus,
    solve_dcopf_horizon,
)


def _period() -> tuple[tuple[Bus, ...], tuple[Generator, ...], tuple[Branch, ...]]:
    return (
        (Bus("B1", 0.0, reference=True), Bus("B2", 0.0)),
        (),
        (),
    )


@pytest.mark.parametrize("initial_soc", [0.0, 4.999999, 10.000001, math.inf, math.nan])
def test_horizon_solver_rejects_initial_soc_outside_persisted_bounds(initial_soc: float) -> None:
    with pytest.raises(ValueError, match="invalid storage dimensions or initial SOC"):
        solve_dcopf_horizon(
            periods=(_period(),),
            storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, initial_soc),),
            owner_load_mw_by_team={"team-a": ({"B1": 1.0},)},
        )


def test_storage_owner_groups_are_partitioned_by_team_and_bus() -> None:
    assets = (
        StorageDispatchAsset("a-1", "team-a", "B1", 2.0, 10.0, 5.0),
        StorageDispatchAsset("a-2", "team-a", "B1", 3.0, 12.0, 6.0),
        StorageDispatchAsset("a-3", "team-a", "B2", 4.0, 16.0, 8.0),
        StorageDispatchAsset("b-1", "team-b", "B1", 5.0, 20.0, 10.0),
    )

    groups = _group_storage_by_owner_bus(assets)

    assert groups == {
        ("team-a", "B1"): (assets[0], assets[1]),
        ("team-a", "B2"): (assets[2],),
        ("team-b", "B1"): (assets[3],),
    }


def test_owner_load_validation_rejects_unknown_bus_before_solver_import() -> None:
    with pytest.raises(ValueError, match="owner load for team-a references an unknown bus"):
        solve_dcopf_horizon(
            periods=(_period(),),
            storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, 5.0),),
            owner_load_mw_by_team={"team-a": ({"UNKNOWN": 1.0},)},
        )


def test_storage_solver_requires_owner_load_contract() -> None:
    with pytest.raises(ValueError, match="owner load is required"):
        solve_dcopf_horizon(
            periods=(_period(),),
            storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, 5.0),),
        )
