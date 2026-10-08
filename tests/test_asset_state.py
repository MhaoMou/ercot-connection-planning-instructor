from __future__ import annotations

import json

import pytest

from ercot_game.asset_state import AssetLedger, AssetStateError
from ercot_game.models import ConnectionPlan, GeneratorPlan, StoragePlan, TeamPlan


def _plan(*, team_id: str = "team-a", generator_capacity: float = 10.0, storage_energy: float = 20.0) -> TeamPlan:
    return TeamPlan(
        team_id=team_id,
        connections=(
            ConnectionPlan(
                connection_id="site-1",
                bus_id="b0001",
                capacity_mw=40.0,
                profile=(1.0,),
            ),
        ),
        generators=(
            GeneratorPlan(
                generator_id="gen-1",
                connection_id="site-1",
                capacity_mw=generator_capacity,
                marginal_cost_per_mwh=16.0,
            ),
        ),
        storages=(
            StoragePlan(
                storage_id="store-1",
                connection_id="site-1",
                power_mw=5.0,
                energy_mwh=storage_energy,
            ),
        ),
    )


def test_register_plan_persists_team_owned_assets_and_round_metadata() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")

    assert ledger.version == 1
    assert ledger.team("team-a").generators["gen-1"].bus_id == "b0001"
    assert ledger.team("team-a").generators["gen-1"].capacity_mw == 10.0
    assert ledger.team("team-a").storages["store-1"].energy_mwh == 20.0
    assert ledger.team("team-a").storages["store-1"].state_of_charge_mwh == 10.0
    assert ledger.team("team-a").last_round_key == "2027_jan_jun"


def test_round_trip_json_preserves_assets_deterministically() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")

    payload = ledger.to_dict()
    restored = AssetLedger.from_dict(json.loads(json.dumps(payload)))

    assert restored.to_dict() == payload


def test_legacy_storage_asset_without_soc_loads_half_full() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")
    payload = ledger.to_dict()
    del payload["teams"]["team-a"]["storages"]["store-1"]["state_of_charge_mwh"]

    restored = AssetLedger.from_dict(payload)

    assert restored.team("team-a").storages["store-1"].state_of_charge_mwh == 10.0


def test_update_storage_soc_returns_new_ledger_and_round_trips() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")

    updated = ledger.update_storage_soc({"team-a": {"store-1": 15.0}})

    assert ledger.team("team-a").storages["store-1"].state_of_charge_mwh == 10.0
    assert updated.team("team-a").storages["store-1"].state_of_charge_mwh == 15.0
    assert AssetLedger.from_dict(updated.to_dict()) == updated


@pytest.mark.parametrize(
    "mapping, message",
    [
        ({"team-a": {"store-1": -1.0}}, "between 0 and energy capacity"),
        ({"team-a": {"store-1": 21.0}}, "between 0 and energy capacity"),
        ({"team-a": {"store-1": float("nan")}}, "finite"),
        ({"team-x": {"store-1": 10.0}}, "unknown team"),
        ({"team-a": {"store-x": 10.0}}, "unknown storage asset"),
    ],
)
def test_update_storage_soc_validates_bounds_and_ownership(mapping, message) -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")

    with pytest.raises(AssetStateError, match=message):
        ledger.update_storage_soc(mapping)


def test_same_asset_id_can_be_reused_only_with_identical_specification() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")

    unchanged = ledger.register_plan(_plan(), round_key="2027_jul_dec")
    assert unchanged.team("team-a").last_round_key == "2027_jul_dec"

    with pytest.raises(AssetStateError, match="generator gen-1 conflicts"):
        ledger.register_plan(
            _plan(generator_capacity=11.0),
            round_key="2027_jul_dec",
        )


def test_register_plan_preserves_existing_storage_soc() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")
    ledger = ledger.update_storage_soc({"team-a": {"store-1": 15.0}})

    repeated = ledger.register_plan(_plan(), round_key="2027_jul_dec")

    assert repeated.team("team-a").storages["store-1"].state_of_charge_mwh == 15.0


def test_asset_ids_are_scoped_to_team_ownership() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")
    ledger = ledger.register_plan(
        _plan(team_id="team-b"),
        round_key="2027_jul_dec",
    )

    assert set(ledger.team_ids()) == {"team-a", "team-b"}
    assert ledger.team("team-a").generators["gen-1"].team_id == "team-a"
    assert ledger.team("team-b").generators["gen-1"].team_id == "team-b"


def test_unknown_team_lookup_is_explicit() -> None:
    with pytest.raises(KeyError, match="team-x"):
        AssetLedger.empty().team("team-x")


@pytest.mark.parametrize(
    ("connections", "generators", "storages", "message"),
    (
        (
            (
                ConnectionPlan("site-1", "b0001", 40.0, (1.0,)),
                ConnectionPlan("site-1", "b0002", 40.0, (1.0,)),
            ),
            (),
            (),
            "duplicate connection site: site-1",
        ),
        (
            (ConnectionPlan("site-1", "b0001", 40.0, (1.0,)),),
            (
                GeneratorPlan("gen-1", "site-1", 10.0, 16.0),
                GeneratorPlan("gen-1", "site-1", 10.0, 16.0),
            ),
            (),
            "duplicate generator asset: gen-1",
        ),
        (
            (ConnectionPlan("site-1", "b0001", 40.0, (1.0,)),),
            (),
            (
                StoragePlan("store-1", "site-1", 5.0, 20.0),
                StoragePlan("store-1", "site-1", 5.0, 20.0),
            ),
            "duplicate storage asset: store-1",
        ),
    ),
)
def test_register_plan_rejects_duplicate_ids(
    connections, generators, storages, message
) -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=connections,
        generators=generators,
        storages=storages,
    )

    with pytest.raises(AssetStateError, match=message):
        AssetLedger.empty().register_plan(plan, round_key="2027_jan_jun")
