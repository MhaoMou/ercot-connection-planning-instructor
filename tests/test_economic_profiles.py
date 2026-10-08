from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest
import numpy as np

from ercot_game.economic_profiles import load_economic_profiles_artifact
from tools.activsg2000.build_economic_profiles import (
    DEFAULT_IT_VALUE_PER_MWH,
    IT_VALUE_PROFILE_VERSION,
    build_it_value_profile,
    it_value_metadata,
    write_it_value_profile,
)


def test_it_value_profile_has_exact_half_year_horizon_and_no_six_hour_cycle():
    for horizon in (4368, 4416):
        profile = build_it_value_profile(horizon)
        assert len(profile) == horizon
        assert profile == [DEFAULT_IT_VALUE_PER_MWH] * horizon
        assert len(profile) != 6
        assert DEFAULT_IT_VALUE_PER_MWH == 1500.0


def test_it_value_profile_is_deterministic_and_metadata_is_explicit():
    assert build_it_value_profile(10) == build_it_value_profile(10)
    metadata = it_value_metadata(4368)
    assert metadata["profile_version"] == IT_VALUE_PROFILE_VERSION
    assert metadata["method"] == "explicit constant full-horizon instructor profile"
    assert metadata["source_category"] == "INSTRUCTOR-CHOSEN"
    assert metadata["units"] == "USD/MWh of served computational load"
    assert metadata["profile_name"] == "AI-intensive"
    assert metadata["representation"] == "explicit full-horizon profile"


def test_it_value_profile_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="positive"):
        build_it_value_profile(0)
    with pytest.raises(ValueError, match="integer"):
        build_it_value_profile(4368.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite and positive"):
        build_it_value_profile(10, base_value_per_mwh=float("nan"))


def test_it_value_profile_artifact_round_trips(tmp_path: Path):
    path = tmp_path / "economic_profile.json"
    write_it_value_profile(path, 4416)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["it_value_per_mwh"]) == 4416
    assert payload["it_value_provenance"]["horizon_hours"] == 4416


def test_baseline_builder_writes_json_it_profile_next_to_lmp_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import tools.activsg2000.build_economic_profiles as builder

    artifact_dir = tmp_path / "scenario"
    artifact_dir.mkdir()
    (artifact_dir / "manifest.json").write_text(json.dumps({"validation_status": "passed", "horizon": 2}), encoding="utf-8")
    output_dir = tmp_path / "economic"

    class Result:
        balance_dual_by_bus = {"B1": 10.0}

    monkeypatch.setattr(builder, "_read_bus_ids", lambda _root: ["B1"])
    monkeypatch.setattr(builder, "solve_prepared_period", lambda *args, **kwargs: Result())
    monkeypatch.setattr(builder, "extract_lmp_by_bus", lambda _result, clip_negative: {"B1": 10.0})
    monkeypatch.setattr(builder, "validate_balance_dual_sign", lambda *args, **kwargs: {"sign": "positive"})

    metadata = builder.build_baseline_lmp_profile(artifact_dir, output_dir)
    profile_path = output_dir / "economic_profile.json"
    manifest_path = output_dir / "economic_profile_manifest.json"
    assert profile_path.is_file()
    assert (output_dir / "economic_profiles.npz").is_file()
    assert manifest_path.is_file()
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert profile["it_value_per_mwh"] == [DEFAULT_IT_VALUE_PER_MWH] * 2
    assert profile["it_value_provenance"]["horizon_hours"] == 2
    assert metadata["artifact_files"]["economic_profile_json"] == "economic_profile.json"
    assert manifest["economic_profile_json_sha256"] == hashlib.sha256(profile_path.read_bytes()).hexdigest()


def _write_profile_artifact(root: Path, *, horizon: int = 4) -> Path:
    root.mkdir()
    (root / "bus.csv").write_text("bus_id\nB1\nB2\n", encoding="utf-8")
    np.savez(root / "economic_profiles.npz", lmp_per_mwh=np.arange(horizon * 2, dtype=float).reshape(horizon, 2))
    digest = hashlib.sha256((root / "economic_profiles.npz").read_bytes()).hexdigest()
    (root / "economic_profile_manifest.json").write_text(
        json.dumps(
            {
                "profile_version": "lmp-v1",
                "horizon_hours": horizon,
                "bus_ids": ["B1", "B2"],
                "artifact_sha256": digest,
                "price_source": "test baseline",
            }
        ),
        encoding="utf-8",
    )
    (root / "economic_profile.json").write_text(
        json.dumps(
            {
                "it_value_per_mwh": [100.0] * horizon,
                "it_value_provenance": {"horizon_hours": horizon},
            }
        ),
        encoding="utf-8",
    )
    return root


def test_load_economic_profiles_artifact_validates_npz_manifest_and_it_vector(tmp_path: Path):
    profile = load_economic_profiles_artifact(_write_profile_artifact(tmp_path / "artifact"), expected_horizon=4)

    assert profile.horizon_hours == 4
    assert profile.price_for_bus("B2", 3) == 7.0
    assert profile.it_value_per_mwh == (100.0,) * 4


def test_load_economic_profiles_artifact_fails_closed_on_hash_mismatch(tmp_path: Path):
    artifact = _write_profile_artifact(tmp_path / "artifact")
    manifest_path = artifact / "economic_profile_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["artifact_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        load_economic_profiles_artifact(artifact, expected_horizon=4)


def test_load_economic_profiles_artifact_fails_closed_on_shape_mismatch(tmp_path: Path):
    artifact = _write_profile_artifact(tmp_path / "artifact", horizon=4)
    np.savez(artifact / "economic_profiles.npz", lmp_per_mwh=np.zeros((3, 2)))
    manifest = json.loads((artifact / "economic_profile_manifest.json").read_text(encoding="utf-8"))
    manifest["artifact_sha256"] = hashlib.sha256((artifact / "economic_profiles.npz").read_bytes()).hexdigest()
    (artifact / "economic_profile_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="shape"):
        load_economic_profiles_artifact(artifact, expected_horizon=4)
