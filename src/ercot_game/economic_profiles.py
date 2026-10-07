"""Immutable full-horizon economic profiles used by batch scoring."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping, Set
import hashlib
import json
import math
from pathlib import Path

import numpy as np


def _validate_vector(name: str, values: object, horizon: int, *, nonnegative: bool) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must contain exactly {horizon} values")
    try:
        normalized = tuple(float(value) for value in values)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{name} must contain exactly {horizon} values") from None
    if len(normalized) != horizon:
        raise ValueError(f"{name} must contain exactly {horizon} values")
    for index, value in enumerate(normalized):
        if not math.isfinite(value):
            raise ValueError(f"{name}[{index}] must be finite")
        if nonnegative and value < 0:
            raise ValueError(f"{name}[{index}] must be non-negative")
    return normalized


@dataclass(frozen=True)
class HourlyEconomicProfiles:
    """Complete, immutable economic vectors for one evaluation block."""

    energy_price_per_mwh_by_bus: Mapping[str, tuple[float, ...]]
    it_value_per_mwh: tuple[float, ...]
    horizon_hours: int
    currency_year: str
    price_source: str
    profile_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.horizon_hours, int) or isinstance(self.horizon_hours, bool) or self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be a positive integer")
        normalized_prices: dict[str, tuple[float, ...]] = {}
        for bus_id, values in self.energy_price_per_mwh_by_bus.items():
            if not isinstance(bus_id, str) or not bus_id:
                raise ValueError("energy price profile bus IDs must be non-empty strings")
            normalized_prices[bus_id] = _validate_vector(
                f"energy_price_per_mwh_by_bus[{bus_id!r}]",
                values,
                self.horizon_hours,
                nonnegative=True,
            )
        if not normalized_prices:
            raise ValueError("energy_price_per_mwh_by_bus must not be empty")
        object.__setattr__(self, "energy_price_per_mwh_by_bus", MappingProxyType(normalized_prices))
        object.__setattr__(
            self,
            "it_value_per_mwh",
            _validate_vector("it_value_per_mwh", self.it_value_per_mwh, self.horizon_hours, nonnegative=True),
        )
        for field_name in ("currency_year", "price_source", "profile_version"):
            if not isinstance(getattr(self, field_name), str) or not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must be a non-empty string")

    @property
    def bus_ids(self) -> frozenset[str]:
        return frozenset(self.energy_price_per_mwh_by_bus)

    def price_for_bus(self, bus_id: str, period_index: int) -> float:
        if bus_id not in self.energy_price_per_mwh_by_bus:
            raise ValueError(f"economic profile has no price for bus {bus_id!r}")
        if not isinstance(period_index, int) or isinstance(period_index, bool):
            raise ValueError("period_index must be an integer")
        if period_index < 0 or period_index >= self.horizon_hours:
            raise ValueError(f"period_index must be in [0, {self.horizon_hours})")
        return self.energy_price_per_mwh_by_bus[bus_id][period_index]

    def it_value_for(self, period_index: int) -> float:
        if not isinstance(period_index, int) or isinstance(period_index, bool):
            raise ValueError("period_index must be an integer")
        if period_index < 0 or period_index >= self.horizon_hours:
            raise ValueError(f"period_index must be in [0, {self.horizon_hours})")
        return self.it_value_per_mwh[period_index]


def load_economic_profiles(
    payload: Mapping[str, object],
    *,
    bus_ids: Set[str] | frozenset[str] | None = None,
) -> HourlyEconomicProfiles:
    """Load and validate a complete profile payload.

    A legacy global ``energy_price_per_mwh`` vector is deliberately rejected;
    prices must be locational and must cover the complete block horizon.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("economic profiles must be a JSON object")
    try:
        horizon = int(payload["horizon_hours"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("economic profiles require horizon_hours") from None
    if "energy_price_per_mwh_by_bus" not in payload:
        if "energy_price_per_mwh" in payload:
            raise ValueError(f"energy_price_per_mwh must contain exactly {horizon} values")
        raise ValueError("economic profiles require energy_price_per_mwh_by_bus")
    raw_prices = payload["energy_price_per_mwh_by_bus"]
    if not isinstance(raw_prices, Mapping):
        raise ValueError("energy_price_per_mwh_by_bus must be an object")
    profile = HourlyEconomicProfiles(
        energy_price_per_mwh_by_bus=raw_prices,  # type: ignore[arg-type]
        it_value_per_mwh=payload.get("it_value_per_mwh", ()),  # type: ignore[arg-type]
        horizon_hours=horizon,
        currency_year=str(payload.get("currency_year", "")),
        price_source=str(payload.get("price_source", "")),
        profile_version=str(payload.get("profile_version", "")),
    )
    if bus_ids is not None:
        expected = frozenset(bus_ids)
        unknown = profile.bus_ids - expected
        missing = expected - profile.bus_ids
        if unknown or missing:
            details = []
            if unknown:
                details.append(f"unknown bus IDs: {sorted(unknown)!r}")
            if missing:
                details.append(f"missing bus IDs: {sorted(missing)!r}")
            raise ValueError("economic profile bus set mismatch (" + "; ".join(details) + ")")
    return profile


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bus_catalog(artifact_dir: Path) -> tuple[str, ...]:
    import csv

    path = artifact_dir / "bus.csv"
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise ValueError(f"bus catalog is missing: {path}") from exc
    bus_ids = tuple(str(row["bus_id"]) for row in rows)
    if not bus_ids or len(set(bus_ids)) != len(bus_ids):
        raise ValueError("bus catalog must contain a non-empty unique bus_id sequence")
    return bus_ids


def load_economic_profiles_artifact(
    artifact_dir: str | Path,
    *,
    expected_horizon: int | None = None,
) -> HourlyEconomicProfiles:
    """Load the validated hourly LMP and IT-value artifacts for one block.

    The required production layout is::

        economic_profiles.npz
        economic_profile_manifest.json
        economic_profile.json

    The NPZ must contain ``lmp_per_mwh`` with shape ``[hour, bus]``. The
    manifest's ordered bus list and SHA-256 digest are checked before values
    are exposed to scoring.
    """
    root = Path(artifact_dir)
    manifest_path = root / "economic_profile_manifest.json"
    npz_path = root / "economic_profiles.npz"
    it_path = root / "economic_profile.json"
    for path, label in ((manifest_path, "economic profile manifest"), (npz_path, "economic profile NPZ"), (it_path, "IT-value profile")):
        if not path.is_file():
            raise ValueError(f"{label} is missing: {path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        it_payload = json.loads(it_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not load economic profile metadata: {exc}") from exc
    if not isinstance(manifest, Mapping):
        raise ValueError("economic profile manifest must be a JSON object")
    if not isinstance(it_payload, Mapping):
        raise ValueError("IT-value profile must be a JSON object")
    expected_hash = manifest.get("artifact_sha256")
    actual_hash = _sha256_file(npz_path)
    if not isinstance(expected_hash, str) or expected_hash != actual_hash:
        raise ValueError("economic profile NPZ hash mismatch")
    try:
        horizon = int(manifest["horizon_hours"])
        manifest_bus_ids = tuple(str(bus_id) for bus_id in manifest["bus_ids"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("economic profile manifest requires horizon_hours and bus_ids") from None
    if expected_horizon is not None and horizon != expected_horizon:
        raise ValueError("economic profile horizon must match the evaluation horizon")
    catalog_bus_ids = _read_bus_catalog(root)
    if manifest_bus_ids != catalog_bus_ids:
        raise ValueError("economic profile bus IDs do not match the bus catalog")
    if len(set(manifest_bus_ids)) != len(manifest_bus_ids) or not manifest_bus_ids:
        raise ValueError("economic profile manifest bus_ids must be non-empty and unique")
    try:
        with np.load(npz_path, allow_pickle=False) as archive:
            lmp = np.asarray(archive["lmp_per_mwh"], dtype=float)
    except (OSError, KeyError, ValueError) as exc:
        raise ValueError(f"could not load lmp_per_mwh from economic profile NPZ: {exc}") from exc
    expected_shape = (horizon, len(manifest_bus_ids))
    if lmp.shape != expected_shape:
        raise ValueError(f"lmp_per_mwh must have shape {expected_shape}, got {lmp.shape}")
    if not np.isfinite(lmp).all() or (lmp < 0).any():
        raise ValueError("economic profile LMPs must be finite and non-negative")
    raw_it = it_payload.get("it_value_per_mwh")
    it_provenance = it_payload.get("it_value_provenance", {})
    if not isinstance(it_provenance, Mapping):
        raise ValueError("IT-value provenance must be a JSON object")
    if it_provenance.get("horizon_hours") != horizon:
        raise ValueError("IT-value profile horizon must match the economic profile horizon")
    prices = {bus_id: tuple(float(value) for value in lmp[:, index]) for index, bus_id in enumerate(manifest_bus_ids)}
    profile = HourlyEconomicProfiles(
        energy_price_per_mwh_by_bus=prices,
        it_value_per_mwh=raw_it or (),
        horizon_hours=horizon,
        currency_year=str(manifest.get("currency_year", "instructional")),
        price_source=str(manifest.get("price_source", "")),
        profile_version=str(manifest.get("profile_version", "")),
    )
    if profile.profile_version.strip() == "":
        raise ValueError("economic profile manifest requires profile_version")
    return profile
