"""Analytical and backend regression coverage for hosting capacity."""

from __future__ import annotations

import importlib.util
import math

import pytest

import tools.activsg2000.dcopf as dcopf
from tools.activsg2000.dcopf import Branch, Bus, Generator
from tools.hosting_capacity import HostingCapacityPeriod, solve_hosting_capacity_at_bus


HIGHSPY_MISSING = importlib.util.find_spec("highspy") is None
GUROBIPY_MISSING = importlib.util.find_spec("gurobipy") is None


def _period(
    *,
    buses: tuple[Bus, ...],
    generators: tuple[Generator, ...],
    branches: tuple[Branch, ...],
    fixed_injection_mw_by_bus: dict[str, float] | None = None,
) -> HostingCapacityPeriod:
    return HostingCapacityPeriod(
        period_index=0,
        buses=buses,
        generators=generators,
        branches=branches,
        fixed_injection_mw_by_bus=fixed_injection_mw_by_bus or {},
        source_metadata={"source": "task-7-regression"},
    )


def _two_bus_period(
    *,
    candidate_demand_mw: float,
    generator_p_min_mw: float = 0.0,
    generator_p_max_mw: float = 100.0,
    branch_limits_mw: tuple[float, ...] = (100.0,),
    branch_susceptances: tuple[float, ...] | None = None,
    branches: tuple[Branch, ...] | None = None,
) -> HostingCapacityPeriod:
    if branches is None:
        susceptances = branch_susceptances or (1.0,) * len(branch_limits_mw)
        branches = tuple(
            Branch(f"br{index}", "b1", "b2", susceptance, limit)
            for index, (susceptance, limit) in enumerate(
                zip(susceptances, branch_limits_mw, strict=True), start=1
            )
        )
    return _period(
        buses=(Bus("b1", 0.0, reference=True), Bus("b2", candidate_demand_mw)),
        generators=(Generator("g1", "b1", generator_p_min_mw, generator_p_max_mw, 10.0),),
        branches=branches,
    )


@pytest.mark.skipif(HIGHSPY_MISSING, reason="highspy is not installed")
def test_uncongested_two_bus_capacity_equals_generator_headroom():
    period = _two_bus_period(candidate_demand_mw=5.0, generator_p_max_mw=20.0)

    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")

    assert point.status == "optimal"
    assert point.hosting_capacity_mw == pytest.approx(15.0, abs=1e-7)


@pytest.mark.skipif(HIGHSPY_MISSING, reason="highspy is not installed")
def test_single_bottleneck_capacity_equals_branch_headroom():
    period = _two_bus_period(
        candidate_demand_mw=2.0,
        generator_p_max_mw=100.0,
        branch_limits_mw=(7.0,),
    )

    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")

    assert point.status == "optimal"
    assert point.hosting_capacity_mw == pytest.approx(5.0, abs=1e-7)


@pytest.mark.skipif(HIGHSPY_MISSING, reason="highspy is not installed")
def test_generator_minimum_output_is_feasible_and_limits_upper_capacity():
    period = _two_bus_period(
        candidate_demand_mw=5.0,
        generator_p_min_mw=5.0,
        generator_p_max_mw=10.0,
    )

    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")

    assert point.status == "optimal"
    # The base demand absorbs the 5 MW minimum; only p_max - p_min is available.
    assert point.hosting_capacity_mw == pytest.approx(5.0, abs=1e-7)


@pytest.mark.skipif(HIGHSPY_MISSING, reason="highspy is not installed")
def test_parallel_paths_respect_each_branch_limit():
    period = _two_bus_period(
        candidate_demand_mw=2.0,
        generator_p_max_mw=100.0,
        branch_limits_mw=(3.0, 3.0),
    )

    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")

    assert point.status == "optimal"
    # Equal parallel susceptances split the 6 MW aggregate transfer limit evenly.
    assert point.hosting_capacity_mw == pytest.approx(4.0, abs=1e-7)


@pytest.mark.skipif(HIGHSPY_MISSING, reason="highspy is not installed")
def test_disconnected_candidate_never_becomes_a_fabricated_finite_capacity():
    period = _two_bus_period(candidate_demand_mw=5.0, branches=())

    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")

    assert point.status == "infeasible_base_case"
    assert point.hosting_capacity_mw is None
    assert point.objective_value is None


@pytest.mark.skipif(HIGHSPY_MISSING, reason="highspy is not installed")
def test_infeasible_base_case_is_reported_explicitly():
    period = _two_bus_period(candidate_demand_mw=5.0, generator_p_max_mw=4.0)

    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")

    assert point.status == "infeasible_base_case"
    assert point.hosting_capacity_mw is None


def test_unbounded_backend_status_is_preserved_without_fabricated_capacity(monkeypatch):
    class UnboundedModel:
        def __init__(self, **kwargs):
            assert kwargs["candidate_demand_bus_id"] == "b2"

        def max_additional_demand(self, *args, **kwargs):
            return math.nan, "unbounded"

    monkeypatch.setattr(dcopf, "resolve_solver_backend", lambda requested: "highs")
    monkeypatch.setattr(dcopf, "ReusableDCOPFModel", UnboundedModel)

    point = solve_hosting_capacity_at_bus(
        _two_bus_period(candidate_demand_mw=0.0), "b2", solver_backend="auto"
    )

    assert point.status == "unbounded"
    assert point.hosting_capacity_mw is None
    assert point.objective_value is None


def test_nonfinite_input_returns_invalid_input_before_a_solve():
    period = _two_bus_period(candidate_demand_mw=math.nan)

    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")

    assert point.status == "invalid_input"
    assert point.hosting_capacity_mw is None
    assert point.objective_value is None


def test_auto_backend_uses_monkeypatched_resolver_result(monkeypatch):
    resolved_requests = []
    constructed_backends = []

    class FakeModel:
        def __init__(self, **kwargs):
            constructed_backends.append(kwargs["candidate_demand_bus_id"])

        def max_additional_demand(self, *args, **kwargs):
            return 7.5, "optimal"

    def fake_resolver(requested):
        resolved_requests.append(requested)
        return "highs"

    monkeypatch.setattr(dcopf, "resolve_solver_backend", fake_resolver)
    monkeypatch.setattr(dcopf, "ReusableDCOPFModel", FakeModel)

    point = solve_hosting_capacity_at_bus(
        _two_bus_period(candidate_demand_mw=0.0), "b2", solver_backend="auto"
    )

    assert resolved_requests == ["auto"]
    assert constructed_backends == ["b2"]
    assert point.status == "optimal"
    assert point.hosting_capacity_mw == pytest.approx(7.5)


@pytest.mark.skipif(
    GUROBIPY_MISSING or HIGHSPY_MISSING,
    reason="gurobipy and highspy are required for backend parity",
)
def test_hosting_capacity_gurobi_matches_highs_on_small_case():
    period = _two_bus_period(candidate_demand_mw=5.0, generator_p_max_mw=20.0)

    highs = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")
    gurobi = solve_hosting_capacity_at_bus(period, "b2", solver_backend="gurobi")

    assert highs.status == gurobi.status == "optimal"
    assert highs.hosting_capacity_mw == pytest.approx(gurobi.hosting_capacity_mw, abs=1e-7)
