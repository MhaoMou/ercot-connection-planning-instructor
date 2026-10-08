from __future__ import annotations

import json
from pathlib import Path

import pytest

from ercot_game.asset_state import AssetLedger, AssetStateError, StorageAsset
from ercot_game.models import ConnectionPlan, GeneratorPlan, SitePlan, StoragePlan, TeamPlan


def _plan(
    *,
    team_id: str = "team-a",
    site_id: str = "site-1",
    bus_id: str = "b0001",
    generator_id: str = "gen-1",
    generator_capacity: float = 10.0,
    storage_id: str = "store-1",
    storage_energy: float = 20.0,
) -> TeamPlan:
    return TeamPlan(
        team_id=team_id,
        connections=(
            ConnectionPlan(
                connection_id=site_id,
                bus_id=bus_id,
                capacity_mw=40.0,
                profile=(1.0,),
            ),
        ),
        generators=(
            GeneratorPlan(
                generator_id=generator_id,
                connection_id=site_id,
                capacity_mw=generator_capacity,
                marginal_cost_per_mwh=16.0,
            ),
        ),
        storages=(
            StoragePlan(
                storage_id=storage_id,
                connection_id=site_id,
                power_mw=5.0,
                energy_mwh=storage_energy,
            ),
        ),
    )


def test_register_plan_persists_team_owned_assets_and_round_metadata() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")

    assert ledger.version == 2
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
    del payload["teams"]["team-a"]["storages"]["storage:team-a:site-1"]["state_of_charge_mwh"]

    restored = AssetLedger.from_dict(payload)

    assert restored.team("team-a").storages["store-1"].state_of_charge_mwh == 10.0


def test_persisted_storage_soc_null_is_rejected_instead_of_defaulting() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")
    payload = ledger.to_dict()
    payload["teams"]["team-a"]["storages"]["storage:team-a:site-1"]["state_of_charge_mwh"] = None

    with pytest.raises(AssetStateError, match="storage state of charge must be finite"):
        AssetLedger.from_dict(payload)


@pytest.mark.parametrize("value", [9.0, 21.0, float("nan"), float("inf")])
def test_persisted_storage_soc_must_be_finite_and_within_terminal_bounds(value) -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")
    payload = ledger.to_dict()
    payload["teams"]["team-a"]["storages"]["storage:team-a:site-1"]["state_of_charge_mwh"] = value

    with pytest.raises(AssetStateError, match="storage state of charge"):
        AssetLedger.from_dict(payload)


def test_legacy_six_argument_storage_asset_defaults_to_half_full() -> None:
    asset = StorageAsset("team-a", "store-1", "site-1", "b0001", 5.0, 20.0)

    assert asset.state_of_charge_mwh == 10.0


def test_explicit_none_storage_soc_keeps_legacy_half_full_constructor_behavior() -> None:
    asset = StorageAsset("team-a", "store-1", "site-1", "b0001", 5.0, 20.0, None)

    assert asset.state_of_charge_mwh == 10.0


@pytest.mark.parametrize("value", [9.0, 21.0, float("nan")])
def test_direct_storage_asset_rejects_invalid_explicit_soc(value) -> None:
    with pytest.raises(AssetStateError, match="storage state of charge"):
        StorageAsset("team-a", "store-1", "site-1", "b0001", 5.0, 20.0, value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("power_mw", 0.0),
        ("power_mw", -1.0),
        ("power_mw", float("nan")),
        ("power_mw", True),
        ("energy_mwh", 0.0),
        ("energy_mwh", -1.0),
        ("energy_mwh", float("nan")),
        ("energy_mwh", True),
    ],
)
def test_direct_storage_asset_rejects_invalid_dimensions(field, value) -> None:
    dimensions = {"power_mw": 5.0, "energy_mwh": 20.0}
    dimensions[field] = value

    with pytest.raises(AssetStateError, match=f"storage {field} must be finite and positive"):
        StorageAsset(
            "team-a",
            "store-1",
            "site-1",
            "b0001",
            dimensions["power_mw"],
            dimensions["energy_mwh"],
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("power_mw", 0.0),
        ("power_mw", -1.0),
        ("power_mw", float("nan")),
        ("power_mw", True),
        ("energy_mwh", 0.0),
        ("energy_mwh", -1.0),
        ("energy_mwh", float("nan")),
        ("energy_mwh", True),
    ],
)
def test_asset_ledger_from_dict_rejects_invalid_storage_dimensions(field, value) -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")
    payload = ledger.to_dict()
    payload["teams"]["team-a"]["storages"]["storage:team-a:site-1"][field] = value

    with pytest.raises(AssetStateError, match=f"storage {field} must be finite and positive"):
        AssetLedger.from_dict(payload)


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
        ({"team-a": {"store-1": 9.0}}, "at least half"),
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


def test_same_team_site_and_kind_reuses_asset_when_submitted_label_changes() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")
    ledger = ledger.update_storage_soc({"team-a": {"store-1": 15.0}})

    renamed = ledger.register_plan(
        _plan(generator_id="friendly-generator", storage_id="battery-label"),
        round_key="2027_jul_dec",
    )

    team = renamed.team("team-a")
    assert len(team.generators) == 1
    assert len(team.storages) == 1
    assert team.generators["friendly-generator"].asset_key == "generator:team-a:site-1"
    assert team.storages["battery-label"].asset_key == "storage:team-a:site-1"
    assert team.storages["battery-label"].state_of_charge_mwh == 15.0


def test_different_sites_and_asset_kinds_keep_distinct_internal_identities() -> None:
    first = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")
    combined = first.register_plan(
        _plan(site_id="site-2", bus_id="b0002", generator_id="gen-1", storage_id="store-1"),
        round_key="2027_jul_dec",
    )

    team = combined.team("team-a")
    assert len(team.generators) == 2
    assert len(team.storages) == 2
    assert {asset.asset_key for asset in team.generators.values()} == {
        "generator:team-a:site-1",
        "generator:team-a:site-2",
    }
    assert {asset.asset_key for asset in team.storages.values()} == {
        "storage:team-a:site-1",
        "storage:team-a:site-2",
    }


def test_round_trip_preserves_assets_when_display_label_collides_with_canonical_key() -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(
            ConnectionPlan("site-a", "b0001", 40.0, (1.0,)),
            ConnectionPlan("site-b", "b0002", 30.0, (1.0,)),
        ),
        generators=(
            GeneratorPlan("generator:team-a:site-b", "site-a", 10.0, 16.0),
            GeneratorPlan("generator-at-site-b", "site-b", 12.0, 22.0),
        ),
        storages=(
            StoragePlan("storage-at-site-a", "site-a", 5.0, 20.0),
            StoragePlan("storage-at-site-b", "site-b", 4.0, 16.0),
        ),
    )

    ledger = AssetLedger.empty().register_plan(plan, round_key="2027_jan_jun")
    ledger = ledger.update_storage_soc(
        {"team-a": {"storage-at-site-a": 15.0, "storage-at-site-b": 12.0}}
    )

    payload = ledger.to_dict()
    restored = AssetLedger.from_dict(json.loads(json.dumps(payload)))

    assert set(payload["teams"]["team-a"]["generators"]) == {
        "generator:team-a:site-a",
        "generator:team-a:site-b",
    }
    team = restored.team("team-a")
    assert team.generators.canonical_get("generator:team-a:site-a") == team.generators[
        "generator:team-a:site-a"
    ]
    assert team.generators.canonical_get("generator:team-a:site-a").capacity_mw == 10.0
    assert team.generators.canonical_get("generator:team-a:site-a").marginal_cost_per_mwh == 16.0
    assert team.generators.canonical_get("generator:team-a:site-b").capacity_mw == 12.0
    assert team.generators.canonical_get("generator:team-a:site-b").marginal_cost_per_mwh == 22.0
    assert team.storages["storage-at-site-a"].state_of_charge_mwh == 15.0
    assert team.storages["storage-at-site-b"].state_of_charge_mwh == 12.0
    assert restored.to_dict() == payload


def test_reset_clears_assets_and_storage_soc_for_a_new_weekly_match() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")
    ledger = ledger.update_storage_soc({"team-a": {"store-1": 15.0}})

    reset = ledger.reset()

    assert reset.team_ids() == ()
    assert ledger.team("team-a").storages["store-1"].state_of_charge_mwh == 15.0


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


def test_register_plan_rejects_duplicate_site_only_ids() -> None:
    plan = TeamPlan(
        team_id="team-a",
        sites=(
            SitePlan("site-1", "b0001", 40.0),
            SitePlan("site-1", "b0002", 30.0),
        ),
    )

    with pytest.raises(AssetStateError, match="duplicate site_id: site-1"):
        AssetLedger.empty().register_plan(plan, round_key="2027_jan_jun")


def test_asset_collection_alias_lookup_is_explicit_and_ambiguous_aliases_are_rejected() -> None:
    plan = TeamPlan(
        team_id="team-a",
        connections=(
            ConnectionPlan("site-a", "b0001", 40.0, (1.0,)),
            ConnectionPlan("site-b", "b0002", 30.0, (1.0,)),
        ),
        generators=(
            GeneratorPlan("same-label", "site-a", 10.0, 16.0),
            GeneratorPlan("same-label", "site-b", 12.0, 22.0),
        ),
    )
    ledger = AssetLedger.empty().register_plan(plan, round_key="2027_jan_jun")
    generators = ledger.team("team-a").generators

    assert generators["generator:team-a:site-a"].capacity_mw == 10.0
    with pytest.raises(KeyError, match="ambiguous submitted asset label: same-label"):
        generators["same-label"]


def test_asset_ledger_migrates_genuine_version_one_label_keyed_fixture() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "asset_ledger_v1_label_keyed.json").read_text()
    )

    restored = AssetLedger.from_dict(fixture)

    assert restored.version == 2
    assert restored.team("team-a").generators["legacy-generator"].capacity_mw == 10.0
    assert restored.team("team-a").storages["legacy-storage"].state_of_charge_mwh == 10.0
    assert set(restored.to_dict()["teams"]["team-a"]["generators"]) == {
        "generator:team-a:site-1"
    }


def test_asset_ledger_migrates_version_one_canonical_keyed_payload() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")
    payload = ledger.to_dict()
    payload["version"] = 1

    restored = AssetLedger.from_dict(payload)

    assert restored.version == 2
    assert restored.to_dict() == ledger.to_dict()


def test_asset_ledger_version_two_requires_canonical_keys() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")
    payload = ledger.to_dict()
    asset = payload["teams"]["team-a"]["generators"].pop(
        "generator:team-a:site-1"
    )
    payload["teams"]["team-a"]["generators"]["legacy-generator"] = asset

    with pytest.raises(AssetStateError, match="version 2 requires canonical generator keys"):
        AssetLedger.from_dict(payload)


def test_asset_ledger_rejects_duplicate_canonical_identity_before_collection_build() -> None:
    ledger = AssetLedger.empty().register_plan(_plan(), round_key="2027_jan_jun")
    payload = ledger.to_dict()
    payload["version"] = 1
    generators = payload["teams"]["team-a"]["generators"]
    duplicate = dict(generators["generator:team-a:site-1"])
    duplicate["generator_id"] = "other-label"
    generators["other-label"] = duplicate

    with pytest.raises(AssetStateError, match="duplicate canonical generator identity"):
        AssetLedger.from_dict(payload)
