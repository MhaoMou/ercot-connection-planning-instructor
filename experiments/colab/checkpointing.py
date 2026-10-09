"""Checkpoint persistence helpers for the Colab DCOPF benchmark."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


INT_FIELDS = frozenset({
    "horizon_hours",
    "repeat_index",
    "worker_count",
    "bus_count",
    "generator_count",
    "branch_count",
    "network_variable_count",
    "variable_count",
    "constraint_count",
    "repeat_count_completed",
})
FLOAT_FIELDS = frozenset({
    "wall_time_s",
    "median_hourly_solve_time_s",
    "p95_hourly_solve_time_s",
    "peak_memory_mb",
    "process_rss_peak_mb",
    "python_peak_memory_mb",
    "process_rss_before_mb",
    "rss_sampling_interval_s",
    "median_wall_time_s",
    "p95_wall_time_s",
})
BOOL_FIELDS = frozenset({
    "storage_enabled",
    "verified",
})


def configuration_signature(configuration: Mapping[str, Any]) -> str:
    """Return a stable digest for a JSON-compatible benchmark configuration."""

    encoded = json.dumps(
        configuration,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _typed_value(field: str, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if field in INT_FIELDS:
        return None if stripped == "" else int(stripped)
    if field in FLOAT_FIELDS:
        return None if stripped == "" else float(stripped)
    if field in BOOL_FIELDS:
        if stripped == "":
            return None
        lowered = stripped.lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return value


def coerce_checkpoint_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Restore checkpoint scalar types after JSON or CSV deserialization."""

    coerced: dict[str, Any] = {}
    for field, value in row.items():
        try:
            coerced[field] = _typed_value(field, value)
        except (TypeError, ValueError):
            # Keep malformed recovery values visible to downstream validation.
            # In particular, recommendation() must reject rather than guess.
            coerced[field] = value
    return coerced


def _load_json(path: Path) -> list[dict[str, Any]] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return None
    return [coerce_checkpoint_row(row) for row in rows if isinstance(row, Mapping)]


def _load_csv(path: Path) -> list[dict[str, Any]] | None:
    if not path.is_file():
        return None
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return [
                coerce_checkpoint_row(row)
                for row in csv.DictReader(handle)
            ]
    except (OSError, csv.Error):
        return None


def load_checkpoint(json_path: Path, csv_path: Path) -> list[dict[str, Any]]:
    """Load JSON rows first, falling back to typed CSV rows when needed."""

    json_rows = _load_json(json_path)
    if json_rows is not None:
        return json_rows
    csv_rows = _load_csv(csv_path)
    return csv_rows if csv_rows is not None else []


def matching_checkpoint_rows(
    rows: Sequence[Mapping[str, Any]],
    active_signature: str,
    expected_keys: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Index only rows belonging to the active configuration and case matrix."""

    expected = set(expected_keys)
    return {
        str(row["case_key"]): dict(row)
        for row in rows
        if row.get("case_key") in expected
        and row.get("configuration_signature") == active_signature
    }
