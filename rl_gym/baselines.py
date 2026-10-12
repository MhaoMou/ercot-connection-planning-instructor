"""Deterministic and seeded baseline policies for the public planning API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

import numpy as np


class Policy(Protocol):
    """Callable policy interface shared by baseline and user policies."""

    def __call__(self, observation: Mapping[str, Any], info: Mapping[str, Any]) -> Mapping[str, Any]: ...


_ACTION_FIELDS = (
    "bus_index",
    "requested_capacity_mw",
    "generator_capacity_mw",
    "storage_power_mw",
    "storage_energy_mwh",
    "finish",
    "revise",
)


def _limit(info: Mapping[str, Any]) -> float:
    value = float(info.get("max_capacity_mw", 1.0))
    return value if np.isfinite(value) and value > 0 else 1.0


def _occupied(observation: Mapping[str, Any]) -> set[int]:
    plan = np.asarray(observation.get("own_plan", []))
    mask = np.asarray(observation.get("own_plan_mask", []), dtype=bool)
    return {int(round(row[0])) for row, present in zip(plan, mask) if present}


def _available(observation: Mapping[str, Any], info: Mapping[str, Any]) -> list[int]:
    catalog = np.asarray(observation.get("bus_catalog", []))
    occupied = _occupied(observation)
    max_sites = int(info.get("max_sites", len(catalog)))
    if len(occupied) >= max_sites:
        return []
    return [index for index in range(len(catalog)) if index not in occupied]


def _finish() -> dict[str, float | int]:
    return {
        "bus_index": 0,
        "requested_capacity_mw": 0.0,
        "generator_capacity_mw": 0.0,
        "storage_power_mw": 0.0,
        "storage_energy_mwh": 0.0,
        "finish": 1,
        "revise": 0,
    }


def _action(bus_index: int, requested: float, generator: float, power: float) -> dict[str, float | int]:
    requested = max(0.0, float(requested))
    generator = min(max(0.0, float(generator)), requested)
    power = max(0.0, float(power))
    return {
        "bus_index": int(bus_index),
        "requested_capacity_mw": requested,
        "generator_capacity_mw": generator,
        "storage_power_mw": power,
        "storage_energy_mwh": 4.0 * power,
        "finish": 0,
        "revise": 0,
    }


class RandomPolicy:
    """Seeded random policy that only adds currently available public sites."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = np.random.default_rng(seed)

    def __call__(self, observation: Mapping[str, Any], info: Mapping[str, Any]) -> Mapping[str, Any]:
        available = _available(observation, info)
        if not available:
            return _finish()
        maximum = _limit(info)
        requested = float(self._rng.uniform(maximum / 2.0, maximum))
        generator = float(self._rng.uniform(0.0, requested))
        power = float(self._rng.uniform(0.0, maximum / 4.0))
        return _action(
            int(self._rng.choice(available)),
            requested,
            generator,
            power,
        )


class GreedyPolicy:
    """Adds the lowest available bus at the largest public capacity."""

    def __call__(self, observation: Mapping[str, Any], info: Mapping[str, Any]) -> Mapping[str, Any]:
        available = _available(observation, info)
        if not available:
            return _finish()
        maximum = _limit(info)
        return _action(available[0], maximum, maximum, 0.0)


class RuleBasedPolicy:
    """Adds buses in catalog order with a fixed generator/storage rule."""

    def __call__(self, observation: Mapping[str, Any], info: Mapping[str, Any]) -> Mapping[str, Any]:
        available = _available(observation, info)
        if not available:
            return _finish()
        maximum = _limit(info)
        requested = maximum / 2.0
        power = maximum / 4.0
        return _action(available[0], requested, 0.0, power)


__all__ = ["Policy", "RandomPolicy", "GreedyPolicy", "RuleBasedPolicy"]
