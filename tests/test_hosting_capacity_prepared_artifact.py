from __future__ import annotations

import csv
import inspect
import json
import pickle
from pathlib import Path

import pytest

from ercot_game.scenario_package import ScenarioPackageError
import tools.activsg2000.dcopf as dcopf
from tools.activsg2000.dcopf import (
    Bus,
    Branch,
    Generator,
    load_prepared_artifact,
    prepared_period_inputs,
)
from tools.hosting_capacity import (
    HostingCapacityPeriod,
    ieee14_period_provider,
    prepared_artifact_period_provider,
    static_period_provider,
)


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_small_prepared_artifact(root: Path, *, validation_status: str = "passed") -> Path:
    artifact = root / "prepared-block"
    artifact.mkdir()
    (artifact / "manifest.json").write_text(
        json.dumps(
            {
                "package_version": "activsg2000-scenarios-v1",
                "validation_status": validation_status,
                "block": "2028-jan_jun",
                "horizon": 2,
            }
        ),
        encoding="utf-8",
    )
    _write_csv(
        artifact / "bus.csv",
        ("bus_id", "type"),
        [{"bus_id": "b1", "type": "3"}, {"bus_id": "b2", "type": "1"}],
    )
    _write_csv(
        artifact / "demand_bus.csv",
        ("period", "b1", "b2"),
        [{"period": 0, "b1": 1.0, "b2": 5.0}, {"period": 1, "b1": 2.0, "b2": 7.0}],
    )
    _write_csv(
        artifact / "generator_limits.csv",
        ("generator_id", "bus_id", "p_min_mw", "p_max_mw", "marginal_cost_c1_per_mwh", "status"),
        [
            {
                "generator_id": "g1",
                "bus_id": "b1",
                "p_min_mw": 0.0,
                "p_max_mw": 20.0,
                "marginal_cost_c1_per_mwh": 10.0,
                "status": 1,
            }
        ],
    )
    _write_csv(
        artifact / "branch_limits.csv",
        ("branch_id", "from_bus_id", "to_bus_id", "reactance_pu", "thermal_limit_mva", "status"),
        [
            {
                "branch_id": "br1",
                "from_bus_id": "b1",
                "to_bus_id": "b2",
                "reactance_pu": 0.1,
                "thermal_limit_mva": 10.0,
                "status": 1,
            }
        ],
    )
    _write_csv(
        artifact / "dcline.csv",
        ("from_bus_id", "to_bus_id", "Pt", "status"),
        [{"from_bus_id": "b1", "to_bus_id": "b2", "Pt": 1.5, "status": 1}],
    )
    return artifact


def _tiny_period() -> HostingCapacityPeriod:
    return HostingCapacityPeriod(
        period_index=0,
        buses=(Bus("b1", 0.0, reference=True), Bus("b2", 5.0)),
        generators=(Generator("g1", "b1", 0.0, 20.0, 10.0),),
        branches=(Branch("br1", "b1", "b2", 10.0, 10.0),),
        fixed_injection_mw_by_bus={},
        source_metadata={"source": "test"},
    )


def test_prepared_artifact_provider_returns_normalized_periods(tmp_path: Path) -> None:
    artifact_dir = _write_small_prepared_artifact(tmp_path)

    provider = prepared_artifact_period_provider(artifact_dir)
    first = provider(0)
    second = provider(1)

    assert first.period_index == 0
    assert second.period_index == 1
    assert tuple(bus.bus_id for bus in first.buses) == ("b1", "b2")
    assert tuple(bus.bus_id for bus in second.buses) == ("b1", "b2")
    assert first.buses[1].demand_mw == 5.0
    assert second.buses[1].demand_mw == 7.0
    assert first.generators == second.generators
    assert first.branches == second.branches
    assert first.fixed_injection_mw_by_bus == {"b2": 1.5}
    assert first.source_metadata["period_index"] == 0
    assert first.source_metadata["artifact_path_identity"].startswith(f"{artifact_dir.name}:")
    assert first.source_metadata["scenario_year"] == 2028
    assert first.source_metadata["scenario_block"] == "jan_jun"
    assert str(artifact_dir) not in repr(first.source_metadata)


def test_public_prepared_period_wrapper_validates_before_conversion(tmp_path: Path) -> None:
    artifact_dir = _write_small_prepared_artifact(tmp_path, validation_status="pending")
    artifact = load_prepared_artifact(artifact_dir)

    assert "validate_artifact" not in inspect.signature(prepared_period_inputs).parameters
    with pytest.raises(ScenarioPackageError, match="validation_status"):
        prepared_period_inputs(artifact_dir, artifact, period_index=0)


def test_prepared_provider_validates_at_construction(tmp_path: Path) -> None:
    artifact_dir = _write_small_prepared_artifact(tmp_path, validation_status="pending")

    with pytest.raises(ScenarioPackageError, match="validation_status"):
        prepared_artifact_period_provider(artifact_dir)


def test_prepared_provider_does_not_use_eager_legacy_artifact_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_dir = _write_small_prepared_artifact(tmp_path)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("provider must not load eager legacy tables")

    monkeypatch.setattr(dcopf, "load_prepared_artifact", fail_if_called)
    monkeypatch.setattr(dcopf, "_read_renewable_profiles", fail_if_called)
    provider = prepared_artifact_period_provider(artifact_dir)

    period = provider(1)
    assert period.buses[1].demand_mw == 7.0


def test_prepared_provider_rejects_unknown_demand_keys_in_adapter_path(tmp_path: Path) -> None:
    artifact_dir = _write_small_prepared_artifact(tmp_path)
    _write_csv(
        artifact_dir / "demand_bus.csv",
        ("period", "b1", "b2", "unknown-bus"),
        [
            {"period": 0, "b1": 1.0, "b2": 5.0, "unknown-bus": 0.0},
            {"period": 1, "b1": 2.0, "b2": 7.0, "unknown-bus": 0.0},
        ],
    )

    provider = prepared_artifact_period_provider(artifact_dir)
    with pytest.raises(ValueError, match="unknown bus"):
        provider(0)


def test_prepared_provider_rejects_out_of_range_periods(tmp_path: Path) -> None:
    provider = prepared_artifact_period_provider(_write_small_prepared_artifact(tmp_path))

    with pytest.raises(ValueError, match="period_index.*out of range"):
        provider(2)


def test_prepared_provider_is_pickleable_without_serializing_period_data(tmp_path: Path) -> None:
    provider = prepared_artifact_period_provider(_write_small_prepared_artifact(tmp_path))

    restored = pickle.loads(pickle.dumps(provider))
    assert restored(1).buses == provider(1).buses
    assert restored(1).source_metadata == provider(1).source_metadata


def test_prepared_provider_rejects_unknown_artifact_data(tmp_path: Path) -> None:
    artifact_dir = _write_small_prepared_artifact(tmp_path)
    _write_csv(
        artifact_dir / "generator_limits.csv",
        ("generator_id", "bus_id", "p_min_mw", "p_max_mw", "marginal_cost_c1_per_mwh", "status"),
        [
            {
                "generator_id": "g1",
                "bus_id": "missing-bus",
                "p_min_mw": 0.0,
                "p_max_mw": 20.0,
                "marginal_cost_c1_per_mwh": 10.0,
                "status": 1,
            }
        ],
    )

    provider = prepared_artifact_period_provider(artifact_dir)
    with pytest.raises(ValueError, match="unknown bus"):
        provider(0)


def test_static_and_ieee14_providers_are_indexed_and_pickleable() -> None:
    period = _tiny_period()
    static = static_period_provider(period)
    ieee14 = ieee14_period_provider((period,))

    for provider in (static, ieee14):
        restored = pickle.loads(pickle.dumps(provider))
        assert restored(0).buses == provider(0).buses
        with pytest.raises(ValueError, match="period_index"):
            restored(1)


def test_ieee14_provider_requires_explicit_normalized_periods() -> None:
    with pytest.raises(ValueError, match="provide.*normalized IEEE-14 period"):
        ieee14_period_provider()
