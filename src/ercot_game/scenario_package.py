"""Validation contract for the production ACTIVSg2000 scenario package."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ScenarioPackageError(ValueError):
    """Raised when a scenario package is incomplete or unvalidated."""


EXPECTED_HORIZONS = {
    "2027-jan_jun": 4344,
    "2027-jul_dec": 4416,
    "2028-jan_jun": 4368,
    "2028-jul_dec": 4416,
    "2029-jan_jun": 4344,
    "2029-jul_dec": 4416,
}

AGGREGATED_TIME_STEP_HOURS = 6
AGGREGATED_EXPECTED_HORIZONS = {
    block: horizon // AGGREGATED_TIME_STEP_HOURS
    for block, horizon in EXPECTED_HORIZONS.items()
}
SUPPORTED_PACKAGE_VERSIONS = frozenset(
    {"activsg2000-scenarios-v1", "activsg2000-scenarios-v2-6h"}
)


def expected_horizons_for_manifest(payload: dict[str, Any]) -> dict[str, int]:
    """Return the horizon contract selected by a scenario manifest."""
    version = payload.get("package_version")
    if version == "activsg2000-scenarios-v1":
        return EXPECTED_HORIZONS
    if version == "activsg2000-scenarios-v2-6h":
        return AGGREGATED_EXPECTED_HORIZONS
    raise ScenarioPackageError("unsupported scenario package_version")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_scenario_manifest(root: str | Path, *, require_validated: bool = True) -> dict[str, Any]:
    package_root = Path(root)
    path = package_root / "manifest.json"
    if not path.is_file():
        raise ScenarioPackageError(f"scenario manifest is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioPackageError(f"cannot read scenario manifest: {exc}") from exc
    expected_horizons = expected_horizons_for_manifest(payload)
    if require_validated and payload.get("validation_status") != "passed":
        raise ScenarioPackageError("scenario manifest validation_status is not passed")
    blocks = payload.get("blocks")
    if not isinstance(blocks, dict) or set(blocks) != set(expected_horizons):
        raise ScenarioPackageError("scenario manifest must contain all six half-year blocks")
    for block, horizon in expected_horizons.items():
        entry = blocks.get(block)
        if not isinstance(entry, dict) or entry.get("horizon") != horizon:
            raise ScenarioPackageError(f"invalid horizon for scenario block {block}")
        if payload.get("package_version") == "activsg2000-scenarios-v2-6h" and entry.get("time_step_hours") != AGGREGATED_TIME_STEP_HOURS:
            raise ScenarioPackageError(f"invalid time_step_hours for scenario block {block}")
        if not isinstance(entry.get("sha256"), str) or not entry["sha256"]:
            raise ScenarioPackageError(f"missing hash for scenario block {block}")
    network = payload.get("network")
    if not isinstance(network, dict) or not network.get("files") or not isinstance(network.get("sha256"), dict):
        raise ScenarioPackageError("scenario manifest has no network file hashes")
    for relative in network["files"]:
        file_path = package_root / relative
        if not file_path.is_file():
            raise ScenarioPackageError(f"scenario network file is missing: {relative}")
        expected = network["sha256"].get(relative)
        if expected and expected != sha256_file(file_path):
            raise ScenarioPackageError(f"scenario network hash mismatch: {relative}")
    return payload


def require_validated_block(root: str | Path) -> dict[str, Any]:
    """Load a block artifact manifest and fail closed until it is validated."""
    path = Path(root) / "manifest.json"
    if not path.is_file():
        raise ScenarioPackageError(f"scenario block manifest is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioPackageError(f"cannot read scenario block manifest: {exc}") from exc
    if payload.get("package_version") not in SUPPORTED_PACKAGE_VERSIONS:
        raise ScenarioPackageError("unsupported scenario block package_version")
    if payload.get("validation_status") != "passed":
        raise ScenarioPackageError("scenario block validation_status is not passed")
    return payload
