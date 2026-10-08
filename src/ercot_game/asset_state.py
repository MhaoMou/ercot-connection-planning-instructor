"""Persistent state for team-owned assets across half-year rounds.

The dispatch evaluator still evaluates one frozen half-year at a time.  This
module provides the state contract needed by the multi-round game: a team's
generator and storage assets remain owned by that team after the round in
which they are first declared.  Students may choose display labels, while
the ledger uses a stable team/site/kind identity internally.
"""

from __future__ import annotations

from collections.abc import Mapping as ABCMapping
from dataclasses import asdict, dataclass, field
import math
from typing import Any, Mapping

from .models import ConnectionPlan, GeneratorPlan, SitePlan, StoragePlan, TeamPlan


ASSET_LEDGER_SCHEMA_VERSION = 2


class AssetStateError(ValueError):
    """Raised when a plan conflicts with previously persisted team assets."""


def canonical_asset_key(team_id: str, connection_id: str, kind: str) -> str:
    """Return the stable ledger identity for one team/site asset kind."""
    return f"{kind}:{team_id}:{connection_id}"


def canonical_asset_keys_for_plan(plan: TeamPlan, *, kind: str) -> frozenset[str]:
    """Return declared team/site identities, including site-only assets."""
    _, generators, storages = _plan_components(plan)
    assets = generators if kind == "generator" else storages if kind == "storage" else None
    if assets is None:
        raise ValueError(f"unsupported asset kind: {kind}")
    return frozenset(
        canonical_asset_key(plan.team_id, asset.connection_id, kind)
        for asset in assets.values()
    )


def _canonical_asset_key(team_id: str, connection_id: str, kind: str) -> str:
    return canonical_asset_key(team_id, connection_id, kind)


class _AssetCollection(ABCMapping):
    """Canonical asset storage with explicit submitted-label aliases.

    Canonical keys are the primary lookup and iteration keys. A submitted
    label is accepted only as an alias when it matches exactly one asset;
    ambiguous labels raise ``KeyError`` instead of selecting an arbitrary
    asset. ``items()`` and serialization therefore always expose canonical
    identities.
    """

    def __init__(self, assets: Mapping[str, Any] | None = None) -> None:
        values = assets.values() if isinstance(assets, Mapping) else (assets or ())
        self._assets = {asset.asset_key: asset for asset in values}

    def __getitem__(self, key: str) -> Any:
        if key in self._assets:
            return self._assets[key]
        matches = [asset for asset in self._assets.values() if _asset_label(asset) == key]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise KeyError(f"ambiguous submitted asset label: {key}")
        raise KeyError(key)

    def __iter__(self):
        yield from sorted(self._assets)

    def __len__(self) -> int:
        return len(self._assets)

    def items(self):
        for key in self:
            yield key, self[key]

    def values(self):
        return self._assets.values()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _AssetCollection):
            return self._assets == other._assets
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return NotImplemented

    def canonical_get(self, asset_key: str) -> Any | None:
        return self._assets.get(asset_key)

    def canonical_items(self):
        return self._assets.items()

    def canonical_values(self):
        return self._assets.values()

    def canonical_dict(self) -> dict[str, Any]:
        return dict(self._assets)


@dataclass(frozen=True)
class GeneratorAsset:
    team_id: str
    generator_id: str
    connection_id: str
    bus_id: str
    capacity_mw: float
    marginal_cost_per_mwh: float
    asset_key: str | None = None

    def __post_init__(self) -> None:
        expected = _canonical_asset_key(self.team_id, self.connection_id, "generator")
        if self.asset_key not in (None, expected):
            raise AssetStateError(
                f"generator asset key does not match team/site identity: {self.asset_key}"
            )
        object.__setattr__(self, "asset_key", expected)


@dataclass(frozen=True)
class StorageAsset:
    team_id: str
    storage_id: str
    connection_id: str
    bus_id: str
    power_mw: float
    energy_mwh: float
    state_of_charge_mwh: float | None = None
    asset_key: str | None = None

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
        expected = _canonical_asset_key(self.team_id, self.connection_id, "storage")
        if self.asset_key not in (None, expected):
            raise AssetStateError(
                f"storage asset key does not match team/site identity: {self.asset_key}"
            )
        object.__setattr__(self, "asset_key", expected)


@dataclass(frozen=True)
class TeamAssetState:
    team_id: str
    sites: Mapping[str, SitePlan] = field(default_factory=dict)
    generators: Mapping[str, GeneratorAsset] = field(default_factory=dict)
    storages: Mapping[str, StorageAsset] = field(default_factory=dict)
    last_round_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sites", dict(self.sites))
        object.__setattr__(self, "generators", _AssetCollection(self.generators))
        object.__setattr__(self, "storages", _AssetCollection(self.storages))


@dataclass(frozen=True)
class AssetLedger:
    """Immutable, serializable ledger of all team-owned built assets."""

    version: int = ASSET_LEDGER_SCHEMA_VERSION
    teams: Mapping[str, TeamAssetState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.version != ASSET_LEDGER_SCHEMA_VERSION:
            raise AssetStateError(
                f"asset ledger instances must use schema version {ASSET_LEDGER_SCHEMA_VERSION}"
            )
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

        A repeated team/site/kind identity is valid when its immutable
        specification is the same.  The submitted ID is only a display label
        and may change between rounds; changing the site or capacity is
        rejected because that would silently move or resize an asset.
        """
        if not round_key or not round_key.strip():
            raise AssetStateError("round_key must be a non-empty string")
        connections, declared_generators, declared_storages = _plan_components(plan)
        current = self.teams.get(plan.team_id, TeamAssetState(team_id=plan.team_id))
        sites = dict(current.sites)
        for site_id, site in _site_plans(plan).items():
            previous = sites.get(site_id)
            if previous is not None and not _same_site_connection(previous, site):
                raise AssetStateError(
                    f"site {site_id} conflicts with its persisted connection specification"
                )
            sites[site_id] = site
        generators = current.generators.canonical_dict()
        storages = current.storages.canonical_dict()

        for generator in declared_generators.values():
            connection = _connection_for_asset(connections, generator.connection_id)
            candidate = _generator_asset(plan.team_id, generator, connection)
            previous = generators.get(candidate.asset_key)
            if previous is not None and not _same_generator_spec(previous, candidate):
                raise AssetStateError(
                    f"generator {generator.generator_id} conflicts with its persisted specification"
                )
            generators[candidate.asset_key] = candidate

        for storage in declared_storages.values():
            connection = _connection_for_asset(connections, storage.connection_id)
            asset_key = _canonical_asset_key(plan.team_id, storage.connection_id, "storage")
            previous = storages.get(asset_key)
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
            storages[candidate.asset_key] = candidate

        updated = dict(self.teams)
        updated[plan.team_id] = TeamAssetState(
            team_id=plan.team_id,
            sites=sites,
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

        connections, declared_generators, declared_storages = _plan_components(plan)
        current = self.teams.get(plan.team_id, TeamAssetState(team_id=plan.team_id))
        current_generators = declared_generators
        current_storages = declared_storages

        for site_id, site in _site_plans(plan).items():
            previous = current.sites.get(site_id)
            if previous is not None and not _same_site_connection(previous, site):
                raise AssetStateError(
                    f"site {site_id} conflicts with its persisted connection specification"
                )

        for generator in current_generators.values():
            connection = _connection_for_asset(connections, generator.connection_id)
            persisted = current.generators.canonical_get(
                _canonical_asset_key(plan.team_id, generator.connection_id, "generator")
            )
            candidate = _generator_asset(plan.team_id, generator, connection)
            if persisted is not None and not _same_generator_spec(persisted, candidate):
                raise AssetStateError(
                    f"generator {generator.generator_id} conflicts with its persisted specification"
                )

        for storage in current_storages.values():
            connection = _connection_for_asset(connections, storage.connection_id)
            persisted = current.storages.canonical_get(
                _canonical_asset_key(plan.team_id, storage.connection_id, "storage")
            )
            candidate = _storage_asset(plan.team_id, storage, connection)
            if persisted is not None and not _same_storage_spec(persisted, candidate):
                raise AssetStateError(
                    f"storage {storage.storage_id} conflicts with its persisted specification"
                )

        _validate_persisted_connection_specs(connections, current)

        materialized_connections = {
            connection_id: _expand_constant_connection(connection, horizon)
            for connection_id, connection in connections.items()
        }
        for site_id, site in current.sites.items():
            if site_id not in materialized_connections:
                materialized_connections[site_id] = ConnectionPlan(
                    connection_id=site.site_id,
                    bus_id=site.bus_id,
                    capacity_mw=site.connection_capacity_mw,
                    profile=(1.0,) * horizon,
                )
        for asset in (*current.generators.values(), *current.storages.values()):
            if asset.connection_id not in materialized_connections:
                materialized_connections[asset.connection_id] = ConnectionPlan(
                    connection_id=asset.connection_id,
                    bus_id=asset.bus_id,
                    capacity_mw=0.0,
                    profile=(0.0,) * horizon,
                )

        generators: dict[str, GeneratorPlan] = {}
        current_generator_keys: set[str] = set()
        for generator in current_generators.values():
            asset_key = _canonical_asset_key(plan.team_id, generator.connection_id, "generator")
            current_generator_keys.add(asset_key)
            generators[asset_key] = generator
        for asset in current.generators.canonical_values():
            if asset.asset_key not in current_generator_keys:
                generators[asset.asset_key] = GeneratorPlan(
                    generator_id=asset.generator_id,
                    connection_id=asset.connection_id,
                    capacity_mw=asset.capacity_mw,
                    marginal_cost_per_mwh=asset.marginal_cost_per_mwh,
                )

        storages: dict[str, StoragePlan] = {}
        current_storage_keys: set[str] = set()
        for storage in current_storages.values():
            asset_key = _canonical_asset_key(plan.team_id, storage.connection_id, "storage")
            current_storage_keys.add(asset_key)
            persisted = current.storages.canonical_get(asset_key)
            storages[asset_key] = (
                StoragePlan(
                    storage_id=storage.storage_id,
                    connection_id=storage.connection_id,
                    power_mw=storage.power_mw,
                    energy_mwh=storage.energy_mwh,
                    initial_state_of_charge_mwh=persisted.state_of_charge_mwh,
                )
                if persisted is not None
                else storage
            )
        for asset in current.storages.canonical_values():
            if asset.asset_key not in current_storage_keys:
                storages[asset.asset_key] = StoragePlan(
                    storage_id=asset.storage_id,
                    connection_id=asset.connection_id,
                    power_mw=asset.power_mw,
                    energy_mwh=asset.energy_mwh,
                    initial_state_of_charge_mwh=asset.state_of_charge_mwh,
                )

        generators = _canonicalize_generator_ids(generators)
        storages = _canonicalize_storage_ids(storages)

        return TeamPlan(
            team_id=plan.team_id,
            connections=tuple(materialized_connections[key] for key in sorted(materialized_connections)),
            generators=tuple(sorted(generators.values(), key=lambda item: item.generator_id)),
            storages=tuple(sorted(storages.values(), key=lambda item: item.storage_id)),
            sites=tuple(
                current.sites[site_id]
                for site_id in sorted(current.sites)
                if site_id not in {site.site_id for site in plan.sites}
            ) + tuple(sorted(plan.sites, key=lambda item: item.site_id)),
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
            storages = current.storages.canonical_dict()
            for storage_id, value in storage_values.items():
                try:
                    asset = current.storages[storage_id]
                except KeyError:
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
                storages[asset.asset_key] = StorageAsset(
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
                sites=current.sites,
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
                    "sites": {
                        site_id: asdict(site)
                        for site_id, site in sorted(state.sites.items())
                    },
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
        if not isinstance(payload, Mapping):
            raise AssetStateError("asset ledger must be an object")
        version = payload.get("version")
        if version not in (1, ASSET_LEDGER_SCHEMA_VERSION):
            raise AssetStateError(f"unsupported asset ledger version: {version!r}")
        teams: dict[str, TeamAssetState] = {}
        for team_id, raw_state in _mapping(payload.get("teams"), "teams").items():
            if not isinstance(team_id, str) or not team_id:
                raise AssetStateError("asset ledger team IDs must be non-empty strings")
            if not isinstance(raw_state, Mapping):
                raise AssetStateError(f"team asset state must be an object: {team_id}")
            state_team_id = raw_state.get("team_id", team_id)
            if state_team_id != team_id:
                raise AssetStateError(
                    f"team asset state identity mismatch: {team_id} vs {state_team_id}"
                )
            generators = _deserialize_assets(
                raw_state.get("generators"),
                team_id=team_id,
                kind="generator",
                version=version,
            )
            storages = _deserialize_assets(
                raw_state.get("storages"),
                team_id=team_id,
                kind="storage",
                version=version,
            )
            teams[team_id] = TeamAssetState(
                team_id=team_id,
                sites=_deserialize_sites(raw_state.get("sites", {}), team_id=team_id),
                generators=generators,
                storages=storages,
                last_round_key=raw_state.get("last_round_key"),
            )
        return cls(version=ASSET_LEDGER_SCHEMA_VERSION, teams=teams)


def _connection_for_asset(connections: Mapping[str, Any], connection_id: str) -> Any:
    try:
        return connections[connection_id]
    except KeyError:
        raise AssetStateError(
            f"asset references unknown connection site: {connection_id}"
        ) from None


def _site_plans(plan: TeamPlan) -> dict[str, SitePlan]:
    sites: dict[str, SitePlan] = {}
    for site in plan.sites:
        if site.site_id in sites:
            raise AssetStateError(f"duplicate site_id: {site.site_id}")
        sites[site.site_id] = site
    return sites


def _same_site_connection(left: SitePlan, right: SitePlan) -> bool:
    return (
        left.site_id,
        left.bus_id,
        left.connection_capacity_mw,
    ) == (
        right.site_id,
        right.bus_id,
        right.connection_capacity_mw,
    )


def _expand_constant_connection(connection: ConnectionPlan, horizon: int) -> ConnectionPlan:
    if connection.profile:
        return connection
    return ConnectionPlan(
        connection_id=connection.connection_id,
        bus_id=connection.bus_id,
        capacity_mw=connection.capacity_mw,
        profile=(1.0,) * horizon,
    )


def _plan_components(
    plan: TeamPlan,
) -> tuple[dict[str, ConnectionPlan], dict[str, GeneratorPlan], dict[str, StoragePlan]]:
    """Normalize legacy connections and site-only plans at the state boundary."""
    connections = _unique_connections(plan.connections)
    site_ids: set[str] = set()
    for site in plan.sites:
        if site.site_id in site_ids:
            raise AssetStateError(f"duplicate site_id: {site.site_id}")
        site_ids.add(site.site_id)
        site_connection = ConnectionPlan(
            connection_id=site.site_id,
            bus_id=site.bus_id,
            capacity_mw=site.connection_capacity_mw,
        )
        previous = connections.get(site.site_id)
        if previous is not None and (
            previous.bus_id != site_connection.bus_id
            or previous.capacity_mw != site_connection.capacity_mw
        ):
            raise AssetStateError(
                f"site {site.site_id} conflicts with its persisted connection specification"
            )
        if previous is None:
            connections[site.site_id] = site_connection

    generators = list(plan.generators)
    storages = list(plan.storages)
    generator_sites = {generator.connection_id for generator in generators}
    storage_sites = {storage.connection_id for storage in storages}
    for site in plan.sites:
        if site.site_id not in generator_sites and site.generator_capacity_mw != 0:
            generators.append(
                GeneratorPlan(
                    generator_id=_canonical_asset_key(plan.team_id, site.site_id, "generator"),
                    connection_id=site.site_id,
                    capacity_mw=site.generator_capacity_mw,
                    marginal_cost_per_mwh=0.0,
                )
            )
        if site.site_id not in storage_sites and (
            site.storage_power_mw != 0 or site.storage_energy_mwh != 0
        ):
            storages.append(
                StoragePlan(
                    storage_id=_canonical_asset_key(plan.team_id, site.site_id, "storage"),
                    connection_id=site.site_id,
                    power_mw=site.storage_power_mw,
                    energy_mwh=site.storage_energy_mwh,
                )
            )
    return (
        connections,
        _unique_assets(tuple(generators), kind="generator"),
        _unique_assets(tuple(storages), kind="storage"),
    )


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
        site_id = asset.connection_id
        if site_id in indexed:
            previous_id = getattr(indexed[site_id], field)
            if previous_id == asset_id:
                raise AssetStateError(f"duplicate {kind} asset: {asset_id}")
            raise AssetStateError(f"duplicate {kind} asset at connection site: {site_id}")
        indexed[site_id] = asset
    return indexed


def _canonicalize_generator_ids(
    generators: Mapping[str, GeneratorPlan],
) -> dict[str, GeneratorPlan]:
    label_counts: dict[str, int] = {}
    for generator in generators.values():
        label_counts[generator.generator_id] = label_counts.get(generator.generator_id, 0) + 1
    result = {}
    for asset_key, generator in generators.items():
        evaluator_id = (
            generator.generator_id
            if label_counts[generator.generator_id] == 1 and generator.generator_id not in generators
            else asset_key
        )
        result[asset_key] = GeneratorPlan(
            generator_id=evaluator_id,
            connection_id=generator.connection_id,
            capacity_mw=generator.capacity_mw,
            marginal_cost_per_mwh=generator.marginal_cost_per_mwh,
        )
    return result


def _canonicalize_storage_ids(
    storages: Mapping[str, StoragePlan],
) -> dict[str, StoragePlan]:
    label_counts: dict[str, int] = {}
    for storage in storages.values():
        label_counts[storage.storage_id] = label_counts.get(storage.storage_id, 0) + 1
    result = {}
    for asset_key, storage in storages.items():
        evaluator_id = (
            storage.storage_id
            if label_counts[storage.storage_id] == 1 and storage.storage_id not in storages
            else asset_key
        )
        result[asset_key] = StoragePlan(
            storage_id=evaluator_id,
            connection_id=storage.connection_id,
            power_mw=storage.power_mw,
            energy_mwh=storage.energy_mwh,
            initial_state_of_charge_mwh=storage.initial_state_of_charge_mwh,
        )
    return result


def _asset_label(asset: Any) -> str:
    return asset.generator_id if hasattr(asset, "generator_id") else asset.storage_id


def _same_generator_spec(left: GeneratorAsset, right: GeneratorAsset) -> bool:
    return (
        left.team_id,
        left.asset_key,
        left.connection_id,
        left.bus_id,
        left.capacity_mw,
        left.marginal_cost_per_mwh,
    ) == (
        right.team_id,
        right.asset_key,
        right.connection_id,
        right.bus_id,
        right.capacity_mw,
        right.marginal_cost_per_mwh,
    )


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
        asset_key=_canonical_asset_key(team_id, generator.connection_id, "generator"),
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
        asset_key=_canonical_asset_key(team_id, storage.connection_id, "storage"),
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
        left.asset_key,
        left.connection_id,
        left.bus_id,
        left.power_mw,
        left.energy_mwh,
    ) == (
        right.team_id,
        right.asset_key,
        right.connection_id,
        right.bus_id,
        right.power_mw,
        right.energy_mwh,
    )


def _validate_persisted_connection_specs(
    connections: Mapping[str, Any],
    state: TeamAssetState,
) -> None:
    for site in state.sites.values():
        connection = connections.get(site.site_id)
        if connection is not None and connection.bus_id != site.bus_id:
            raise AssetStateError(
                f"connection {site.site_id} conflicts with its persisted source bus"
            )
        if connection is not None and connection.capacity_mw != site.connection_capacity_mw:
            raise AssetStateError(
                f"connection {site.site_id} conflicts with its persisted connection specification"
            )
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


def _deserialize_sites(value: Any, *, team_id: str) -> dict[str, SitePlan]:
    raw_sites = _mapping(value, "sites")
    parsed: dict[str, SitePlan] = {}
    for serialized_id, raw_site in raw_sites.items():
        if not isinstance(serialized_id, str) or not serialized_id:
            raise AssetStateError("site keys must be non-empty strings")
        if not isinstance(raw_site, Mapping):
            raise AssetStateError(f"site must be an object: {serialized_id}")
        payload = dict(raw_site)
        payload.setdefault("site_id", serialized_id)
        if payload.get("site_id") != serialized_id:
            raise AssetStateError(
                f"site identity mismatch for team {team_id}: {serialized_id}"
            )
        try:
            site = SitePlan(**payload)
        except (TypeError, ValueError) as exc:
            raise AssetStateError(f"invalid site state for {team_id}/{serialized_id}: {exc}") from exc
        if site.site_id in parsed:
            raise AssetStateError(f"duplicate site identity: {site.site_id}")
        parsed[site.site_id] = site
    return parsed


def _deserialize_assets(
    value: Any,
    *,
    team_id: str,
    kind: str,
    version: int,
) -> dict[str, Any]:
    """Migrate v1 label/canonical keys and validate identities before storage."""
    raw_assets = _mapping(value, f"{kind}s")
    parsed: dict[str, Any] = {}
    for serialized_id, raw_asset in raw_assets.items():
        if not isinstance(serialized_id, str) or not serialized_id:
            raise AssetStateError(f"{kind} asset keys must be non-empty strings")
        payload = _asset_payload(raw_asset, kind, serialized_id)
        if payload.get("team_id") != team_id:
            raise AssetStateError(
                f"{kind} asset {serialized_id} has a mismatched team identity"
            )
        connection_id = payload.get("connection_id")
        canonical_id = canonical_asset_key(team_id, connection_id, kind)
        label = payload[f"{kind}_id"]
        if version == ASSET_LEDGER_SCHEMA_VERSION:
            if serialized_id != canonical_id:
                raise AssetStateError(
                    f"version {ASSET_LEDGER_SCHEMA_VERSION} requires canonical {kind} keys: "
                    f"{serialized_id}"
                )
        elif serialized_id != canonical_id and serialized_id != label:
            raise AssetStateError(
                f"version 1 {kind} key must be its submitted label or canonical identity: "
                f"{serialized_id}"
            )
        if canonical_id in parsed:
            raise AssetStateError(
                f"duplicate canonical {kind} identity: {canonical_id}"
            )
        parsed[canonical_id] = (
            GeneratorAsset(**payload)
            if kind == "generator"
            else StorageAsset(**payload)
        )
    return parsed


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
