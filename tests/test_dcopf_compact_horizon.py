from __future__ import annotations

import pytest

from tools.activsg2000.dcopf import (
    Branch,
    Bus,
    Generator,
    StorageDispatchAsset,
    solve_dcopf_horizon_benders,
)


def test_compact_demand_horizon_matches_materialized_bus_horizon():
    materialized_periods = tuple(
        (
            (Bus("b1", 0.0, reference=True), Bus("b2", 8.0)),
            (Generator("g1", "b1", 0.0, 20.0, 10.0),),
            (Branch("br1", "b1", "b2", 1.0, 20.0),),
        )
        for _ in range(4)
    )
    compact_periods = tuple(
        (
            (Bus("b1", 0.0, reference=True), Bus("b2", 0.0)),
            materialized_periods[0][1],
            materialized_periods[0][2],
        )
        for _ in range(4)
    )
    storage = StorageDispatchAsset("s1", "team-1", "b2", 2.0, 8.0, 4.0)
    owner_load = {"team-1": ({"b2": 8.0},) * 4}
    materialized = solve_dcopf_horizon_benders(
        periods=materialized_periods,
        storage_assets=(storage,),
        owner_load_mw_by_team=owner_load,
        parallel_workers=1,
    )
    compact = solve_dcopf_horizon_benders(
        periods=compact_periods,
        demand_mw_by_period=({"b1": 0.0, "b2": 8.0},) * 4,
        storage_assets=(storage,),
        owner_load_mw_by_team=owner_load,
        parallel_workers=1,
    )
    assert materialized.status == compact.status == "optimal"
    assert compact.objective_cost == pytest.approx(materialized.objective_cost, abs=1e-7)
    assert compact.final_soc_mwh_by_storage == pytest.approx(materialized.final_soc_mwh_by_storage, abs=1e-7)
    for left, right in zip(materialized.periods, compact.periods):
        assert right.objective_cost == pytest.approx(left.objective_cost, abs=1e-7)
        assert right.balance_dual_by_bus == pytest.approx(left.balance_dual_by_bus, abs=1e-6)
