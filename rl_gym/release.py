"""Deterministic release manifests for RL experiments."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any

from ercot_game.calendars import half_year_calendar


RELEASE_VERSION = "phase5-release-v1"
ENVIRONMENT_VERSION = "phase5-environment-v1"
PLAN_SCHEMA_VERSION = "phase5-plan-schema-v1"


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value deterministically as UTF-8 JSON text."""
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def stable_hash(value: Any) -> str:
    """Return the lowercase SHA-256 digest of a canonical JSON value."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    """Return a detached ordinary JSON value after strict validation."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("value is not valid JSON: non-finite float")
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("value is not valid JSON: mapping keys must be strings")
            result[key] = _json_value(item)
        return result
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    raise TypeError(f"value is not valid JSON: unsupported type {type(value).__name__}")


def _freeze_json(value: Any) -> Any:
    """Validate JSON metadata and recursively freeze mappings and lists."""
    value = _json_value(value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    """Return ordinary dict/list values from frozen JSON metadata."""
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validate_identity(
    release_version: str,
    environment_version: str,
    plan_schema_version: str,
    scenario_year: int,
    scenario_block: str,
) -> None:
    for field_name, field_value in (
        ("release_version", release_version),
        ("environment_version", environment_version),
        ("plan_schema_version", plan_schema_version),
    ):
        if not isinstance(field_value, str) or not field_value:
            raise ValueError(f"{field_name} must be a non-empty string")
    if isinstance(scenario_year, bool) or not isinstance(scenario_year, int):
        raise ValueError("scenario_year must be an integer")
    if not isinstance(scenario_block, str) or not scenario_block:
        raise ValueError("scenario_block must be a non-empty string")
    half_year_calendar(scenario_year, scenario_block)


@dataclass(frozen=True)
class ReleaseManifest:
    """Versioned, JSON-safe identity for a released RL experiment."""

    release_version: str
    environment_version: str
    plan_schema_version: str
    scenario_year: int
    scenario_block: str
    rules: Any
    config: Any

    def __post_init__(self) -> None:
        _validate_identity(
            self.release_version,
            self.environment_version,
            self.plan_schema_version,
            self.scenario_year,
            self.scenario_block,
        )
        object.__setattr__(self, "rules", _freeze_json(self.rules))
        object.__setattr__(self, "config", _freeze_json(self.config))

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_version": self.release_version,
            "environment_version": self.environment_version,
            "plan_schema_version": self.plan_schema_version,
            "scenario_year": self.scenario_year,
            "scenario_block": self.scenario_block,
            "rules": _thaw_json(self.rules),
            "config": _thaw_json(self.config),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReleaseManifest":
        if not isinstance(value, Mapping):
            raise ValueError("release manifest must be a mapping")
        required = (
            "release_version",
            "environment_version",
            "plan_schema_version",
            "scenario_year",
            "scenario_block",
            "rules",
            "config",
        )
        missing = [field for field in required if field not in value]
        if missing:
            raise ValueError(f"release manifest missing required field(s): {', '.join(missing)}")
        return make_release_manifest(
            release_version=value["release_version"],
            environment_version=value["environment_version"],
            plan_schema_version=value["plan_schema_version"],
            scenario_year=value["scenario_year"],
            scenario_block=value["scenario_block"],
            rules=value["rules"],
            config=value["config"],
        )

    def digest(self) -> str:
        return stable_hash(self.to_dict())


def make_release_manifest(
    *,
    scenario_year: int,
    scenario_block: str,
    rules: Any,
    config: Any,
    release_version: str = RELEASE_VERSION,
    environment_version: str = ENVIRONMENT_VERSION,
    plan_schema_version: str = PLAN_SCHEMA_VERSION,
) -> ReleaseManifest:
    """Create a validated release manifest for one supported half-year scenario."""
    _validate_identity(
        release_version,
        environment_version,
        plan_schema_version,
        scenario_year,
        scenario_block,
    )
    return ReleaseManifest(
        release_version=release_version,
        environment_version=environment_version,
        plan_schema_version=plan_schema_version,
        scenario_year=scenario_year,
        scenario_block=scenario_block,
        rules=rules,
        config=config,
    )


__all__ = [
    "RELEASE_VERSION",
    "ENVIRONMENT_VERSION",
    "PLAN_SCHEMA_VERSION",
    "canonical_json",
    "stable_hash",
    "ReleaseManifest",
    "make_release_manifest",
]
