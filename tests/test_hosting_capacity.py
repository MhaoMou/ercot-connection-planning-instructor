from dataclasses import FrozenInstanceError
import inspect

import pytest

import tools.activsg2000.dcopf as dcopf
from tools.activsg2000.dcopf import (
    Branch,
    Bus,
    Generator,
    ReusableDCOPFModel,
)
from tools.hosting_capacity import (
    HOSTING_CAPACITY_STATUSES,
    HostingCapacityPeriod,
    HostingCapacityPoint,
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
