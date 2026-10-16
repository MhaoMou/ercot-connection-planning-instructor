from __future__ import annotations

import json
from pathlib import Path

import pytest

from ercot_game.scenario_package import ScenarioPackageError, load_scenario_manifest


def _write_manifest(root: Path, *, validation_status: str = "passed") -> None:
    (root / "network").mkdir(parents=True, exist_ok=True)
    (root / "network" / "bus.csv").write_text("bus_id\n1\n", encoding="utf-8")
    (root / "profile_parameters.json").write_text("{}", encoding="utf-8")
    blocks = {
        block: {"horizon": horizon, "sha256": "block-hash"}
        for block, horizon in {
            "2027-jan_jun": 4344,
            "2027-jul_dec": 4416,
            "2028-jan_jun": 4368,
            "2028-jul_dec": 4416,
            "2029-jan_jun": 4344,
            "2029-jul_dec": 4416,
        }.items()
    }
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "package_version": "activsg2000-scenarios-v1",
                "validation_status": validation_status,
                "network": {"files": ["network/bus.csv"], "sha256": {"network/bus.csv": ""}},
                "blocks": blocks,
            }
        ),
        encoding="utf-8",
    )


def test_manifest_requires_passed_validation(tmp_path: Path) -> None:
    _write_manifest(tmp_path, validation_status="passed")
    assert load_scenario_manifest(tmp_path)["package_version"] == "activsg2000-scenarios-v1"

    _write_manifest(tmp_path, validation_status="failed")
    with pytest.raises(ScenarioPackageError, match="validation_status"):
        load_scenario_manifest(tmp_path)


def test_manifest_rejects_unknown_block_horizon(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    payload["blocks"]["2028-jan_jun"]["horizon"] = 6
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ScenarioPackageError, match="horizon"):
        load_scenario_manifest(tmp_path)


def test_manifest_accepts_six_hour_release_contract(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    payload["package_version"] = "activsg2000-scenarios-v2-6h"
    for block, entry in payload["blocks"].items():
        entry["horizon"] = entry["horizon"] // 6
        entry["time_step_hours"] = 6
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    assert load_scenario_manifest(tmp_path)["package_version"] == "activsg2000-scenarios-v2-6h"
