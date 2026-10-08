"""Persistent state for team-owned assets across half-year rounds.

The dispatch evaluator still evaluates one frozen half-year at a time.  This
module provides the state contract needed by the multi-round game: a team's
generator and storage assets remain owned by that team after the round in
which they are first declared.  Asset identifiers are intentionally scoped
to a team because student teams choose those identifiers themselves.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Mapping

from .models import ConnectionPlan, GeneratorPlan, StoragePlan, TeamPlan


class AssetStateError(ValueError):
    """Raised when a plan conflicts with previously persisted team assets."""


@dataclass(frozen=True)
class GeneratorAsset:
    team_id: str
    generator_id: str
    connection_id: str
    bus_id: str
    capacity_mw: float
    marginal_cost_per_mwh: float


@dataclass(frozen=True)
class StorageAsset:
    team_id: str
    storage_id: str
    connection_id: str
    bus_id: str
    power_mw: float
    energy_mwh: float
    state_of_charge_mwh: float | None = None

    def __post_init__(self) -> None:
        _validate_storage_dimension(self.power_mw, "power_mw")
        _validate_storage_dimension(self.energy_mwh, "energy_mwh")
        state_of_charge_mwh = (
            self.energy_mwh * 0.5
            if self.state_of_charge_mwh is None
            else self.state_of_charge_mwh
        )
        _validate_storage_soc(
            state_of_charge_mwh,
            self.energy_mwh,
            team_id=self.team_id,
            storage_id=self.storage_id,
        )
        object.__setattr__(self, "state_of_charge_mwh", float(state_of_charge_mwh))


@dataclass(frozen=True)
class TeamAssetState:
    team_id: str
    generators: Mapping[str, GeneratorAsset] = field(default_factory=dict)
    storages: Mapping[str, StorageAsset] = field(default_factory=dict)
    last_round_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "generators", dict(self.generators))
        object.__setattr__(self, "storages", dict(self.storages))


@dataclass(frozen=True)
class AssetLedger:
    """Immutable, serializable ledger of all team-owned built assets."""

    version: int = 1
    teams: Mapping[str, TeamAssetState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "teams", dict(self.teams))

    @classmethod
    def empty(cls) -> "AssetLedger":
        return cls()

    def reset(self) -> "AssetLedger":
        """Return an empty ledger at a weekly-match boundary.

        A new weekly match starts with no built assets and no carried storage
        state of charge.  The current ledger remains immutable and unchanged.
        """
        return type(self)(version=self.version)

    def team_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.teams))

    def team(self, team_id: str) -> TeamAssetState:
        try:
            return self.teams[team_id]
        except KeyError:
            raise KeyError(f"unknown team in asset ledger: {team_id}") from None

    def register_plan(self, plan: TeamPlan, *, round_key: str) -> "AssetLedger":
        """Record newly built assets and preserve earlier identical assets.

        A repeated asset ID is valid when its immutable specification is the
        same.  Changing its site or capacity is rejected because that would
        silently move or resize an already-built asset between rounds.
        """
        if not round_key or not round_key.strip():
            raise AssetStateError("round_key must be a non-empty string")
        connections = _unique_connections(plan.connections)
        current = self.teams.get(plan.team_id, TeamAssetState(team_id=plan.team_id))
        generators = dict(current.generators)
        storages = dict(current.storages)

        for generator in _unique_assets(plan.generators, kind="generator").values():
            connection = _connection_for_asset(connections, generator.connection_id)
            candidate = _generator_asset(plan.team_id, generator, connection)
            previous = generators.get(generator.generator_id)
            if previous is not None and previous != candidate:
                raise AssetStateError(
                    f"generator {generator.generator_id} conflicts with its persisted specification"
                )
            generators[generator.generator_id] = candidate

        for storage in _unique_assets(plan.storages, kind="storage").values():
            connection = _connection_for_asset(connections, storage.connection_id)
            previous = storages.get(storage.storage_id)
            candidate = _storage_asset(
                plan.team_id,
                storage,
                connection,
                state_of_charge_mwh=(
                    previous.state_of_charge_mwh
                    if previous is not None
                    else storage.energy_mwh * 0.5
                ),
            )
            if previous is not None and not _same_storage_spec(previous, candidate):
                raise AssetStateError(
                    f"storage {storage.storage_id} conflicts with its persisted specification"
                )
            storages[storage.storage_id] = candidate

        updated = dict(self.teams)
        updated[plan.team_id] = TeamAssetState(
            team_id=plan.team_id,
            generators=generators,
            storages=storages,
            last_round_key=round_key,
        )
        return AssetLedger(version=self.version, teams=updated)

    def materialize_plan(self, plan: TeamPlan, *, horizon: int) -> TeamPlan:
        """Return the active portfolio represented by ``plan`` and this ledger.

        A current submission may omit assets built in earlier rounds.  Those
        assets are restored from the team's ledger, while an omitted
        connection site is represented by a zero-load placeholder so the
        single-round evaluator can still see the asset without inventing new
        demand.  This method only constructs the evaluator input; it does not
        mutate the ledger or perform dispatch/scoring.
        """
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
            raise AssetStateError("horizon must be a positive integer")

        connections = _unique_connections(plan.connections)
        current = self.teams.get(plan.team_id, TeamAssetState(team_id=plan.team_id))
        current_generators = _unique_assets(plan.generators, kind="generator")
        current_storages = _unique_assets(plan.storages, kind="storage")

        for generator in current_generators.values():
            connection = _connection_for_asset(connections, generator.connection_id)
            persisted = current.generators.get(generator.generator_id)
            candidate = _generator_asset(plan.team_id, generator, connection)
            if persisted is not None and persisted != candidate:
                raise AssetStateError(
                    f"generator {generator.generator_id} conflicts with its persisted specification"
                )

        for storage in current_storages.values():
            connection = _connection_for_asset(connections, storage.connection_id)
            persisted = current.storages.get(storage.storage_id)
            candidate = _storage_asset(plan.team_id, storage, connection)
            if persisted is not None and not _same_storage_spec(persisted, candidate):
                raise AssetStateError(
                    f"storage {storage.storage_id} conflicts with its persisted specification"
                )

        _validate_persisted_connection_buses(connections, current)

        materialized_connections = dict(connections)
        for asset in (*current.generators.values(), *current.storages.values()):
            if asset.connection_id not in materialized_connections:
                materialized_connections[asset.connection_id] = ConnectionPlan(
                    connection_id=asset.connection_id,
                    bus_id=asset.bus_id,
                    capacity_mw=0.0,
                    profile=(0.0,) * horizon,
                )

        generators = {
            generator_id: GeneratorPlan(
                generator_id=asset.generator_id,
                connection_id=asset.connection_id,
                capacity_mw=asset.capacity_mw,
                marginal_cost_per_mwh=asset.marginal_cost_per_mwh,
            )
            for generator_id, asset in current.generators.items()
        }
        generators.update(
            {
                generator_id: generator
                for generator_id, generator in current_generators.items()
                if generator_id not in current.generators
            }
        )

        storages = {
            storage_id: StoragePlan(
                storage_id=asset.storage_id,
                connection_id=asset.connection_id,
                power_mw=asset.power_mw,
                energy_mwh=asset.energy_mwh,
                initial_state_of_charge_mwh=asset.state_of_charge_mwh,
            )
            for storage_id, asset in current.storages.items()
        }
        storages.update(
            {
                storage_id: storage
                for storage_id, storage in current_storages.items()
                if storage_id not in current.storages
            }
        )

        return TeamPlan(
            team_id=plan.team_id,
            connections=tuple(materialized_connections[key] for key in sorted(materialized_connections)),
            generators=tuple(generators[key] for key in sorted(generators)),
            storages=tuple(storages[key] for key in sorted(storages)),
            sites=plan.sites,
        )

    def update_storage_soc(
        self,
        state_of_charge_mwh_by_team: Mapping[str, Mapping[str, float]],
    ) -> "AssetLedger":
        """Return a copy with validated end-of-round storage SOC values."""
        if not isinstance(state_of_charge_mwh_by_team, Mapping):
            raise AssetStateError("storage state of charge must be a mapping by team")
        updated = dict(self.teams)
        for team_id, storage_values in state_of_charge_mwh_by_team.items():
            if team_id not in self.teams:
                raise AssetStateError(f"unknown team in storage state of charge: {team_id}")
            if not isinstance(storage_values, Mapping):
                raise AssetStateError(
                    f"storage state of charge for team {team_id} must be a mapping"
                )
            current = self.teams[team_id]
            storages = dict(current.storages)
            for storage_id, value in storage_values.items():
                if storage_id not in storages:
                    raise AssetStateError(
                        f"unknown storage asset for team {team_id}: {storage_id}"
                    )
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise AssetStateError(
                        f"storage state of charge must be finite for {team_id}/{storage_id}"
                    )
                if not math.isfinite(value):
                    raise AssetStateError(
                        f"storage state of charge must be finite for {team_id}/{storage_id}"
                    )
                asset = storages[storage_id]
                if value < 0.0 or value > asset.energy_mwh:
                    raise AssetStateError(
                        f"storage state of charge must be between 0 and energy capacity "
                        f"for {team_id}/{storage_id}"
                    )
                if value < asset.energy_mwh * 0.5:
                    raise AssetStateError(
                        f"storage state of charge must be at least half of energy capacity "
                        f"for {team_id}/{storage_id}"
                    )
                storages[storage_id] = StorageAsset(
                    team_id=asset.team_id,
                    storage_id=asset.storage_id,
                    connection_id=asset.connection_id,
                    bus_id=asset.bus_id,
                    power_mw=asset.power_mw,
                    energy_mwh=asset.energy_mwh,
                    state_of_charge_mwh=float(value),
                )
            updated[team_id] = TeamAssetState(
                team_id=current.team_id,
                generators=current.generators,
                storages=storages,
                last_round_key=current.last_round_key,
            )
        return AssetLedger(version=self.version, teams=updated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "teams": {
                team_id: {
                    "team_id": state.team_id,
                    "last_round_key": state.last_round_key,
                    "generators": {
                        asset_id: asdict(asset)
                        for asset_id, asset in sorted(state.generators.items())
                    },
                    "storages": {
                        asset_id: asdict(asset)
                        for asset_id, asset in sorted(state.storages.items())
                    },
                }
                for team_id, state in sorted(self.teams.items())
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AssetLedger":
        version = payload.get("version")
        if version != 1:
            raise AssetStateError(f"unsupported asset ledger version: {version!r}")
        teams: dict[str, TeamAssetState] = {}
        for team_id, raw_state in _mapping(payload.get("teams"), "teams").items():
            if not isinstance(raw_state, Mapping):
                raise AssetStateError(f"team asset state must be an object: {team_id}")
            generators = {
                asset_id: GeneratorAsset(**_asset_payload(raw_asset, "generator", asset_id))
                for asset_id, raw_asset in _mapping(raw_state.get("generators"), "generators").items()
            }
            storages = {
                asset_id: StorageAsset(**_asset_payload(raw_asset, "storage", asset_id))
                for asset_id, raw_asset in _mapping(raw_state.get("storages"), "storages").items()
            }
            teams[team_id] = TeamAssetState(
                team_id=str(raw_state.get("team_id", team_id)),
                generators=generators,
                storages=storages,
                last_round_key=raw_state.get("last_round_key"),
            )
        return cls(version=version, teams=teams)


def _connection_for_asset(connections: Mapping[str, Any], connection_id: str) -> Any:
    try:
        return connections[connection_id]
    except KeyError:
        raise AssetStateError(
            f"asset references unknown connection site: {connection_id}"
        ) from None


def _unique_connections(connections: tuple[Any, ...]) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for connection in connections:
        if connection.connection_id in indexed:
            raise AssetStateError(f"duplicate connection site: {connection.connection_id}")
        indexed[connection.connection_id] = connection
    return indexed


def _unique_assets(assets: tuple[Any, ...], *, kind: str) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    field = f"{kind}_id"
    for asset in assets:
        asset_id = getattr(asset, field)
        if asset_id in indexed:
            raise AssetStateError(f"duplicate {kind} asset: {asset_id}")
        indexed[asset_id] = asset
    return indexed


def _generator_asset(
    team_id: str,
    generator: GeneratorPlan,
    connection: Any,
) -> GeneratorAsset:
    return GeneratorAsset(
        team_id=team_id,
        generator_id=generator.generator_id,
        connection_id=generator.connection_id,
        bus_id=connection.bus_id,
        capacity_mw=generator.capacity_mw,
        marginal_cost_per_mwh=generator.marginal_cost_per_mwh,
    )


def _storage_asset(
    team_id: str,
    storage: StoragePlan,
    connection: Any,
    state_of_charge_mwh: float | None = None,
) -> StorageAsset:
    return StorageAsset(
        team_id=team_id,
        storage_id=storage.storage_id,
        connection_id=storage.connection_id,
        bus_id=connection.bus_id,
        power_mw=storage.power_mw,
        energy_mwh=storage.energy_mwh,
        state_of_charge_mwh=(
            storage.energy_mwh * 0.5
            if state_of_charge_mwh is None
            else state_of_charge_mwh
        ),
    )


def _validate_storage_soc(
    value: Any,
    energy_mwh: float,
    *,
    team_id: str,
    storage_id: str,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AssetStateError(
            f"storage state of charge must be finite for {team_id}/{storage_id}"
        )
    if value < energy_mwh * 0.5:
        raise AssetStateError(
            f"storage state of charge for {team_id}/{storage_id} "
            "must be at least half of energy capacity"
        )
    if value > energy_mwh:
        raise AssetStateError(
            f"storage state of charge for {team_id}/{storage_id} "
            "exceeds energy capacity"
        )


def _validate_storage_dimension(value: Any, field: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise AssetStateError(f"storage {field} must be finite and positive")


def _same_storage_spec(left: StorageAsset, right: StorageAsset) -> bool:
    return (
        left.team_id,
        left.storage_id,
        left.connection_id,
        left.bus_id,
        left.power_mw,
        left.energy_mwh,
    ) == (
        right.team_id,
        right.storage_id,
        right.connection_id,
        right.bus_id,
        right.power_mw,
        right.energy_mwh,
    )


def _validate_persisted_connection_buses(
    connections: Mapping[str, Any],
    state: TeamAssetState,
) -> None:
    for asset in (*state.generators.values(), *state.storages.values()):
        connection = connections.get(asset.connection_id)
        if connection is not None and connection.bus_id != asset.bus_id:
            raise AssetStateError(
                f"connection {asset.connection_id} conflicts with its persisted source bus"
            )


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AssetStateError(f"asset ledger field {name} must be an object")
    return value


def _asset_payload(value: Any, kind: str, asset_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AssetStateError(f"{kind} asset must be an object: {asset_id}")
    payload = dict(value)
    payload.setdefault(f"{kind}_id", asset_id)
    if kind == "storage" and (
        "state_of_charge_mwh" in payload and payload["state_of_charge_mwh"] is None
    ):
        raise AssetStateError("storage state of charge must be finite")
    required = {"team_id", f"{kind}_id", "connection_id", "bus_id"}
    missing = sorted(field for field in required if not payload.get(field))
    if missing:
        raise AssetStateError(f"{kind} asset {asset_id} is missing: {', '.join(missing)}")
    return payload
