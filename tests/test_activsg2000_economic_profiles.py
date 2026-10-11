from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("highspy")

from tools.activsg2000.build_economic_profiles import (
    build_baseline_lmp_profile,
    build_it_value_profile,
)
from tools.activsg2000.dcopf import (
    Branch,
    Bus,
    Generator,
    extract_lmp_by_bus,
    solve_dcopf,
    validate_balance_dual_sign,
)


def _three_bus_case() -> tuple[tuple[Bus, ...], tuple[Generator, ...], tuple[Branch, ...]]:
    return (
        (Bus("B1", 0.0, reference=True), Bus("B2", 10.0), Bus("B3", 0.0)),
        (Generator("G1", "B1", 0.0, 100.0, 10.0), Generator("G2", "B3", 0.0, 100.0, 20.0)),
        (Branch("L1", "B1", "B2", 1.0, 100.0), Branch("L2", "B2", "B3", 1.0, 100.0)),
    )


def test_balance_dual_sign_matches_objective_finite_difference() -> None:
    buses, generators, branches = _three_bus_case()
    base = solve_dcopf(buses=buses, generators=generators, branches=branches)
    epsilon = 1e-4
    perturbed = solve_dcopf(
        buses=tuple(Bus(bus.bus_id, bus.demand_mw + (epsilon if bus.bus_id == "B2" else 0.0), bus.reference) for bus in buses),
        generators=generators,
        branches=branches,
    )
    validation = validate_balance_dual_sign(base, perturbed, bus_id="B2", epsilon=epsilon)
    assert validation["sign"] == "positive"
    assert abs(validation["finite_difference"] - validation["raw_dual"]) < 1e-5
    assert extract_lmp_by_bus(base)["B2"] == pytest.approx(base.balance_dual_by_bus["B2"])


def test_lmp_builder_writes_deterministic_full_horizon_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = tmp_path / "block"
    artifact.mkdir()
    (artifact / "manifest.json").write_text('{"package_version":"activsg2000-scenarios-v1","validation_status":"passed","horizon":2}\n', encoding="utf-8")
    (artifact / "scenario_profile.json").write_text('{"year":2028,"calendar_block":"jan_jun"}\n', encoding="utf-8")
    (artifact / "bus.csv").write_text("bus_id,type\nB1,3\nB2,1\nB3,1\n", encoding="utf-8")
    buses, generators, branches = _three_bus_case()
    results = [
        solve_dcopf(buses=buses, generators=generators, branches=branches),
        solve_dcopf(
            buses=tuple(Bus(bus.bus_id, bus.demand_mw + (1e-2 if bus.bus_id == "B2" else 0.0), bus.reference) for bus in buses),
            generators=generators,
            branches=branches,
        ),
    ]
    def fake_solve(*_args, period_index=0, **kwargs):
        return results[1] if kwargs.get("additional_demand_mw_by_bus") else results[period_index]

    monkeypatch.setattr("tools.activsg2000.build_economic_profiles.solve_prepared_period", fake_solve)
    output_a = tmp_path / "economic_a"
    output_b = tmp_path / "economic_b"
    manifest_a = build_baseline_lmp_profile(artifact, output_a)
    manifest_b = build_baseline_lmp_profile(artifact, output_b)
    assert manifest_a["horizon_hours"] == 2
    assert manifest_a["bus_ids"] == ["B1", "B2", "B3"]
    assert manifest_a["artifact_sha256"] == manifest_b["artifact_sha256"]
    assert (output_a / "economic_profiles.npz").read_bytes() == (output_b / "economic_profiles.npz").read_bytes()
    import numpy as np

    payload = np.load(output_a / "economic_profiles.npz")
    assert payload["lmp_per_mwh"].shape == (2, 3)
    assert payload["raw_balance_dual"].shape == (2, 3)
    assert payload["lmp_per_mwh"].min() >= 0.0
    assert manifest_a["dual_sign_validation"]["sign"] == "positive"
    assert manifest_a["dual_sign_validation"]["epsilon"] == 1e-2
