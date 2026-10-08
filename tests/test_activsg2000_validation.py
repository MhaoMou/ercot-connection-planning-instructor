from __future__ import annotations

import json
from pathlib import Path

import pytest

from ercot_game.scenario_package import ScenarioPackageError, require_validated_block
from tools.activsg2000.build_scenario_package import build_package
from tools.activsg2000 import validate_scenario_package
from tools.activsg2000.validate_scenario_package import validate_package


EXPECTED_BLOCKS = {
    "2027-jan_jun": 4344,
    "2027-jul_dec": 4416,
    "2028-jan_jun": 4368,
    "2028-jul_dec": 4416,
    "2029-jan_jun": 4344,
    "2029-jul_dec": 4416,
}


def _minimal_package(root: Path) -> Path:
    package = root / "package"
    (package / "network").mkdir(parents=True)
    (package / "network" / "placeholder.csv").write_text("placeholder\n", encoding="utf-8")
    for block, horizon in EXPECTED_BLOCKS.items():
        year, calendar_block = block.split("-", 1)
        block_dir = package / year / calendar_block
        block_dir.mkdir(parents=True)
        (block_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "package_version": "activsg2000-scenarios-v1",
                    "validation_status": "pending",
                    "block": block,
                    "horizon": horizon,
                }
            ),
            encoding="utf-8",
        )
    (package / "manifest.json").write_text(
        json.dumps(
            {
                "package_version": "activsg2000-scenarios-v1",
                "validation_status": "pending",
                "network": {"files": [], "sha256": {}},
                "blocks": {
                    block: {
                        "year": int(block.split("-", 1)[0]),
                        "calendar_block": block.split("-", 1)[1],
                        "horizon": horizon,
                    }
                    for block, horizon in EXPECTED_BLOCKS.items()
                },
            }
        ),
        encoding="utf-8",
    )
    return package


def _raise_missing_highspy(*args, **kwargs):
    raise RuntimeError(
        "DCOPF requires the optional project dependency 'highspy'; install the project dependencies first"
    )


def _patch_minimal_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(validate_scenario_package, "_network", lambda package: ((), (), ()))
    monkeypatch.setattr(validate_scenario_package, "load_profile_inputs", lambda package: None)
    monkeypatch.setattr(
        validate_scenario_package,
        "hourly_state",
        lambda package, *, year, calendar_block, period_index: ({}, {}),
    )
    monkeypatch.setattr(validate_scenario_package, "solve_dcopf", _raise_missing_highspy)


def test_validator_does_not_mark_partial_validation_as_passed(tmp_path: Path) -> None:
    pytest.importorskip("highspy")
    source = Path("/private/tmp/activsg2000_source/ACTIVSg2000-master")
    if not source.exists():
        pytest.skip("canonical ACTIVSg2000 source is not available")
    package = tmp_path / "package"
    build_package(source, package)
    result = validate_package(package, max_hours=1)
    assert result["validation_status"] == "partial"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["validation_status"] == "partial"
    assert manifest["validation"]["hour_count"] == 1


def test_missing_highspy_is_unverified_dependency_and_rejects_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = _minimal_package(tmp_path)
    _patch_minimal_validation(monkeypatch)

    result = validate_package(package, max_hours=1)

    assert result["validation_status"] == "unverified_dependency"
    assert result["validation"]["classification"] == "dependency_limited"
    assert result["validation"]["failed_hour_count"] == 0
    assert result["validation"]["unverified_hour_count"] == 1
    assert "highspy" in result["validation"]["reason"]
    block_manifest_path = package / "2027" / "jan_jun" / "manifest.json"
    block_manifest = json.loads(block_manifest_path.read_text(encoding="utf-8"))
    assert block_manifest["validation_status"] == "unverified_dependency"
    assert "highspy" in block_manifest["validation_reason"]
    with pytest.raises(ScenarioPackageError, match="validation_status"):
        require_validated_block(block_manifest_path.parent)


def test_missing_highspy_marks_every_block_unverified_when_full_validation_is_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _minimal_package(tmp_path)
    _patch_minimal_validation(monkeypatch)

    result = validate_package(package)

    assert result["validation_status"] == "unverified_dependency"
    assert result["validation"]["unverified_hour_count"] == len(EXPECTED_BLOCKS)
    for block in EXPECTED_BLOCKS:
        year, calendar_block = block.split("-", 1)
        manifest = json.loads(
            (package / year / calendar_block / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["validation_status"] == "unverified_dependency"
        assert "highspy" in manifest["validation_reason"]


def test_non_dependency_runtime_error_remains_a_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _minimal_package(tmp_path)
    monkeypatch.setattr(validate_scenario_package, "_network", lambda package: ((), (), ()))
    monkeypatch.setattr(validate_scenario_package, "load_profile_inputs", lambda package: None)
    monkeypatch.setattr(
        validate_scenario_package,
        "hourly_state",
        lambda package, *, year, calendar_block, period_index: ({}, {}),
    )
    monkeypatch.setattr(
        validate_scenario_package,
        "solve_dcopf",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("solver crashed")),
    )

    result = validate_scenario_package._validate_block(package, "2027-jan_jun", 1)

    assert result["status"] == "failed"
    assert result["failed"] == 1
    assert result["unverified"] == []
