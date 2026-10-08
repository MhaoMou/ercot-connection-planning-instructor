"""Canonical data structures for the ACTIVSg2000 dispatch game.

The production evaluator operates on source-artifact bus IDs. These records
contain only the fields needed to validate a student CSV, build joint dispatch,
and compute the score.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ConnectionPlan:
    """One team-owned data-center connection at a scenario source bus."""

    connection_id: str
    bus_id: str
    capacity_mw: float
    profile: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", tuple(self.profile))


@dataclass(frozen=True)
class GeneratorPlan:
    """One team-owned generator attached to a connection site."""

    generator_id: str
    connection_id: str
    capacity_mw: float
    marginal_cost_per_mwh: float


@dataclass(frozen=True)
class StoragePlan:
    """One team-owned storage asset attached to a connection site."""

    storage_id: str
    connection_id: str
    power_mw: float
    energy_mwh: float
    initial_state_of_charge_mwh: float | None = None


@dataclass(frozen=True)
class TeamPlan:
    """Complete canonical plan reconstructed from one student CSV."""

    team_id: str
    connections: tuple[ConnectionPlan, ...]
    generators: tuple[GeneratorPlan, ...] = ()
    storages: tuple[StoragePlan, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "connections", tuple(self.connections))
        object.__setattr__(self, "generators", tuple(self.generators))
        object.__setattr__(self, "storages", tuple(self.storages))
