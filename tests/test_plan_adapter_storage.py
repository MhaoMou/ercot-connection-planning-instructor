from __future__ import annotations

from ercot_game.models import ConnectionPlan, StoragePlan, TeamPlan
from tools.activsg2000.plan_adapter import _storage_schedule


def test_zero_load_placeholder_storage_stays_idle() -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(
            ConnectionPlan("active-site", "b0001", 40.0, (0.25, 0.75, 0.25, 0.75)),
            ConnectionPlan("placeholder-site", "b0002", 0.0, (0.0, 0.0, 0.0, 0.0)),
        ),
        storages=(StoragePlan("persisted-store", "placeholder-site", 5.0, 20.0),),
    )

    schedule = _storage_schedule(
        plan,
        plan.connections,
        (0, 1, 2, 3),
    )["persisted-store"]

    assert schedule["charge"] == {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
    assert schedule["discharge"] == {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
    assert schedule["soc"] == {0: 10.0, 1: 10.0, 2: 10.0, 3: 10.0}


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
