from dataclasses import FrozenInstanceError
import importlib.util
import inspect
import math

import pytest

import tools.activsg2000.dcopf as dcopf
from tools.activsg2000.dcopf import (
    Branch,
    Bus,
    DCOPFInfeasible,
    Generator,
    ReusableDCOPFModel,
)
from tools.hosting_capacity import (
    HOSTING_CAPACITY_STATUSES,
    HostingCapacityPeriod,
    HostingCapacityPoint,
    solve_hosting_capacity_at_bus,
    validate_period,
)


def _tiny_period(**overrides):
    values = {
        "period_index": 0,
        "buses": (
            Bus("b1", 0.0, reference=True),
            Bus("b2", 5.0),
        ),
        "generators": (Generator("g1", "b1", 0.0, 20.0, 10.0),),
        "branches": (Branch("br1", "b1", "b2", 1.0, 100.0),),
        "fixed_injection_mw_by_bus": {},
        "source_metadata": {"source": "test"},
    }
    values.update(overrides)
    return HostingCapacityPeriod(**values)


def test_validate_period_preserves_bus_order_and_normalizes_empty_injections():
    period = validate_period(
        _tiny_period(
            buses=[Bus("b1", 0.0, reference=True), Bus("b2", 5.0)],
            fixed_injection_mw_by_bus={},
            source_metadata={"source": "test"},
        )
    )

    assert tuple(bus.bus_id for bus in period.buses) == ("b1", "b2")
    assert period.fixed_injection_mw_by_bus == {}
    assert isinstance(period.buses, tuple)
    assert isinstance(period.fixed_injection_mw_by_bus, dict) is False
    with pytest.raises(FrozenInstanceError):
        period.period_index = 1


def test_validate_period_defaults_first_bus_as_reference():
    period = validate_period(
        _tiny_period(
            buses=[Bus("b1", 0.0), Bus("b2", 5.0)],
        )
    )

    assert period.buses[0].reference is True
    assert period.buses[1].reference is False


def test_statuses_are_exact_and_point_serializes_without_binding_resources():
    assert HOSTING_CAPACITY_STATUSES == frozenset(
        {
            "optimal",
            "infeasible_base_case",
            "unbounded",
            "numerical_failure",
            "invalid_input",
            "solver_error",
            "interrupted",
        }
    )
    point = HostingCapacityPoint(
        period_index=0,
        bus_id="b2",
        hosting_capacity_mw=12.5,
        status="optimal",
        objective_value=100.0,
        solve_time_s=0.25,
        binding_resources=("g1:pmax",),
    )
    assert point.to_record() == {
        "period_index": 0,
        "bus_id": "b2",
        "hosting_capacity_mw": 12.5,
        "status": "optimal",
        "objective_value": 100.0,
        "solve_time_s": 0.25,
    }


def test_validate_period_rejects_duplicate_bus_ids():
    with pytest.raises(ValueError, match="duplicate bus ID.*b1"):
        validate_period(
            _tiny_period(
                buses=(Bus("b1", 0.0, reference=True), Bus("b1", 5.0)),
            )
        )


def test_validate_period_rejects_unknown_injection_bus():
    with pytest.raises(ValueError, match="fixed injection.*unknown bus.*missing"):
        validate_period(_tiny_period(fixed_injection_mw_by_bus={"missing": 1.0}))


def test_reusable_model_exposes_candidate_demand_probe_contract():
    constructor_parameters = inspect.signature(ReusableDCOPFModel).parameters
    method_parameters = inspect.signature(ReusableDCOPFModel.max_additional_demand).parameters

    assert "candidate_demand_bus_id" in constructor_parameters
    assert "enable_candidate_demand_probes" in constructor_parameters
    assert tuple(method_parameters)[:3] == ("self", "buses", "bus_id")
    assert method_parameters["upper_bound_mw"].default is None
    assert method_parameters["fixed_injection_mw_by_bus"].default is None


@pytest.mark.parametrize(
    ("status", "expected"),
    (("optimal", "optimal"), ("infeasible", "infeasible_base_case"),
     ("unbounded", "unbounded"), ("ambiguous", "solver_error"),
     ("numerical", "numerical_failure"), ("other", "solver_error")),
)
def test_candidate_probe_status_mapping_rejects_ambiguous_unboundedness(status, expected):
    status_mapper = getattr(dcopf, "_candidate_probe_status", None)
    assert status_mapper is not None
    assert status_mapper(status, base_case=True) == expected
    assert status_mapper(status, base_case=False) == (
        "solver_error" if status == "infeasible" else expected
    )


@pytest.mark.skipif(importlib.util.find_spec("highspy") is None, reason="highspy is not installed")
def test_solve_hosting_capacity_at_bus_returns_tiny_period_capacity():
    point = solve_hosting_capacity_at_bus(_tiny_period(), "b2", solver_backend="highs")

    assert point.period_index == 0
    assert point.bus_id == "b2"
    assert point.hosting_capacity_mw == pytest.approx(15.0)
    assert point.status == "optimal"
    assert point.objective_value == pytest.approx(15.0)
    assert point.solve_time_s is not None and point.solve_time_s >= 0.0
    assert point.binding_resources == ()


def test_solve_hosting_capacity_at_bus_unknown_bus_is_invalid_input():
    point = solve_hosting_capacity_at_bus(_tiny_period(), "missing", solver_backend="highs")

    assert point.status == "invalid_input"
    assert point.hosting_capacity_mw is None
    assert point.objective_value is None
    assert point.binding_resources == ()


@pytest.mark.skipif(importlib.util.find_spec("highspy") is None, reason="highspy is not installed")
def test_solve_hosting_capacity_at_bus_maps_base_infeasibility():
    period = _tiny_period(
        generators=(Generator("g1", "b1", 0.0, 4.0, 10.0),),
    )

    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")

    assert point.status == "infeasible_base_case"
    assert point.hosting_capacity_mw is None
    assert point.objective_value is None


@pytest.mark.skipif(importlib.util.find_spec("highspy") is None, reason="highspy is not installed")
def test_solve_hosting_capacity_at_bus_maps_disconnected_candidate():
    period = _tiny_period(branches=())

    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")

    assert point.status == "infeasible_base_case"
    assert point.hosting_capacity_mw is None


def test_solve_hosting_capacity_at_bus_maps_malformed_values_without_zero_capacity():
    point = solve_hosting_capacity_at_bus(
        _tiny_period(), "b2", solver_backend="highs", upper_bound_mw=math.nan
    )

    assert point.status == "invalid_input"
    assert point.hosting_capacity_mw is None
    assert point.objective_value is None


def test_solve_hosting_capacity_at_bus_uses_resolved_auto_backend(monkeypatch):
    class FakeModel:
        def __init__(self, **kwargs):
            assert kwargs["candidate_demand_bus_id"] == "b2"

        def max_additional_demand(self, buses, bus_id, *, upper_bound_mw, fixed_injection_mw_by_bus):
            assert bus_id == "b2"
            return 7.5, "optimal"

    monkeypatch.setattr(dcopf, "resolve_solver_backend", lambda backend: "highs")
    monkeypatch.setattr(dcopf, "ReusableDCOPFModel", FakeModel)

    point = solve_hosting_capacity_at_bus(_tiny_period(), "b2", solver_backend="auto")

    assert point.status == "optimal"
    assert point.hosting_capacity_mw == pytest.approx(7.5)


@pytest.mark.parametrize(
    "failure_status", ("solver_error", "interrupted", "numerical_failure", "unbounded")
)
def test_solve_hosting_capacity_at_bus_maps_backend_statuses(monkeypatch, failure_status):
    class FakeModel:
        def __init__(self, **kwargs):
            pass

        def max_additional_demand(self, *args, **kwargs):
            return math.nan, failure_status

    monkeypatch.setattr(dcopf, "resolve_solver_backend", lambda backend: "highs")
    monkeypatch.setattr(dcopf, "ReusableDCOPFModel", FakeModel)

    point = solve_hosting_capacity_at_bus(_tiny_period(), "b2", solver_backend="auto")

    assert point.status == failure_status
    assert point.hosting_capacity_mw is None
    assert point.objective_value is None


@pytest.mark.skipif(importlib.util.find_spec("highspy") is None, reason="highspy is not installed")
def test_hosting_capacity_boundary_uses_public_task2_model_api():
    period = _tiny_period()
    model = ReusableDCOPFModel(
        buses=tuple(period.buses),
        generators=tuple(period.generators),
        branches=tuple(period.branches),
        candidate_demand_bus_id="b2",
    )
    capacity, status = model.max_additional_demand(
        tuple(period.buses),
        "b2",
        fixed_injection_mw_by_bus=period.fixed_injection_mw_by_bus,
    )

    assert status == "optimal"
    assert capacity == pytest.approx(15.0, abs=1e-7)
    at_capacity = _tiny_period(buses=(Bus("b1", 0.0, reference=True), Bus("b2", 20.0)))
    above_capacity = _tiny_period(buses=(Bus("b1", 0.0, reference=True), Bus("b2", 20.0 + 1e-5)))
    assert model.solve(tuple(at_capacity.buses)).status == "optimal"
    with pytest.raises(DCOPFInfeasible):
        model.solve(tuple(above_capacity.buses), return_result=False)
