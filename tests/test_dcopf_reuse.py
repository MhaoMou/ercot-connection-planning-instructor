from __future__ import annotations

import pytest

pytest.importorskip("highspy")

from tools.activsg2000.dcopf import Branch, Bus, Generator, solve_dcopf


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
