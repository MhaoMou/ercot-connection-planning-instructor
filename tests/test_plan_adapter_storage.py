from __future__ import annotations

import pytest

from ercot_game.asset_state import AssetLedger
from ercot_game.models import ConnectionPlan, GeneratorPlan, SitePlan, StoragePlan, TeamPlan
from tools.activsg2000 import plan_adapter
from tools.activsg2000.dcopf import DCOPFHorizonResult, DCOPFResult
from tools.activsg2000.plan_adapter import (
    _initial_storage_soc,
    _storage_schedule,
    _validate_plan,
    evaluate_plan_with_prepared_dcopf,
    evaluate_plans_with_prepared_dcopf,
)


def test_batch_adapter_passes_time_step_to_central_storage_solver(monkeypatch) -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site", "b0001", 10.0, (1.0, 1.0, 1.0, 1.0)),),
        storages=(StoragePlan("store", "site", 2.0, 8.0),),
    )
    observed = {}

    def fake_central(_artifact_dir, **kwargs):
        observed.update(kwargs)
        return plan_adapter.BatchDispatchResult(team_ids=("team-a",), periods=())

    monkeypatch.setattr(plan_adapter, "_evaluate_batch_with_central_storage", fake_central)
    evaluate_plans_with_prepared_dcopf(
        "unused-artifact",
        (plan,),
        period_indices=(0, 1, 2, 3),
        time_step_hours=6.0,
    )

    assert observed["time_step_hours"] == 6.0


def test_daily_exact_cycle_uses_four_six_hour_periods(monkeypatch) -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site", "b0001", 10.0, (1.0,) * 8),),
        storages=(StoragePlan("store", "site", 2.0, 8.0),),
    )
    observed = {}

    def fake_benders(**kwargs):
        observed.update(kwargs)
        periods = tuple(
            DCOPFResult(
                status="optimal",
                objective_cost=0.0,
                generation_mw={},
                angle_by_bus={},
                flow_mw_by_branch={},
                balance_dual_by_bus={},
                storage_soc_mwh={"store": 4.0},
            )
            for _ in kwargs["period_indices"]
        )
        return DCOPFHorizonResult(
            status="optimal",
            periods=periods,
            final_soc_mwh_by_storage={"store": 4.0},
        )

    monkeypatch.setattr(plan_adapter, "solve_prepared_horizon_benders", fake_benders)
    plan_adapter._evaluate_batch_with_central_storage(
        "unused-artifact",
        ordered_plans=(plan,),
        team_ids=("team-a",),
        connection_by_team={"team-a": {"site": plan.connections[0]}},
        ordered_connections_by_team={"team-a": plan.connections},
        period_indices=tuple(range(8)),
        time_step_hours=6.0,
        storage_boundary_mode="daily_exact_cycle",
    )

    assert observed["storage_cycle_hours"] == 4


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


def test_materialized_duplicate_labels_pass_real_plan_adapter_boundary(monkeypatch) -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(
            ConnectionPlan("site-a", "b0001", 10.0, (1.0,)),
            ConnectionPlan("site-b", "b0002", 10.0, (1.0,)),
        ),
        generators=(
            GeneratorPlan("same-label", "site-a", 2.0, 16.0),
            GeneratorPlan("same-label", "site-b", 2.0, 16.0),
        ),
        storages=(
            StoragePlan("same-label", "site-a", 2.0, 8.0),
            StoragePlan("same-label", "site-b", 2.0, 8.0),
        ),
    )
    materialized = AssetLedger.empty().register_plan(
        plan, round_key="2027_jan_jun"
    ).materialize_plan(plan, horizon=1)
    seen = []

    def solve(*args, **kwargs):
        seen.append(kwargs)
        return DCOPFResult(
            status="optimal",
            objective_cost=0.0,
            generation_mw={},
            angle_by_bus={},
            flow_mw_by_branch={},
            balance_dual_by_bus={},
        )

    monkeypatch.setattr(plan_adapter, "solve_prepared_period", solve)

    result = evaluate_plan_with_prepared_dcopf(
        "unused-artifact",
        materialized,
        period_indices=(0,),
    )

    assert result.periods[0].submitted_load_mw == 20.0
    assert len(seen) == 1
    assert {storage.storage_id for storage in materialized.storages} == {
        "storage:team-a:site-a",
        "storage:team-a:site-b",
    }
    assert {generator.generator_id for generator in materialized.generators} == {
        "generator:team-a:site-a",
        "generator:team-a:site-b",
    }


def test_site_only_plan_evaluates_constant_capacity_through_plan_adapter(monkeypatch) -> None:
    plan = TeamPlan(
        team_id="team-a",
        sites=(SitePlan("site-1", "b0001", 40.0, 2.0, 1.0, 4.0),),
    )
    materialized = AssetLedger.empty().materialize_plan(plan, horizon=2)
    seen = []

    def solve(*args, **kwargs):
        seen.append(kwargs["additional_demand_mw_by_bus"])
        return DCOPFResult(
            status="optimal",
            objective_cost=0.0,
            generation_mw={},
            angle_by_bus={},
            flow_mw_by_branch={},
            balance_dual_by_bus={},
        )

    monkeypatch.setattr(plan_adapter, "solve_prepared_period", solve)

    result = evaluate_plan_with_prepared_dcopf(
        "unused-artifact",
        materialized,
        period_indices=(0, 1),
    )

    assert [period.submitted_load_mw for period in result.periods] == [40.0, 40.0]
    assert seen == [{"b0001": 40.0}, {"b0001": 40.0}]
    assert materialized.generators[0].generator_id == "generator:team-a:site-1"
    assert materialized.storages[0].storage_id == "storage:team-a:site-1"


def test_raw_site_only_plan_evaluates_constant_load_through_public_adapter(monkeypatch) -> None:
    plan = TeamPlan(
        team_id="team-a",
        sites=(SitePlan("site-1", "b0001", 40.0, 2.0, 1.0, 4.0),),
    )
    seen = []

    def solve(*args, **kwargs):
        seen.append(kwargs["additional_demand_mw_by_bus"])
        return DCOPFResult(
            status="optimal",
            objective_cost=0.0,
            generation_mw={},
            angle_by_bus={},
            flow_mw_by_branch={},
            balance_dual_by_bus={},
        )

    monkeypatch.setattr(plan_adapter, "solve_prepared_period", solve)

    result = evaluate_plan_with_prepared_dcopf(
        "unused-artifact",
        plan,
        period_indices=(0, 1, 2),
    )

    assert [period.submitted_load_mw for period in result.periods] == [40.0, 40.0, 40.0]
    assert seen == [{"b0001": 40.0}, {"b0001": 40.0}, {"b0001": 40.0}]


def test_raw_site_only_plan_rejects_duplicate_site_ids_at_adapter_boundary() -> None:
    plan = TeamPlan(
        team_id="team-a",
        sites=(
            SitePlan("site-1", "b0001", 40.0),
            SitePlan("site-1", "b0002", 30.0),
        ),
    )

    with pytest.raises(ValueError, match="duplicate site_id: site-1"):
        evaluate_plan_with_prepared_dcopf(
            "unused-artifact",
            plan,
            period_indices=(0,),
        )


def test_raw_site_only_plan_evaluates_constant_load_through_batch_adapter(monkeypatch) -> None:
    plan = TeamPlan(
        team_id="team-a",
        sites=(SitePlan("site-1", "b0001", 40.0),),
    )
    seen = []

    def solve(*args, **kwargs):
        seen.append(kwargs["additional_demand_mw_by_bus"])
        return DCOPFResult(
            status="optimal",
            objective_cost=0.0,
            generation_mw={},
            angle_by_bus={},
            flow_mw_by_branch={},
            balance_dual_by_bus={},
        )

    monkeypatch.setattr(plan_adapter, "solve_prepared_period", solve)

    result = evaluate_plans_with_prepared_dcopf(
        "unused-artifact",
        (plan,),
        period_indices=(0, 1),
    )

    assert [period.submitted_load_mw_by_team for period in result.periods] == [
        {"team-a": 40.0},
        {"team-a": 40.0},
    ]
    assert seen == [{"b0001": 40.0}, {"b0001": 40.0}]


def test_legacy_generator_and_storage_labels_share_one_connection(monkeypatch) -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("legacy-site", "b0001", 40.0, (1.0,)),),
        generators=(GeneratorPlan("legacy-generator", "legacy-site", 2.0, 16.0),),
        storages=(StoragePlan("legacy-storage", "legacy-site", 1.0, 4.0),),
    )

    monkeypatch.setattr(
        plan_adapter,
        "solve_prepared_period",
        lambda *args, **kwargs: DCOPFResult(
            status="optimal",
            objective_cost=0.0,
            generation_mw={},
            angle_by_bus={},
            flow_mw_by_branch={},
            balance_dual_by_bus={},
        ),
    )

    result = evaluate_plan_with_prepared_dcopf(
        "unused-artifact",
        plan,
        period_indices=(0,),
    )

    assert result.periods[0].submitted_load_mw == 40.0
    assert plan.generators[0].generator_id == "legacy-generator"
    assert plan.storages[0].storage_id == "legacy-storage"


def test_single_plan_adapter_serializes_horizon_schedule_and_final_soc(monkeypatch, tmp_path) -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site-a", "b0001", 4.0, (1.0, 1.0)),),
        storages=(StoragePlan("store-a", "site-a", 2.0, 8.0, 6.0),),
    )

    def solve_horizon(*, period_indices, **kwargs):
        dcopf_periods = tuple(
            DCOPFResult(
                status="optimal",
                objective_cost=0.0,
                generation_mw={},
                angle_by_bus={},
                flow_mw_by_branch={},
                balance_dual_by_bus={},
                storage_charge_mw={"store-a": float(period_index + 1)},
                storage_discharge_mw={"store-a": float(period_index) * 0.25},
                storage_soc_mwh={"store-a": 6.0 + period_index * 0.75},
            )
            for period_index in period_indices
        )
        return DCOPFHorizonResult(
            status="optimal",
            periods=dcopf_periods,
            final_soc_mwh_by_storage={"store-a": 6.75},
        )

    monkeypatch.setattr(plan_adapter, "solve_prepared_horizon", solve_horizon)

    result = evaluate_plan_with_prepared_dcopf(
        tmp_path,
        plan,
        period_indices=(0, 1),
    )

    assert [period.dcopf.storage_charge_mw for period in result.periods] == [
        {"store-a": 1.0},
        {"store-a": 2.0},
    ]
    assert [period.dcopf.storage_discharge_mw for period in result.periods] == [
        {"store-a": 0.0},
        {"store-a": 0.25},
    ]
    assert [period.dcopf.storage_soc_mwh for period in result.periods] == [
        {"store-a": 6.0},
        {"store-a": 6.75},
    ]
    assert result.final_storage_soc_mwh_by_storage == {"store-a": 6.75}


def test_batch_adapter_dispatches_storage_centrally_and_exposes_final_soc(monkeypatch) -> None:
    plans = (
        TeamPlan(
            team_id="team-a",
            connections=(ConnectionPlan("site-a", "b0001", 4.0, (1.0, 1.0)),),
            storages=(StoragePlan("store-a", "site-a", 2.0, 8.0, 6.0),),
        ),
        TeamPlan(
            team_id="team-b",
            connections=(ConnectionPlan("site-b", "b0001", 4.0, (1.0, 1.0)),),
            storages=(StoragePlan("store-b", "site-b", 2.0, 8.0, 6.0),),
        ),
    )
    seen = {}

    def solve_horizon(*, period_indices, storage_assets, owner_load_mw_by_team, **kwargs):
        seen["periods"] = period_indices
        seen["storage_assets"] = storage_assets
        seen["owner_load_mw_by_team"] = owner_load_mw_by_team
        dcopf_periods = tuple(
            DCOPFResult(
                status="optimal",
                objective_cost=0.0,
                generation_mw={},
                angle_by_bus={},
                flow_mw_by_branch={},
                balance_dual_by_bus={},
                storage_charge_mw={
                    "store-a": float(period_index + 1),
                    "store-b": float(period_index) + 0.5,
                },
                storage_discharge_mw={
                    "store-a": float(period_index) * 0.25,
                    "store-b": float(period_index) * 0.5,
                },
                storage_soc_mwh={
                    "store-a": 7.0 + period_index,
                    "store-b": 6.5 + period_index,
                },
            )
            for period_index in period_indices
        )
        return DCOPFHorizonResult(
            status="optimal",
            periods=dcopf_periods,
            final_soc_mwh_by_storage={"store-a": 8.0, "store-b": 7.5},
        )

    monkeypatch.setattr(plan_adapter, "solve_prepared_horizon_benders", solve_horizon)

    result = evaluate_plans_with_prepared_dcopf(
        "unused-artifact",
        plans,
        period_indices=(0, 1),
    )

    assert {(asset.team_id, asset.storage_id, asset.bus_id) for asset in seen["storage_assets"]} == {
        ("team-a", "store-a", "b0001"),
        ("team-b", "store-b", "b0001"),
    }
    assert {asset.initial_soc_mwh for asset in seen["storage_assets"]} == {6.0}
    assert seen["owner_load_mw_by_team"] == {
        "team-a": ({"b0001": 4.0}, {"b0001": 4.0}),
        "team-b": ({"b0001": 4.0}, {"b0001": 4.0}),
    }
    assert [period.storage_charge_mw_by_team for period in result.periods] == [
        {"team-a": {"store-a": 1.0}, "team-b": {"store-b": 0.5}},
        {"team-a": {"store-a": 2.0}, "team-b": {"store-b": 1.5}},
    ]
    assert [period.storage_discharge_mw_by_team for period in result.periods] == [
        {"team-a": {"store-a": 0.0}, "team-b": {"store-b": 0.0}},
        {"team-a": {"store-a": 0.25}, "team-b": {"store-b": 0.5}},
    ]
    assert [period.storage_soc_mwh_by_team for period in result.periods] == [
        {"team-a": {"store-a": 7.0}, "team-b": {"store-b": 6.5}},
        {"team-a": {"store-a": 8.0}, "team-b": {"store-b": 7.5}},
    ]
    assert result.final_storage_soc_mwh_by_team == {
        "team-a": {"store-a": 8.0},
        "team-b": {"store-b": 7.5},
    }


def test_batch_adapter_uses_daily_exact_cycle_mode(monkeypatch) -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(ConnectionPlan("site-a", "b0001", 4.0, (1.0,) * 48),),
        storages=(StoragePlan("store-a", "site-a", 2.0, 8.0, 6.0),),
    )
    seen = {}

    def solve_benders(*, period_indices, storage_assets, **kwargs):
        seen["period_indices"] = period_indices
        seen["storage_assets"] = storage_assets
        seen.update(kwargs)
        periods = tuple(
            DCOPFResult(
                status="optimal",
                objective_cost=0.0,
                generation_mw={},
                angle_by_bus={},
                flow_mw_by_branch={},
                balance_dual_by_bus={},
                storage_soc_mwh={"store-a": 4.0},
            )
            for _ in period_indices
        )
        return DCOPFHorizonResult(
            status="optimal",
            periods=periods,
            final_soc_mwh_by_storage={"store-a": 4.0},
        )

    monkeypatch.setattr(plan_adapter, "solve_prepared_horizon_benders", solve_benders)

    result = evaluate_plans_with_prepared_dcopf(
        "unused-artifact",
        (plan,),
        period_indices=tuple(range(48)),
        workers=2,
        storage_boundary_mode="daily_exact_cycle",
    )

    assert seen["period_indices"] == tuple(range(48))
    assert seen["storage_cycle_hours"] == 24
    assert seen["terminal_soc_policy"] == "exact_half"
    assert seen["parallel_workers"] == 2
    assert seen["storage_assets"][0].initial_soc_mwh == 6.0
    assert result.final_storage_soc_mwh_by_team == {"team-a": {"store-a": 4.0}}
    assert result.final_soc_mwh_by_team == result.final_storage_soc_mwh_by_team
