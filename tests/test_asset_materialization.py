from __future__ import annotations

import json

import pytest

from ercot_game.asset_state import AssetLedger, AssetStateError
from ercot_game.models import ConnectionPlan, GeneratorPlan, StoragePlan, TeamPlan


def _plan(
    *,
    team_id: str = "team-a",
    connections: tuple[ConnectionPlan, ...] | None = None,
    generators: tuple[GeneratorPlan, ...] = (),
    storages: tuple[StoragePlan, ...] = (),
) -> TeamPlan:
    return TeamPlan(
        team_id=team_id,
        connections=connections
        if connections is not None
        else (
            ConnectionPlan("site-1", "b0001", 40.0, (1.0, 0.5)),
        ),
        generators=generators,
        storages=storages,
    )


def _asset_plan() -> TeamPlan:
    return _plan(
        connections=(
            ConnectionPlan("site-old", "b0001", 40.0, (1.0, 0.5)),
        ),
        generators=(GeneratorPlan("gen-old", "site-old", 10.0, 16.0),),
        storages=(StoragePlan("store-old", "site-old", 5.0, 20.0),),
    )


def test_materialize_without_prior_assets_preserves_current_plan() -> None:
    plan = _plan(
        generators=(GeneratorPlan("gen-new", "site-1", 10.0, 16.0),),
        storages=(StoragePlan("store-new", "site-1", 5.0, 20.0),),
    )

    materialized = AssetLedger.empty().materialize_plan(plan, horizon=2)

    assert materialized == plan


def test_materialize_restores_omitted_assets_with_zero_load_placeholder() -> None:
    ledger = AssetLedger.empty().register_plan(_asset_plan(), round_key="2027_jan_jun")
    current = _plan(
        connections=(ConnectionPlan("site-new", "b0002", 30.0, (0.5, 0.5)),),
    )

    materialized = ledger.materialize_plan(current, horizon=4)

    assert materialized.connections == (
        ConnectionPlan("site-new", "b0002", 30.0, (0.5, 0.5)),
        ConnectionPlan("site-old", "b0001", 0.0, (0.0, 0.0, 0.0, 0.0)),
    )
    assert materialized.generators == (
        GeneratorPlan("gen-old", "site-old", 10.0, 16.0),
    )
    assert materialized.storages == (
        StoragePlan("store-old", "site-old", 5.0, 20.0, 10.0),
    )


def test_materialization_injects_persisted_soc_for_omitted_and_redeclared_storage() -> None:
    ledger = AssetLedger.empty().register_plan(_asset_plan(), round_key="2027_jan_jun")
    ledger = ledger.update_storage_soc({"team-a": {"store-old": 15.0}})

    omitted = ledger.materialize_plan(_plan(connections=()), horizon=2)
    redeclared = ledger.materialize_plan(
        _plan(
            connections=(ConnectionPlan("site-old", "b0001", 40.0, (1.0, 1.0)),),
            storages=(StoragePlan("store-old", "site-old", 5.0, 20.0),),
        ),
        horizon=2,
    )

    assert omitted.storages[0].initial_state_of_charge_mwh == 15.0
    assert redeclared.storages[0].initial_state_of_charge_mwh == 15.0


def test_materialization_reuses_canonical_asset_with_latest_submitted_label() -> None:
    ledger = AssetLedger.empty().register_plan(_asset_plan(), round_key="2027_jan_jun")
    current = _plan(
        connections=(ConnectionPlan("site-old", "b0001", 40.0, (1.0, 1.0)),),
        generators=(GeneratorPlan("generator-label-v2", "site-old", 10.0, 16.0),),
        storages=(StoragePlan("storage-label-v2", "site-old", 5.0, 20.0),),
    )

    materialized = ledger.materialize_plan(current, horizon=2)

    assert materialized.generators == (
        GeneratorPlan("generator-label-v2", "site-old", 10.0, 16.0),
    )
    assert materialized.storages == (
        StoragePlan("storage-label-v2", "site-old", 5.0, 20.0, 10.0),
    )


def test_new_storage_materialization_keeps_default_initial_soc() -> None:
    materialized = AssetLedger.empty().materialize_plan(
        _plan(storages=(StoragePlan("store-new", "site-1", 5.0, 20.0),)),
        horizon=2,
    )

    assert materialized.storages[0].initial_state_of_charge_mwh is None


def test_materialize_keeps_current_metadata_and_new_asset_specification() -> None:
    ledger = AssetLedger.empty().register_plan(_asset_plan(), round_key="2027_jan_jun")
    current = _plan(
        connections=(
            ConnectionPlan("site-old", "b0001", 75.0, (0.2, 0.3)),
            ConnectionPlan("site-new", "b0002", 30.0, (0.5, 0.5)),
        ),
        generators=(GeneratorPlan("gen-new", "site-new", 12.0, 22.0),),
    )

    materialized = ledger.materialize_plan(current, horizon=2)

    assert materialized.connections[0] == ConnectionPlan(
        "site-new", "b0002", 30.0, (0.5, 0.5)
    )
    assert materialized.connections[1] == ConnectionPlan(
        "site-old", "b0001", 75.0, (0.2, 0.3)
    )
    assert materialized.generators == (
        GeneratorPlan("gen-new", "site-new", 12.0, 22.0),
        GeneratorPlan("gen-old", "site-old", 10.0, 16.0),
    )


def test_materialize_accepts_exact_repeat_of_persisted_asset() -> None:
    plan = _asset_plan()
    ledger = AssetLedger.empty().register_plan(plan, round_key="2027_jan_jun")

    materialized = ledger.materialize_plan(plan, horizon=2)

    assert materialized.team_id == plan.team_id
    assert materialized.connections == plan.connections
    assert materialized.generators == plan.generators
    assert materialized.storages == (
        StoragePlan("store-old", "site-old", 5.0, 20.0, 10.0),
    )


def test_materialize_rejects_changed_persisted_asset() -> None:
    ledger = AssetLedger.empty().register_plan(_asset_plan(), round_key="2027_jan_jun")
    changed = _plan(
        connections=(ConnectionPlan("site-old", "b0001", 40.0, (1.0, 0.5)),),
        generators=(GeneratorPlan("gen-old", "site-old", 11.0, 16.0),),
    )

    with pytest.raises(AssetStateError, match="generator gen-old conflicts"):
        ledger.materialize_plan(changed, horizon=2)


def test_materialize_rejects_changed_persisted_storage() -> None:
    ledger = AssetLedger.empty().register_plan(_asset_plan(), round_key="2027_jan_jun")
    changed = _plan(
        connections=(ConnectionPlan("site-old", "b0001", 40.0, (1.0, 0.5)),),
        storages=(StoragePlan("store-old", "site-old", 6.0, 20.0),),
    )

    with pytest.raises(AssetStateError, match="storage store-old conflicts"):
        ledger.materialize_plan(changed, horizon=2)


def test_materialize_rejects_current_asset_with_missing_connection() -> None:
    plan = _plan(
        connections=(ConnectionPlan("site-1", "b0001", 40.0, (1.0, 0.5)),),
        generators=(GeneratorPlan("gen-1", "missing", 10.0, 16.0),),
    )

    with pytest.raises(AssetStateError, match="unknown connection site: missing"):
        AssetLedger.empty().materialize_plan(plan, horizon=2)


def test_materialize_rejects_duplicate_current_connection_ids() -> None:
    plan = _plan(
        connections=(
            ConnectionPlan("site-1", "b0001", 40.0, (1.0, 0.5)),
            ConnectionPlan("site-1", "b0002", 50.0, (0.5, 0.5)),
        ),
    )

    with pytest.raises(AssetStateError, match="duplicate connection site: site-1"):
        AssetLedger.empty().materialize_plan(plan, horizon=2)


def test_materialize_orders_all_components_deterministically() -> None:
    plan = _plan(
        connections=(
            ConnectionPlan("site-z", "b0003", 30.0, (0.1, 0.2)),
            ConnectionPlan("site-a", "b0001", 40.0, (1.0, 0.5)),
        ),
        generators=(
            GeneratorPlan("gen-z", "site-z", 10.0, 20.0),
            GeneratorPlan("gen-a", "site-a", 11.0, 21.0),
        ),
        storages=(
            StoragePlan("store-z", "site-z", 5.0, 20.0),
            StoragePlan("store-a", "site-a", 6.0, 25.0),
        ),
    )

    materialized = AssetLedger.empty().materialize_plan(plan, horizon=2)

    assert [item.connection_id for item in materialized.connections] == ["site-a", "site-z"]
    assert [item.generator_id for item in materialized.generators] == ["gen-a", "gen-z"]
    assert [item.storage_id for item in materialized.storages] == ["store-a", "store-z"]


def test_materialize_requires_positive_horizon() -> None:
    with pytest.raises(AssetStateError, match="horizon must be a positive integer"):
        AssetLedger.empty().materialize_plan(_plan(), horizon=0)


def test_materialize_after_ledger_json_round_trip_is_identical() -> None:
    ledger = AssetLedger.empty().register_plan(_asset_plan(), round_key="2027_jan_jun")
    current = _plan(connections=(ConnectionPlan("site-new", "b0002", 30.0, (0.5, 0.5)),))

    expected = ledger.materialize_plan(current, horizon=4)
    restored = AssetLedger.from_dict(json.loads(json.dumps(ledger.to_dict())))

    assert restored.materialize_plan(current, horizon=4) == expected
