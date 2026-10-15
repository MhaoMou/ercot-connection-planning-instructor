from __future__ import annotations

import importlib.util

import pytest

import tools.activsg2000.dcopf as dcopf
from tools.activsg2000.dcopf import (
    Branch,
    Bus,
    Generator,
    GurobiReusableDCOPFModel,
    StorageDispatchAsset,
    solve_dcopf_horizon_benders,
)


def test_auto_backend_selects_available_solver(monkeypatch):
    monkeypatch.setattr(dcopf, "gurobi_is_available", lambda: True)
    assert dcopf.resolve_solver_backend("auto") == "gurobi"

    monkeypatch.setattr(dcopf, "gurobi_is_available", lambda: False)
    assert dcopf.resolve_solver_backend("auto") == "highs"


def test_explicit_backend_selection_does_not_probe_availability(monkeypatch):
    def fail_probe():
        raise AssertionError("explicit backend must not probe Gurobi")

    monkeypatch.setattr(dcopf, "gurobi_is_available", fail_probe)
    assert dcopf.resolve_solver_backend("highs") == "highs"
    assert dcopf.resolve_solver_backend("gurobi") == "gurobi"


@pytest.mark.skipif(importlib.util.find_spec("gurobipy") is None, reason="gurobipy is not installed")
def test_gurobi_backend_matches_highs_on_small_storage_horizon():
    periods = tuple(
        (
            (
                Bus("b1", 0.0, reference=True),
                Bus("b2", 8.0),
            ),
            (Generator("g1", "b1", 0.0, 20.0, 10.0),),
            (Branch("br1", "b1", "b2", 1.0, 20.0),),
        )
        for _ in range(4)
    )
    storage = StorageDispatchAsset(
        storage_id="storage-1",
        team_id="team-1",
        bus_id="b2",
        power_mw=2.0,
        energy_mwh=8.0,
        initial_soc_mwh=4.0,
    )
    owner_load = {"team-1": ({"b2": 8.0},) * 4}
    common = {
        "periods": periods,
        "storage_assets": (storage,),
        "owner_load_mw_by_team": owner_load,
        "parallel_workers": 1,
        "max_iterations": 20,
    }
    highs = solve_dcopf_horizon_benders(**common, solver_backend="highs")
    gurobi = solve_dcopf_horizon_benders(**common, solver_backend="gurobi")
    assert highs.status == gurobi.status == "optimal"
    assert highs.objective_cost == pytest.approx(gurobi.objective_cost, abs=1e-7)
    assert highs.final_soc_mwh_by_storage == pytest.approx(gurobi.final_soc_mwh_by_storage, abs=1e-7)
    for highs_period, gurobi_period in zip(highs.periods, gurobi.periods):
        assert highs_period.objective_cost == pytest.approx(gurobi_period.objective_cost, abs=1e-7)
        assert highs_period.balance_dual_by_bus == pytest.approx(gurobi_period.balance_dual_by_bus, abs=1e-6)


@pytest.mark.skipif(importlib.util.find_spec("gurobipy") is None, reason="gurobipy is not installed")
def test_gurobi_candidate_probe_restores_ordinary_solve():
    buses = (Bus("b1", 0.0, reference=True), Bus("b2", 5.0))
    generators = (Generator("g1", "b1", 0.0, 20.0, 10.0),)
    branches = (Branch("br1", "b1", "b2", 1.0, 9.0),)
    model = GurobiReusableDCOPFModel(
        buses=buses,
        generators=generators,
        branches=branches,
        candidate_demand_bus_id="b2",
    )
    fresh_model = GurobiReusableDCOPFModel(
        buses=buses,
        generators=generators,
        branches=branches,
    )

    capacity, status = model.max_additional_demand(buses, "b2")
    ordinary = model.solve(buses)
    fresh = fresh_model.solve(buses)

    assert status == "optimal"
    assert capacity == pytest.approx(4.0)
    assert ordinary.status == fresh.status == "optimal"
    assert ordinary.objective_cost == pytest.approx(fresh.objective_cost)
    assert ordinary.generation_mw == pytest.approx(fresh.generation_mw)
    assert ordinary.flow_mw_by_branch == pytest.approx(fresh.flow_mw_by_branch)
