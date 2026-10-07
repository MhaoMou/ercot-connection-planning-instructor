"""Deterministic hourly profiles for the ACTIVSg2000 teaching package."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any


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


def load_profile_inputs(package_root: str | Path) -> tuple[dict[str, float], dict[str, dict[str, Any]], list[dict[str, str]]]:
    root = Path(package_root)
    data_root = root / "network" if (root / "network").is_dir() else root
    buses = {row["bus_id"]: float(row["base_load_mw"]) for row in _read_csv(data_root / "bus_load_base.csv")}
    catalog = {row["generator_id"]: row for row in _read_csv(data_root / "generator_catalog.csv")}
    profile_path = root / "profile_parameters.json"
    if not profile_path.is_file():
        profile_path = data_root / "profile_parameters.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    profiles = {row["generator_id"]: row for row in payload["generators"]}
    return buses, profiles, list(catalog.values())


def hourly_state(package_root: str | Path, *, year: int, calendar_block: str, period_index: int) -> tuple[dict[str, float], dict[str, float]]:
    """Materialize one bus-demand and generator-availability state."""
    base_load, profiles, catalog = load_profile_inputs(package_root)
    horizon = 4368 if calendar_block == "jan_jun" and year % 4 == 0 else 4344 if calendar_block == "jan_jun" else 4416
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
