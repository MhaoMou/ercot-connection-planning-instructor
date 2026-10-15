from dataclasses import FrozenInstanceError

import pytest

from tools.activsg2000.dcopf import Branch, Bus, Generator
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
