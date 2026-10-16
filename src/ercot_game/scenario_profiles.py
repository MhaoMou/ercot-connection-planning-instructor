"""Deterministic operating-period profiles for the ACTIVSg2000 package."""

from __future__ import annotations

import csv
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from .calendars import half_year_calendar


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def block_offset(year: int, calendar_block: str) -> int:
    if calendar_block not in {"jan_jun", "jul_dec"}:
        raise ValueError("calendar_block must be jan_jun or jul_dec")
    return 0 if calendar_block == "jan_jun" else (8784 if year % 4 == 0 else 8760) // 2


def demand_multiplier(hour_index: int, *, year: int) -> float:
    """Return a bounded seasonal/daily/weekly load multiplier."""
    if hour_index < 0:
        raise ValueError("hour_index must be non-negative")
    day = hour_index // 24
    hour = hour_index % 24
    seasonal = 0.08 * math.sin(2.0 * math.pi * (day - 170) / (366 if year % 4 == 0 else 365))
    daily = 0.06 * math.sin(2.0 * math.pi * (hour - 7) / 24.0)
    weekly = 0.025 * math.cos(2.0 * math.pi * day / 7.0)
    return max(0.80, min(1.20, 1.0 + seasonal + daily + weekly))


def generator_availability(profile: dict[str, Any], hour_index: int, *, year: int) -> float:
    """Return the generator-specific availability fraction in [0, 1]."""
    if profile["profile_type"] == "thermal":
        return 1.0
    seed = int(profile["seed"])
    day = hour_index // 24
    hour = hour_index % 24
    phase = (seed % 360) * math.pi / 180.0
    if profile["profile_type"] == "solar":
        daylight = max(0.0, math.sin(math.pi * (hour - 6) / 12.0))
        seasonal = 0.75 + 0.25 * math.sin(2.0 * math.pi * (day - 80) / (366 if year % 4 == 0 else 365))
        return max(0.0, min(1.0, daylight * seasonal))
    weather = 0.55 + 0.25 * math.sin(2.0 * math.pi * day / 5.0 + phase) + 0.15 * math.cos(2.0 * math.pi * hour / 24.0 + phase)
    return max(0.05, min(1.0, weather))


@lru_cache(maxsize=8)
def _load_profile_inputs_cached(package_root: str) -> tuple[dict[str, float], dict[str, dict[str, Any]], tuple[dict[str, str], ...]]:
    root = Path(package_root)
    data_root = root / "network" if (root / "network").is_dir() else root
    buses = {row["bus_id"]: float(row["base_load_mw"]) for row in _read_csv(data_root / "bus_load_base.csv")}
    catalog = {row["generator_id"]: row for row in _read_csv(data_root / "generator_catalog.csv")}
    profile_path = root / "profile_parameters.json"
    if not profile_path.is_file():
        profile_path = data_root / "profile_parameters.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profiles = {row["generator_id"]: row for row in payload["generators"]}
    return buses, profiles, tuple(catalog.values())


def load_profile_inputs(package_root: str | Path) -> tuple[dict[str, float], dict[str, dict[str, Any]], tuple[dict[str, str], ...]]:
    """Load static profile inputs once per package root.

    Scenario evaluation asks for one state per hour.  The source tables are
    immutable during a run, so caching them avoids reparsing the same CSV and
    JSON files thousands of times without changing any generated values.
    """
    return _load_profile_inputs_cached(str(Path(package_root).resolve()))


def clear_profile_input_cache() -> None:
    """Clear cached scenario profile inputs after deliberate file replacement."""
    _load_profile_inputs_cached.cache_clear()
    _scenario_time_step_cached.cache_clear()
    _source_hourly_state_cached.cache_clear()
    _hourly_state_cached.cache_clear()


@lru_cache(maxsize=16384)
def _scenario_time_step_cached(package_root: str) -> int:
    profile_path = Path(package_root) / "scenario_profile.json"
    if not profile_path.is_file():
        return 1
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
        value = payload.get("time_step_hours", 1)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read scenario profile metadata: {exc}") from exc
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("scenario_profile.json time_step_hours must be a positive integer")
    aggregation_method = payload.get("aggregation_method", "mean")
    if aggregation_method != "mean":
        raise ValueError("scenario profile aggregation_method must be 'mean'")
    return value


@lru_cache(maxsize=16384)
def _source_hourly_state_cached(
    package_root: str,
    year: int,
    calendar_block: str,
    period_index: int,
) -> tuple[dict[str, float], dict[str, float]]:
    """Materialize one source hourly bus-demand and generator state."""
    base_load, profiles, catalog = load_profile_inputs(package_root)
    horizon = half_year_calendar(year, calendar_block).horizon
    if not 0 <= period_index < horizon:
        raise IndexError("period_index is outside the half-year block")
    absolute_hour = block_offset(year, calendar_block) + period_index
    multiplier = demand_multiplier(absolute_hour, year=year)
    demand = {bus_id: round(value * multiplier, 8) for bus_id, value in base_load.items()}
    availability = {
        row["generator_id"]: round(float(row["p_max_mw"]) * generator_availability(profiles[row["generator_id"]], absolute_hour, year=year), 8)
        for row in catalog
        if int(row["status"]) == 1
    }
    return demand, availability


@lru_cache(maxsize=16384)
def _hourly_state_cached(
    package_root: str,
    year: int,
    calendar_block: str,
    period_index: int,
) -> tuple[dict[str, float], dict[str, float]]:
    """Materialize one scenario period at the artifact's declared resolution."""

    time_step_hours = _scenario_time_step_cached(package_root)
    source_horizon = half_year_calendar(year, calendar_block).horizon
    aggregate_horizon = source_horizon // time_step_hours
    if source_horizon % time_step_hours:
        raise ValueError(
            "scenario time_step_hours must divide the half-year source horizon"
        )
    if not 0 <= period_index < aggregate_horizon:
        raise IndexError("period_index is outside the half-year block")
    source_indices = range(
        period_index * time_step_hours,
        (period_index + 1) * time_step_hours,
    )
    source_states = tuple(
        _source_hourly_state_cached(package_root, year, calendar_block, source_index)
        for source_index in source_indices
    )
    bus_ids = source_states[0][0]
    generator_ids = source_states[0][1]
    demand = {
        bus_id: round(
            sum(state[0][bus_id] for state in source_states) / time_step_hours,
            8,
        )
        for bus_id in bus_ids
    }
    availability = {
        generator_id: round(
            sum(state[1][generator_id] for state in source_states) / time_step_hours,
            8,
        )
        for generator_id in generator_ids
    }
    return demand, availability


def hourly_state(package_root: str | Path, *, year: int, calendar_block: str, period_index: int) -> tuple[dict[str, float], dict[str, float]]:
    """Materialize one deterministic state, reusing cached profile inputs."""
    return _hourly_state_cached(str(Path(package_root).resolve()), year, calendar_block, period_index)
