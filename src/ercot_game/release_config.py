"""Canonical student-game release configuration."""

from __future__ import annotations

from pathlib import Path


CANONICAL_TIME_STEP_HOURS = 6
CANONICAL_STORAGE_BOUNDARY_MODE = "daily_exact_cycle"
CANONICAL_SCENARIO_PACKAGE = "data/processed/activsg2000_scenarios_v2_6h"


def repository_root() -> Path:
    """Return the source checkout root when running from the installed package."""

    return Path(__file__).resolve().parents[2]


def canonical_artifact_dir(
    year: int,
    round_name: str,
    *,
    root: str | Path | None = None,
) -> Path:
    if round_name not in {"jan_jun", "jul_dec"}:
        raise ValueError("round_name must be 'jan_jun' or 'jul_dec'")
    base = repository_root() if root is None else Path(root)
    return base / CANONICAL_SCENARIO_PACKAGE / str(year) / round_name


def canonical_rules_path(
    round_name: str,
    *,
    root: str | Path | None = None,
) -> Path:
    if round_name not in {"jan_jun", "jul_dec"}:
        raise ValueError("round_name must be 'jan_jun' or 'jul_dec'")
    base = repository_root() if root is None else Path(root)
    return base / "data" / "teaching" / f"dispatch_scoring_rules_{round_name}_6h_v1.json"
