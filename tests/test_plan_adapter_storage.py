from __future__ import annotations

import pytest

from ercot_game.models import ConnectionPlan, StoragePlan, TeamPlan
from tools.activsg2000.plan_adapter import _initial_storage_soc, _storage_schedule, _validate_plan


def test_zero_load_placeholder_storage_stays_idle() -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(
            ConnectionPlan("active-site", "b0001", 40.0, (0.25, 0.75, 0.25, 0.75)),
            ConnectionPlan("placeholder-site", "b0002", 0.0, (0.0, 0.0, 0.0, 0.0)),
        ),
        storages=(StoragePlan("persisted-store", "placeholder-site", 5.0, 20.0, 16.0),),
    )

    schedule = _storage_schedule(
        plan,
        plan.connections,
        (0, 1, 2, 3),
    )["persisted-store"]

    assert schedule["charge"] == {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
    assert schedule["discharge"] == {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
    assert schedule["soc"] == {0: 16.0, 1: 16.0, 2: 16.0, 3: 16.0}


def test_active_storage_schedule_starts_from_optional_initial_soc() -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("active-site", "b0001", 40.0, (0.75,)),),
        storages=(StoragePlan("active-store", "active-site", 5.0, 20.0, 16.0),),
    )

    schedule = _storage_schedule(plan, plan.connections, (0,))["active-store"]

    assert schedule["soc"][0] == 11.0
    assert schedule["discharge"][0] == 5.0


@pytest.mark.parametrize("initial_soc", (-0.1, 9.0, 20.1, float("nan")))
def test_storage_initial_soc_is_validated(initial_soc) -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site", "b0001", 10.0, (1.0,)),),
        storages=(StoragePlan("store", "site", 2.0, 20.0, initial_soc),),
    )

    with pytest.raises(ValueError, match="initial state of charge"):
        _storage_schedule(plan, plan.connections, (0,))


def test_plan_validation_rejects_boolean_initial_soc() -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site", "b0001", 10.0, (1.0,)),),
        storages=(StoragePlan("store", "site", 2.0, 1.0, True),),
    )

    with pytest.raises(ValueError, match="initial state of charge"):
        _validate_plan(plan)


@pytest.mark.parametrize(
    ("field", "message_field"),
    (("power_mw", "power"), ("energy_mwh", "energy")),
)
def test_plan_validation_rejects_boolean_storage_dimensions(field, message_field) -> None:
    dimensions = {"power_mw": 2.0, "energy_mwh": 1.0}
    dimensions[field] = True
    plan = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site", "b0001", 10.0, (1.0,)),),
        storages=(StoragePlan("store", "site", dimensions["power_mw"], dimensions["energy_mwh"]),),
    )

    with pytest.raises(ValueError, match=f"storage store {message_field} must be finite and positive"):
        _validate_plan(plan)


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("power_mw", "storage store power must be finite and positive"),
        ("energy_mwh", "storage store energy must be finite and positive"),
        ("initial_state_of_charge_mwh", "initial state of charge"),
    ),
)
def test_plan_validation_rejects_nonnumeric_storage_values(field, message) -> None:
    values = {
        "power_mw": 2.0,
        "energy_mwh": 20.0,
        "initial_state_of_charge_mwh": 16.0,
    }
    values[field] = "not-a-number"
    plan = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site", "b0001", 10.0, (1.0,)),),
        storages=(StoragePlan(
            "store",
            "site",
            values["power_mw"],
            values["energy_mwh"],
            values["initial_state_of_charge_mwh"],
        ),),
    )

    with pytest.raises(ValueError, match=message):
        _validate_plan(plan)


def test_initial_soc_helper_rejects_boolean_initial_soc() -> None:
    storage = StoragePlan("store", "site", 2.0, 1.0, True)

    with pytest.raises(ValueError, match="initial state of charge"):
        _initial_storage_soc(storage)


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("power_mw", "storage store power must be finite and positive"),
        ("energy_mwh", "storage store energy must be finite and positive"),
        ("initial_state_of_charge_mwh", "initial state of charge"),
    ),
)
def test_initial_soc_helper_rejects_nonnumeric_storage_values(field, message) -> None:
    values = {
        "power_mw": 2.0,
        "energy_mwh": 20.0,
        "initial_state_of_charge_mwh": 16.0,
    }
    values[field] = "not-a-number"
    storage = StoragePlan(
        "store",
        "site",
        values["power_mw"],
        values["energy_mwh"],
        values["initial_state_of_charge_mwh"],
    )

    with pytest.raises(ValueError, match=message):
        _initial_storage_soc(storage)


def test_active_site_storage_keeps_peak_shaving_schedule() -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(
            ConnectionPlan("active-site", "b0001", 40.0, (0.25, 0.75, 0.25, 0.75)),
        ),
        storages=(StoragePlan("active-store", "active-site", 5.0, 20.0),),
    )

    schedule = _storage_schedule(plan, plan.connections, (0, 1, 2, 3))["active-store"]

    assert schedule["charge"][0] == 5.0
    assert schedule["discharge"][1] == 5.0
    assert schedule["soc"][3] == 10.0
