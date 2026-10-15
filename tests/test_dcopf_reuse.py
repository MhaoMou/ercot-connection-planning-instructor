from __future__ import annotations

import pytest
import importlib.util
import math

pytest.importorskip("highspy")

from tools.activsg2000.dcopf import Branch, Bus, Generator, DCOPFInfeasible, solve_dcopf


def _network(demand_b2: float):
    return (
        (Bus("B1", 0.0, reference=True), Bus("B2", demand_b2)),
        (Generator("g1", "B1", 0.0, 20.0, 10.0),),
        (Branch("br1", "B1", "B2", 1.0, 20.0),),
    )


def test_reusable_model_matches_reference_solver_for_multiple_loads() -> None:
    from tools.activsg2000.dcopf import ReusableDCOPFModel

    buses, generators, branches = _network(5.0)
    model = ReusableDCOPFModel(
        buses=buses,
        generators=generators,
        branches=branches,
        base_mva=1.0,
    )

    for demand in (5.0, 7.0):
        current_buses = (buses[0], Bus("B2", demand))
        actual = model.solve(current_buses)
        expected = solve_dcopf(
            buses=current_buses,
            generators=generators,
            branches=branches,
            base_mva=1.0,
        )
        assert actual.status == expected.status == "optimal"
        assert actual.objective_cost == pytest.approx(expected.objective_cost)
        assert actual.generation_mw == pytest.approx(expected.generation_mw)
        assert actual.flow_mw_by_branch == pytest.approx(expected.flow_mw_by_branch)


def test_reusable_model_rejects_changed_topology() -> None:
    from tools.activsg2000.dcopf import ReusableDCOPFModel

    buses, generators, branches = _network(5.0)
    model = ReusableDCOPFModel(
        buses=buses,
        generators=generators,
        branches=branches,
        base_mva=1.0,
    )

    with pytest.raises(ValueError, match="topology inputs must match"):
        model.solve(
            (Bus("B1", 0.0, reference=True), Bus("B2", 5.0), Bus("B3", 0.0)),
        )


def test_reusable_model_supports_status_only_solves() -> None:
    from tools.activsg2000.dcopf import ReusableDCOPFModel

    buses, generators, branches = _network(5.0)
    model = ReusableDCOPFModel(
        buses=buses,
        generators=generators,
        branches=branches,
        base_mva=1.0,
    )

    assert model.solve(buses, return_result=False) == "optimal"


def test_candidate_demand_probe_returns_remaining_generation_headroom() -> None:
    from tools.activsg2000.dcopf import ReusableDCOPFModel

    buses, generators, branches = _network(5.0)
    model = ReusableDCOPFModel(
        buses=buses,
        generators=generators,
        branches=branches,
        candidate_demand_bus_id="B2",
    )

    capacity, status = model.max_additional_demand(buses, "B2")

    assert status == "optimal"
    assert capacity == pytest.approx(15.0)


def test_candidate_demand_probe_respects_branch_limit_and_restores_ordinary_solve() -> None:
    from tools.activsg2000.dcopf import ReusableDCOPFModel

    buses, generators, _ = _network(5.0)
    branches = (Branch("br1", "B1", "B2", 1.0, 9.0),)
    model = ReusableDCOPFModel(
        buses=buses,
        generators=generators,
        branches=branches,
        candidate_demand_bus_id="B2",
    )

    capacity, status = model.max_additional_demand(buses, "B2")
    ordinary = model.solve(buses)
    expected = solve_dcopf(buses=buses, generators=generators, branches=branches)

    assert status == "optimal"
    assert capacity == pytest.approx(4.0)
    assert ordinary.status == expected.status == "optimal"
    assert ordinary.objective_cost == pytest.approx(expected.objective_cost)
    assert ordinary.generation_mw == pytest.approx(expected.generation_mw)
    assert ordinary.flow_mw_by_branch == pytest.approx(expected.flow_mw_by_branch)


def test_candidate_capacity_boundary_is_feasible_and_excess_load_is_infeasible() -> None:
    from tools.activsg2000.dcopf import ReusableDCOPFModel

    buses, generators, branches = _network(5.0)
    model = ReusableDCOPFModel(
        buses=buses,
        generators=generators,
        branches=branches,
        candidate_demand_bus_id="B2",
    )

    capacity, status = model.max_additional_demand(buses, "B2")
    at_capacity = (buses[0], Bus("B2", buses[1].demand_mw + capacity))
    above_capacity = (buses[0], Bus("B2", buses[1].demand_mw + capacity + 0.1))
    reusable_at_capacity = model.solve(at_capacity)
    fresh_at_capacity = solve_dcopf(
        buses=at_capacity,
        generators=generators,
        branches=branches,
    )

    assert status == "optimal"
    assert reusable_at_capacity.status == fresh_at_capacity.status == "optimal"
    assert reusable_at_capacity.objective_cost == pytest.approx(fresh_at_capacity.objective_cost)
    assert reusable_at_capacity.generation_mw == pytest.approx(fresh_at_capacity.generation_mw)
    assert reusable_at_capacity.flow_mw_by_branch == pytest.approx(fresh_at_capacity.flow_mw_by_branch)
    with pytest.raises(DCOPFInfeasible):
        model.solve(above_capacity, return_result=False)


def test_candidate_demand_probe_reports_infeasible_base_case() -> None:
    from tools.activsg2000.dcopf import ReusableDCOPFModel

    buses = (Bus("B1", 0.0, reference=True), Bus("B2", 5.0))
    generators = (Generator("g1", "B1", 0.0, 4.0, 10.0),)
    branches = (Branch("br1", "B1", "B2", 1.0, 20.0),)
    model = ReusableDCOPFModel(
        buses=buses,
        generators=generators,
        branches=branches,
        candidate_demand_bus_id="B2",
    )

    capacity, status = model.max_additional_demand(buses, "B2")

    assert math.isnan(capacity)
    assert status == "infeasible_base_case"


def test_candidate_demand_probe_can_switch_buses_without_rebuilding() -> None:
    from tools.activsg2000.dcopf import ReusableDCOPFModel

    buses, generators, branches = _network(5.0)
    model = ReusableDCOPFModel(
        buses=buses,
        generators=generators,
        branches=branches,
        enable_candidate_demand_probes=True,
    )

    b2_capacity, b2_status = model.max_additional_demand(buses, "B2")
    b1_capacity, b1_status = model.max_additional_demand(buses, "B1")

    assert (b2_capacity, b2_status) == (pytest.approx(15.0), "optimal")
    assert (b1_capacity, b1_status) == (pytest.approx(15.0), "optimal")


@pytest.mark.skipif(importlib.util.find_spec("gurobipy") is None, reason="gurobipy is not installed")
def test_gurobi_candidate_demand_probe_matches_highs() -> None:
    from tools.activsg2000.dcopf import GurobiReusableDCOPFModel, ReusableDCOPFModel

    buses, generators, branches = _network(5.0)
    highs = ReusableDCOPFModel(
        buses=buses,
        generators=generators,
        branches=branches,
        candidate_demand_bus_id="B2",
    )
    gurobi = GurobiReusableDCOPFModel(
        buses=buses,
        generators=generators,
        branches=branches,
        candidate_demand_bus_id="B2",
    )

    highs_capacity, highs_status = highs.max_additional_demand(buses, "B2")
    gurobi_capacity, gurobi_status = gurobi.max_additional_demand(buses, "B2")

    assert highs_status == gurobi_status == "optimal"
    assert gurobi_capacity == pytest.approx(highs_capacity, abs=1e-7)
