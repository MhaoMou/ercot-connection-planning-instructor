from __future__ import annotations

import math

import pytest

from tools.activsg2000 import dcopf
from tools.activsg2000.dcopf import (
    Branch,
    Bus,
    DCOPFResult,
    Generator,
    StorageDispatchAsset,
    _group_storage_by_owner_bus,
    solve_dcopf_daily_blocks,
    solve_dcopf_horizon,
)


def _period() -> tuple[tuple[Bus, ...], tuple[Generator, ...], tuple[Branch, ...]]:
    return (
        (Bus("B1", 0.0, reference=True), Bus("B2", 0.0)),
        (),
        (),
    )


@pytest.mark.parametrize("initial_soc", [0.0, 4.999999, 10.000001, math.inf, math.nan])
def test_horizon_solver_rejects_initial_soc_outside_persisted_bounds(initial_soc: float) -> None:
    with pytest.raises(ValueError, match="invalid storage dimensions or initial SOC"):
        solve_dcopf_horizon(
            periods=(_period(),),
            storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, initial_soc),),
            owner_load_mw_by_team={"team-a": ({"B1": 1.0},)},
        )


def test_storage_owner_groups_are_partitioned_by_team_and_bus() -> None:
    assets = (
        StorageDispatchAsset("a-1", "team-a", "B1", 2.0, 10.0, 5.0),
        StorageDispatchAsset("a-2", "team-a", "B1", 3.0, 12.0, 6.0),
        StorageDispatchAsset("a-3", "team-a", "B2", 4.0, 16.0, 8.0),
        StorageDispatchAsset("b-1", "team-b", "B1", 5.0, 20.0, 10.0),
    )

    groups = _group_storage_by_owner_bus(assets)

    assert groups == {
        ("team-a", "B1"): (assets[0], assets[1]),
        ("team-a", "B2"): (assets[2],),
        ("team-b", "B1"): (assets[3],),
    }


def test_owner_load_validation_rejects_unknown_bus_before_solver_import() -> None:
    with pytest.raises(ValueError, match="owner load for team-a references an unknown bus"):
        solve_dcopf_horizon(
            periods=(_period(),),
            storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, 5.0),),
            owner_load_mw_by_team={"team-a": ({"UNKNOWN": 1.0},)},
        )


def test_storage_solver_requires_owner_load_contract() -> None:
    with pytest.raises(ValueError, match="owner load is required"):
        solve_dcopf_horizon(
            periods=(_period(),),
            storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, 5.0),),
        )


def test_horizon_solver_rejects_fixed_injection_dimension_mismatch() -> None:
    with pytest.raises(ValueError, match="fixed injections must match the number of periods"):
        solve_dcopf_horizon(
            periods=(_period(), _period()),
            fixed_injection_mw_by_period=({},),
        )


def test_horizon_solver_rejects_invalid_period_shape() -> None:
    with pytest.raises(ValueError, match=r"periods must contain \(buses, generators, branches\) triples"):
        solve_dcopf_horizon(periods=((_period()[0], _period()[1]),))


def test_horizon_solver_rejects_owner_load_dimension_mismatch() -> None:
    with pytest.raises(ValueError, match="owner load for team-a must match the number of periods"):
        solve_dcopf_horizon(
            periods=(_period(), _period()),
            storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, 5.0),),
            owner_load_mw_by_team={"team-a": ({"B1": 1.0},)},
        )


def test_horizon_solver_rejects_non_mapping_owner_load_period() -> None:
    with pytest.raises(ValueError, match="owner load for team-a must contain mappings"):
        solve_dcopf_horizon(
            periods=(_period(),),
            storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, 5.0),),
            owner_load_mw_by_team={"team-a": (None,)},
        )


@pytest.mark.parametrize("amount", ["not-a-number", True, float("inf"), float("nan"), -1.0])
def test_horizon_solver_rejects_invalid_owner_load_values(amount: object) -> None:
    with pytest.raises(ValueError, match="owner load for team-a must be finite and non-negative"):
        solve_dcopf_horizon(
            periods=(_period(),),
            storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, 5.0),),
            owner_load_mw_by_team={"team-a": ({"B1": amount},)},
        )


def test_horizon_solver_rejects_non_sequence_owner_load_horizon() -> None:
    with pytest.raises(ValueError, match="owner load for team-a must be a sequence of period mappings"):
        solve_dcopf_horizon(
            periods=(_period(),),
            storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, 5.0),),
            owner_load_mw_by_team={"team-a": None},
        )


def test_horizon_solver_rejects_non_numeric_time_step() -> None:
    with pytest.raises(ValueError, match="time_step_hours must be finite and positive"):
        solve_dcopf_horizon(periods=(_period(),), time_step_hours="one hour")


def test_horizon_solver_supports_exact_half_full_terminal_boundary() -> None:
    period = (
        (Bus("B1", 2.0, reference=True),),
        (Generator("g1", "B1", 0.0, 10.0, 1.0),),
        (),
    )

    result = solve_dcopf_horizon(
        periods=(period, period),
        storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, 5.0),),
        owner_load_mw_by_team={"team-a": ({"B1": 2.0}, {"B1": 2.0})},
        terminal_soc_policy="exact_half",
    )

    assert result.final_soc_mwh_by_storage["store-a"] == pytest.approx(5.0)


def test_benders_daily_cycle_resets_storage_at_every_day_boundary() -> None:
    period = (
        (Bus("B1", 2.0, reference=True),),
        (Generator("g1", "B1", 0.0, 10.0, 1.0),),
        (),
    )
    periods = (period,) * 48

    result = dcopf.solve_dcopf_horizon_benders(
        periods=periods,
        storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, 5.0),),
        owner_load_mw_by_team={"team-a": ({"B1": 2.0},) * 48},
        storage_cycle_hours=24,
        terminal_soc_policy="exact_half",
        solver_backend="highs",
    )

    assert result.periods[23].storage_soc_mwh["store-a"] == pytest.approx(5.0)
    first_hour_of_day_two = result.periods[24]
    assert (
        first_hour_of_day_two.storage_soc_mwh["store-a"]
        + first_hour_of_day_two.storage_discharge_mw["store-a"]
        - first_hour_of_day_two.storage_charge_mw["store-a"]
    ) == pytest.approx(5.0)
    assert result.periods[47].storage_soc_mwh["store-a"] == pytest.approx(5.0)


def test_daily_blocks_reset_each_storage_to_half_full() -> None:
    period = (
        (Bus("B1", 2.0, reference=True),),
        (Generator("g1", "B1", 0.0, 10.0, 1.0),),
        (),
    )
    periods = (period,) * 48

    result = solve_dcopf_daily_blocks(
        periods=periods,
        storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, 5.0),),
        owner_load_mw_by_team={"team-a": ({"B1": 2.0},) * 48},
        block_hours=24,
    )

    assert len(result.periods) == 48
    assert result.periods[23].storage_soc_mwh["store-a"] == pytest.approx(5.0)
    first_hour_of_day_two = result.periods[24]
    assert (
        first_hour_of_day_two.storage_soc_mwh["store-a"]
        + first_hour_of_day_two.storage_discharge_mw["store-a"]
        - first_hour_of_day_two.storage_charge_mw["store-a"]
    ) == pytest.approx(5.0)


def test_prepared_daily_blocks_split_into_exact_cycle_calls(monkeypatch) -> None:
    calls = []

    def fake_benders(_artifact_dir, **kwargs):
        calls.append(kwargs)
        periods = tuple(
            DCOPFResult(
                status="optimal",
                objective_cost=0.0,
                generation_mw={},
                angle_by_bus={},
                flow_mw_by_branch={},
                balance_dual_by_bus={},
                storage_soc_mwh={"store-a": 5.0},
            )
            for _ in kwargs["period_indices"]
        )
        return dcopf.DCOPFHorizonResult(
            status="optimal",
            periods=periods,
            final_soc_mwh_by_storage={"store-a": 5.0},
        )

    monkeypatch.setattr(dcopf, "solve_prepared_horizon_benders", fake_benders)
    result = dcopf.solve_prepared_daily_blocks(
        "unused-artifact",
        period_indices=tuple(range(48)),
        storage_assets=(StorageDispatchAsset("store-a", "team-a", "B1", 2.0, 10.0, 9.0),),
        owner_load_mw_by_team={"team-a": ({"B1": 2.0},) * 48},
    )

    assert [call["period_indices"] for call in calls] == [tuple(range(24)), tuple(range(24, 48))]
    assert all(call["terminal_soc_policy"] == "exact_half" for call in calls)
    assert all(call["storage_assets"][0].initial_soc_mwh == 5.0 for call in calls)
    assert result.final_soc_mwh_by_storage["store-a"] == pytest.approx(5.0)
