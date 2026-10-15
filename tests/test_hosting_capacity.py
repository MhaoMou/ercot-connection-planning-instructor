from dataclasses import FrozenInstanceError
import importlib.util
import inspect
import json
import math

import numpy as np

import pytest

import tools.activsg2000.dcopf as dcopf
import tools.hosting_capacity as hosting_capacity
from tools.activsg2000.dcopf import (
    Branch,
    Bus,
    DCOPFInfeasible,
    Generator,
    ReusableDCOPFModel,
)
from tools.hosting_capacity import (
    HOSTING_CAPACITY_STATUSES,
    compute_hosting_capacity_matrix,
    HostingCapacityPeriod,
    HostingCapacityPoint,
    solve_hosting_capacity_at_bus,
    validate_period,
)


def _tiny_period(**overrides):
    values = {
        "period_index": 0,
        "buses": (
            Bus("b1", 0.0, reference=True),
            Bus("b2", 5.0),
        ),
        "generators": (Generator("g1", "b1", 0.0, 20.0, 10.0),),
        "branches": (Branch("br1", "b1", "b2", 1.0, 100.0),),
        "fixed_injection_mw_by_bus": {},
        "source_metadata": {"source": "test"},
    }
    values.update(overrides)
    return HostingCapacityPeriod(**values)


class _TwoPeriodProvider:
    def __init__(self):
        self.periods = (
            _tiny_period(period_index=0),
            _tiny_period(
                period_index=1,
                buses=(Bus("b1", 1.0, reference=True), Bus("b2", 6.0)),
            ),
        )

    def __call__(self, period_index):
        return self.periods[period_index]


def _fake_matrix_result(period, bus_ids, *, values=None, statuses=None):
    values = values or [float(period.period_index + index) for index, _ in enumerate(bus_ids)]
    statuses = statuses or ["optimal"] * len(bus_ids)
    return {
        "capacity_mw": np.asarray(values, dtype=float),
        "status": np.asarray(statuses, dtype=object),
        "objective_value": np.asarray(values, dtype=float),
        "solve_time_s": np.ones(len(bus_ids), dtype=float),
    }


def test_matrix_driver_returns_sorted_arrays_and_writes_outputs(tmp_path, monkeypatch):
    def fake_period_solver(period, bus_ids, *, solver_backend, solver_threads):
        return {
            "capacity_mw": np.asarray([float(period.period_index + i) for i, _ in enumerate(bus_ids)]),
            "status": np.asarray(["optimal"] * len(bus_ids), dtype=object),
            "objective_value": np.asarray([float(period.period_index + i) for i, _ in enumerate(bus_ids)]),
            "solve_time_s": np.asarray([0.1] * len(bus_ids)),
        }

    monkeypatch.setattr("tools.hosting_capacity._solve_matrix_period", fake_period_solver)

    result = compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[1, 0, 1],
        bus_ids=["b2", "b1", "b2"],
        solver_backend="highs",
        checkpoint_dir=tmp_path / "checkpoints",
        output_dir=tmp_path / "outputs",
    )

    assert result.period_indices == (0, 1)
    assert result.bus_ids == ("b1", "b2")
    assert result.capacity_mw.shape == (2, 2)
    assert result.capacity_mw.tolist() == [[0.0, 1.0], [1.0, 2.0]]
    assert (tmp_path / "outputs" / "hosting_capacity_mw.npy").exists()
    assert (tmp_path / "outputs" / "hosting_capacity_status.npy").exists()
    assert (tmp_path / "outputs" / "hosting_capacity_metadata.json").exists()
    assert (tmp_path / "outputs" / "hosting_capacity_summary.csv").exists()
    assert result.status.dtype.kind == "U"
    saved_status = np.load(tmp_path / "outputs" / "hosting_capacity_status.npy", allow_pickle=False)
    assert saved_status.dtype.kind == "U"


def test_matrix_driver_resumes_completed_period_without_solving_it(tmp_path, monkeypatch):
    calls = []

    def fake_period_solver(period, bus_ids, *, solver_backend, solver_threads):
        calls.append(period.period_index)
        return _fake_matrix_result(period, bus_ids)

    monkeypatch.setattr("tools.hosting_capacity._solve_matrix_period", fake_period_solver)
    provider = _TwoPeriodProvider()
    checkpoint_dir = tmp_path / "checkpoints"
    compute_hosting_capacity_matrix(
        provider,
        period_indices=[0, 1],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        checkpoint_dir=checkpoint_dir,
    )
    assert calls == [0, 1]

    calls.clear()
    resumed = compute_hosting_capacity_matrix(
        provider,
        period_indices=[1, 0, 1],
        bus_ids=["b2", "b1"],
        solver_backend="highs",
        checkpoint_dir=checkpoint_dir,
    )
    assert calls == []
    assert resumed.capacity_mw.tolist() == [[0.0, 1.0], [1.0, 2.0]]


def test_matrix_driver_keeps_prior_checkpoint_when_later_period_raises(tmp_path, monkeypatch):
    def failing_period_solver(period, bus_ids, *, solver_backend, solver_threads):
        if period.period_index == 1:
            raise RuntimeError("later period failed")
        return _fake_matrix_result(period, bus_ids)

    monkeypatch.setattr("tools.hosting_capacity._solve_matrix_period", failing_period_solver)
    checkpoint_dir = tmp_path / "checkpoints"
    with pytest.raises(RuntimeError, match="later period failed"):
        compute_hosting_capacity_matrix(
            _TwoPeriodProvider(),
            period_indices=[0, 1],
            bus_ids=["b1", "b2"],
            solver_backend="highs",
            checkpoint_dir=checkpoint_dir,
        )
    assert (checkpoint_dir / "period_0.npz").exists()
    assert not (checkpoint_dir / "period_1.npz").exists()


def test_matrix_driver_rejects_checkpoint_identity_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.hosting_capacity._solve_matrix_period",
        lambda period, bus_ids, *, solver_backend, solver_threads: _fake_matrix_result(period, bus_ids),
    )
    provider = _TwoPeriodProvider()
    checkpoint_dir = tmp_path / "checkpoints"
    compute_hosting_capacity_matrix(
        provider,
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        checkpoint_dir=checkpoint_dir,
    )

    with pytest.raises(ValueError, match="checkpoint identity mismatch"):
        compute_hosting_capacity_matrix(
            provider,
            period_indices=[0],
            bus_ids=["b1"],
            solver_backend="highs",
            checkpoint_dir=checkpoint_dir,
        )


def test_matrix_driver_preserves_nonoptimal_status_and_nan_values(monkeypatch):
    def fake_period_solver(period, bus_ids, *, solver_backend, solver_threads):
        return _fake_matrix_result(
            period,
            bus_ids,
            values=[3.0, 99.0],
            statuses=["optimal", "infeasible_base_case"],
        )

    monkeypatch.setattr("tools.hosting_capacity._solve_matrix_period", fake_period_solver)
    result = compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
    )

    assert result.status.tolist() == [["optimal", "infeasible_base_case"]]
    assert result.capacity_mw[0, 0] == pytest.approx(3.0)
    assert np.isnan(result.capacity_mw[0, 1])
    assert np.isnan(result.objective_value[0, 1])


def test_matrix_driver_uses_one_reusable_model_for_all_candidate_buses(monkeypatch):
    model_calls = []
    probe_calls = []

    class FakeModel:
        def __init__(self, **kwargs):
            model_calls.append(kwargs)

        def max_additional_demand(self, buses, bus_id, *, fixed_injection_mw_by_bus):
            probe_calls.append(bus_id)
            return 4.0, "optimal"

    monkeypatch.setattr(dcopf, "_reusable_dcopf_model", lambda **kwargs: FakeModel(**kwargs))
    result = compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0],
        bus_ids=["b2", "b1"],
        solver_backend="highs",
    )

    assert len(model_calls) == 1
    assert model_calls[0]["enable_candidate_demand_probes"] is True
    assert model_calls[0]["candidate_demand_bus_id"] is None
    assert probe_calls == ["b1", "b2"]
    assert result.capacity_mw.tolist() == [[4.0, 4.0]]


def test_matrix_driver_rejects_invalid_parallel_settings_and_topology(monkeypatch):
    provider = _TwoPeriodProvider()
    monkeypatch.setattr(
        "tools.hosting_capacity._solve_matrix_period",
        lambda period, bus_ids, *, solver_backend, solver_threads: _fake_matrix_result(period, bus_ids),
    )
    with pytest.raises(ValueError, match="workers must be at least 1"):
        compute_hosting_capacity_matrix(provider, period_indices=[0], workers=0)
    with pytest.raises(ValueError, match="solver_threads must be at least 1"):
        compute_hosting_capacity_matrix(provider, period_indices=[0], solver_threads=0)
    with pytest.raises(ValueError, match="workers > 1"):
        compute_hosting_capacity_matrix(provider, period_indices=[0], workers=2, solver_threads=2)

    mismatched = _TwoPeriodProvider()
    mismatched.periods = (
        mismatched.periods[0],
        _tiny_period(
            period_index=1,
            buses=(Bus("b2", 6.0, reference=True), Bus("b1", 1.0)),
        ),
    )
    with pytest.raises(ValueError, match="different bus order"):
        compute_hosting_capacity_matrix(mismatched, period_indices=[0, 1])


def test_matrix_driver_accepts_period_varying_generator_and_branch_limits(monkeypatch):
    provider = _TwoPeriodProvider()
    provider.periods = (
        provider.periods[0],
        _tiny_period(
            period_index=1,
            buses=(Bus("b1", 1.0, reference=True), Bus("b2", 6.0)),
            generators=(Generator("g1", "b1", 1.0, 30.0, 11.0),),
            branches=(Branch("br1", "b1", "b2", 1.0, 80.0),),
        ),
    )
    monkeypatch.setattr(
        "tools.hosting_capacity._solve_matrix_period",
        lambda period, bus_ids, *, solver_backend, solver_threads: _fake_matrix_result(period, bus_ids),
    )

    result = compute_hosting_capacity_matrix(provider, period_indices=[0, 1])

    assert result.capacity_mw.shape == (2, 2)


@pytest.mark.parametrize("changed_order", ("generators", "branches"))
def test_matrix_driver_rejects_generator_or_branch_order_changes(changed_order):
    period_zero = _tiny_period(
        generators=(
            Generator("g1", "b1", 0.0, 20.0, 10.0),
            Generator("g2", "b2", 0.0, 10.0, 20.0),
        ),
        branches=(
            Branch("br1", "b1", "b2", 1.0, 100.0),
            Branch("br2", "b2", "b1", 2.0, 100.0),
        ),
    )
    period_one = _tiny_period(
        period_index=1,
        buses=(Bus("b1", 1.0, reference=True), Bus("b2", 6.0)),
        generators=(
            Generator("g1", "b1", 0.0, 21.0, 11.0),
            Generator("g2", "b2", 0.0, 11.0, 21.0),
        ),
        branches=(
            Branch("br1", "b1", "b2", 1.0, 90.0),
            Branch("br2", "b2", "b1", 2.0, 90.0),
        ),
    )
    if changed_order == "generators":
        period_one = HostingCapacityPeriod(
            **{
                **period_one.__dict__,
                "generators": tuple(reversed(period_one.generators)),
            }
        )
    else:
        period_one = HostingCapacityPeriod(
            **{
                **period_one.__dict__,
                "branches": tuple(reversed(period_one.branches)),
            }
        )
    provider = _TwoPeriodProvider()
    provider.periods = (period_zero, period_one)

    with pytest.raises(ValueError, match="different fixed topology"):
        compute_hosting_capacity_matrix(provider, period_indices=[0, 1])


def test_matrix_driver_preserves_requested_and_resolved_backend_in_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(dcopf, "resolve_solver_backend", lambda requested: "highs")
    monkeypatch.setattr(
        "tools.hosting_capacity._solve_matrix_period",
        lambda period, bus_ids, *, solver_backend, solver_threads: _fake_matrix_result(period, bus_ids),
    )

    compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0],
        solver_backend="auto",
        output_dir=tmp_path,
    )

    metadata = json.loads((tmp_path / "hosting_capacity_metadata.json").read_text(encoding="utf-8"))
    assert metadata["solver_backend"] == "auto"
    assert metadata["resolved_solver_backend"] == "highs"
    assert metadata["backend"]["requested"] == "auto"
    assert metadata["backend"]["resolved"] == "highs"


def test_matrix_driver_partial_outputs_count_pending_points_separately(tmp_path, monkeypatch):
    def failing_period_solver(period, bus_ids, *, solver_backend, solver_threads):
        if period.period_index == 1:
            raise RuntimeError("later period failed")
        return _fake_matrix_result(period, bus_ids)

    monkeypatch.setattr("tools.hosting_capacity._solve_matrix_period", failing_period_solver)
    with pytest.raises(RuntimeError, match="later period failed"):
        compute_hosting_capacity_matrix(
            _TwoPeriodProvider(),
            period_indices=[0, 1],
            bus_ids=["b1", "b2"],
            solver_backend="highs",
            output_dir=tmp_path,
        )

    metadata = json.loads((tmp_path / "hosting_capacity_metadata.json").read_text(encoding="utf-8"))
    assert metadata["counts"]["completed_count"] == 2
    assert metadata["counts"]["optimal_count"] == 2
    assert metadata["counts"]["failed_count"] == 0
    assert metadata["counts"]["pending_count"] == 2
    rows = (tmp_path / "hosting_capacity_summary.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == (
        "period_index,optimal_count,failed_count,min_hosting_capacity_mw,"
        "median_hosting_capacity_mw,max_hosting_capacity_mw"
    )
    assert rows[2].startswith("1,0,0,nan,nan,nan")


def _tamper_checkpoint(checkpoint_path, **changes):
    with np.load(checkpoint_path, allow_pickle=False) as checkpoint:
        arrays = {name: checkpoint[name] for name in checkpoint.files}
    arrays.update(changes)
    np.savez_compressed(checkpoint_path, **arrays)


@pytest.mark.parametrize(
    ("status", "capacity", "objective"),
    (
        ("unknown_status", 1.0, 1.0),
        ("optimal", math.nan, 1.0),
        ("optimal", 1.0, math.nan),
    ),
)
def test_matrix_driver_recomputes_malformed_checkpoint_points(
    tmp_path, monkeypatch, status, capacity, objective
):
    calls = []

    def fake_period_solver(period, bus_ids, *, solver_backend, solver_threads):
        calls.append(period.period_index)
        return _fake_matrix_result(period, bus_ids)

    monkeypatch.setattr("tools.hosting_capacity._solve_matrix_period", fake_period_solver)
    checkpoint_dir = tmp_path / "checkpoints"
    compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        checkpoint_dir=checkpoint_dir,
    )
    calls.clear()
    checkpoint_path = checkpoint_dir / "period_0.npz"
    _tamper_checkpoint(
        checkpoint_path,
        status=np.asarray([status, "optimal"], dtype="<U32"),
        capacity_mw=np.asarray([capacity, 2.0]),
        objective_value=np.asarray([objective, 2.0]),
    )

    result = compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        checkpoint_dir=checkpoint_dir,
    )
    assert calls == [0]
    assert result.status.tolist() == [["optimal", "optimal"]]


def test_matrix_driver_recomputes_incomplete_checkpoint(tmp_path, monkeypatch):
    calls = []

    def fake_period_solver(period, bus_ids, *, solver_backend, solver_threads):
        calls.append(period.period_index)
        return _fake_matrix_result(period, bus_ids)

    monkeypatch.setattr("tools.hosting_capacity._solve_matrix_period", fake_period_solver)
    checkpoint_dir = tmp_path / "checkpoints"
    compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        checkpoint_dir=checkpoint_dir,
    )
    calls.clear()
    _tamper_checkpoint(checkpoint_dir / "period_0.npz", complete=np.asarray(False))

    compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        checkpoint_dir=checkpoint_dir,
    )
    assert calls == [0]


def test_matrix_driver_sanitizes_checkpoint_diagnostics(tmp_path, monkeypatch):
    diagnostic = "solver failed at /private/tmp/secret"

    def fake_period_solver(period, bus_ids, *, solver_backend, solver_threads):
        result = _fake_matrix_result(period, bus_ids)
        result["diagnostic"] = np.asarray([diagnostic, ""], dtype="<U512")
        return result

    monkeypatch.setattr("tools.hosting_capacity._solve_matrix_period", fake_period_solver)
    checkpoint_dir = tmp_path / "checkpoints"
    compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        checkpoint_dir=checkpoint_dir,
    )

    with np.load(checkpoint_dir / "period_0.npz", allow_pickle=False) as checkpoint:
        persisted_diagnostic = str(checkpoint["diagnostic"][0])
    assert "/private/tmp/secret" not in persisted_diagnostic
    assert "solver failed at <local-path>" == persisted_diagnostic


@pytest.mark.parametrize("solve_time", (math.nan, math.inf, -1.0))
def test_matrix_driver_recomputes_checkpoint_with_invalid_solve_time(
    tmp_path, monkeypatch, solve_time
):
    calls = []

    def fake_period_solver(period, bus_ids, *, solver_backend, solver_threads):
        calls.append(period.period_index)
        return _fake_matrix_result(period, bus_ids)

    monkeypatch.setattr("tools.hosting_capacity._solve_matrix_period", fake_period_solver)
    checkpoint_dir = tmp_path / "checkpoints"
    compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        checkpoint_dir=checkpoint_dir,
    )
    calls.clear()
    _tamper_checkpoint(
        checkpoint_dir / "period_0.npz",
        solve_time_s=np.asarray([solve_time, 1.0]),
    )

    compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        checkpoint_dir=checkpoint_dir,
    )
    assert calls == [0]


def test_matrix_period_worker_rejects_provider_identity_drift(monkeypatch):
    provider = _TwoPeriodProvider()
    expected_fingerprint = hosting_capacity._period_fingerprint(
        hosting_capacity._period_for_requested_index(provider, 0)
    )
    provider.periods = (
        _tiny_period(
            period_index=0,
            buses=(Bus("b1", 0.0, reference=True), Bus("b2", 99.0)),
        ),
        provider.periods[1],
    )
    monkeypatch.setattr(
        "tools.hosting_capacity._solve_matrix_period",
        lambda *args, **kwargs: pytest.fail("worker solved after identity drift"),
    )

    with pytest.raises(ValueError, match="period identity mismatch"):
        hosting_capacity._matrix_period_worker(
            (provider, 0, ("b1", "b2"), "highs", 1, expected_fingerprint)
        )


def test_matrix_driver_retains_period_diagnostics_for_construction_failure(monkeypatch):
    def failing_model(**kwargs):
        raise RuntimeError("backend exploded")

    monkeypatch.setattr(dcopf, "_reusable_dcopf_model", failing_model)
    result = compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
    )

    assert result.status.tolist() == [["solver_error", "solver_error"]]
    diagnostics = result.metadata["diagnostics"]
    assert diagnostics["0"]["b1"] == "backend construction failed: RuntimeError: backend exploded"


def test_matrix_driver_sanitizes_absolute_paths_in_persisted_diagnostics(tmp_path, monkeypatch):
    diagnostic = "backend failed at /private/tmp/secret and /Users/example"

    def fake_period_solver(period, bus_ids, *, solver_backend, solver_threads):
        result = _fake_matrix_result(period, bus_ids, statuses=["solver_error", "optimal"])
        result["capacity_mw"][0] = np.nan
        result["objective_value"][0] = np.nan
        result["diagnostic"] = np.asarray([diagnostic, ""], dtype="<U512")
        return result

    monkeypatch.setattr("tools.hosting_capacity._solve_matrix_period", fake_period_solver)
    output_dir = tmp_path / "outputs"
    result = compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        output_dir=output_dir,
    )

    assert result.metadata["diagnostics"]["0"]["b1"] == (
        "backend failed at <local-path> and <local-path>"
    )
    persisted = (output_dir / "hosting_capacity_metadata.json").read_text(encoding="utf-8")
    assert "/private/tmp/secret" not in persisted
    assert "/Users/example" not in persisted


@pytest.mark.parametrize("changed_component", ("generator_endpoint", "branch_endpoint"))
def test_matrix_driver_rejects_structural_generator_or_branch_changes(changed_component):
    provider = _TwoPeriodProvider()
    if changed_component == "generator_endpoint":
        changed_period = _tiny_period(
            period_index=1,
            generators=(Generator("g1", "b2", 0.0, 20.0, 10.0),),
        )
    else:
        changed_period = _tiny_period(
            period_index=1,
            branches=(Branch("br1", "b2", "b1", 1.0, 100.0),),
        )
    provider.periods = (provider.periods[0], changed_period)

    with pytest.raises(ValueError, match="fixed topology"):
        compute_hosting_capacity_matrix(provider, period_indices=[0, 1])


def test_matrix_driver_rejects_negative_period_before_provider_call():
    calls = []

    def provider(period_index):
        calls.append(period_index)
        return _tiny_period(period_index=period_index)

    with pytest.raises(ValueError, match="nonnegative"):
        compute_hosting_capacity_matrix(provider, period_indices=[-1])
    assert calls == []


def test_matrix_driver_writes_exact_summary_header_and_required_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.hosting_capacity._solve_matrix_period",
        lambda period, bus_ids, *, solver_backend, solver_threads: _fake_matrix_result(period, bus_ids),
    )
    output_dir = tmp_path / "outputs"
    compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0, 1],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        output_dir=output_dir,
    )

    summary_header = (output_dir / "hosting_capacity_summary.csv").read_text(encoding="utf-8").splitlines()[0]
    assert summary_header == (
        "period_index,optimal_count,failed_count,min_hosting_capacity_mw,"
        "median_hosting_capacity_mw,max_hosting_capacity_mw"
    )
    metadata_text = (output_dir / "hosting_capacity_metadata.json").read_text(encoding="utf-8")
    metadata = json.loads(metadata_text)
    assert {"backend", "settings", "tolerances", "counts", "complete", "ordering"} <= set(metadata)
    assert metadata["complete"] is True
    assert metadata["ordering"] == {
        "period_indices": [0, 1],
        "bus_ids": ["b1", "b2"],
        "array_axes": ["period_index", "bus_id"],
    }
    assert str(tmp_path) not in metadata_text


def test_matrix_driver_sanitizes_embedded_paths_in_all_metadata_strings(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.hosting_capacity._solve_matrix_period",
        lambda period, bus_ids, *, solver_backend, solver_threads: _fake_matrix_result(period, bus_ids),
    )
    provider = _TwoPeriodProvider()
    period = provider.periods[0]
    provider.periods = (
        HostingCapacityPeriod(
            **{
                **period.__dict__,
                "source_metadata": {
                    "source": "test",
                    "note": "loaded from /workspace/project/input",
                },
            }
        ),
        provider.periods[1],
    )
    output_dir = tmp_path / "outputs"

    compute_hosting_capacity_matrix(
        provider,
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        output_dir=output_dir,
    )

    metadata_text = (output_dir / "hosting_capacity_metadata.json").read_text(encoding="utf-8")
    assert "/workspace/project/input" not in metadata_text
    assert "loaded from <local-path>" in metadata_text


def test_json_safe_sanitizes_arbitrary_absolute_paths_but_preserves_urls():
    metadata = hosting_capacity._json_safe(
        {
            "note": "inputs /mnt/data/input, /srv/project/input, /root/secret",
            "url": "https://example.com/project/input",
            "nested": ["keep this explanatory text", {"value": 3}],
        }
    )

    assert metadata["note"] == "inputs <local-path>, <local-path>, <local-path>"
    assert metadata["url"] == "https://example.com/project/input"
    assert metadata["nested"] == ["keep this explanatory text", {"value": 3}]


def test_matrix_driver_checkpoint_identity_includes_normalized_period_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.hosting_capacity._solve_matrix_period",
        lambda period, bus_ids, *, solver_backend, solver_threads: _fake_matrix_result(period, bus_ids),
    )
    provider = _TwoPeriodProvider()
    checkpoint_dir = tmp_path / "checkpoints"
    compute_hosting_capacity_matrix(
        provider,
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        checkpoint_dir=checkpoint_dir,
    )

    changed_load = _tiny_period(
        period_index=0,
        buses=(Bus("b1", 0.0, reference=True), Bus("b2", 6.0)),
    )
    provider.periods = (changed_load, provider.periods[1])
    with pytest.raises(ValueError, match="checkpoint identity mismatch"):
        compute_hosting_capacity_matrix(
            provider,
            period_indices=[0],
            bus_ids=["b1", "b2"],
            solver_backend="highs",
            checkpoint_dir=checkpoint_dir,
        )


@pytest.mark.parametrize(
    ("capacity", "objective"),
    ((math.nan, 1.0), (-1.0, 1.0), (1.0, math.nan), (1.0, -1.0)),
)
def test_matrix_driver_rewrites_invalid_optimal_values_as_numerical_failure(
    monkeypatch, capacity, objective
):
    def fake_period_solver(period, bus_ids, *, solver_backend, solver_threads):
        return {
            "capacity_mw": np.asarray([capacity, 2.0]),
            "status": np.asarray(["optimal", "optimal"], dtype=object),
            "objective_value": np.asarray([objective, 2.0]),
            "solve_time_s": np.asarray([0.1, 0.1]),
        }

    monkeypatch.setattr("tools.hosting_capacity._solve_matrix_period", fake_period_solver)
    result = compute_hosting_capacity_matrix(
        _TwoPeriodProvider(),
        period_indices=[0],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
    )

    assert result.status[0, 0] == "numerical_failure"
    assert np.isnan(result.capacity_mw[0, 0])
    assert np.isnan(result.objective_value[0, 0])


def test_matrix_driver_rejects_provider_period_index_mismatch(monkeypatch):
    provider = _TwoPeriodProvider()
    provider.periods = (provider.periods[0], _tiny_period(period_index=9))
    calls = []
    monkeypatch.setattr(
        "tools.hosting_capacity._solve_matrix_period",
        lambda *args, **kwargs: calls.append(args) or _fake_matrix_result(args[0], args[1]),
    )

    with pytest.raises(ValueError, match="period_index"):
        compute_hosting_capacity_matrix(provider, period_indices=[1])
    assert calls == []


def test_matrix_driver_rejects_unpickleable_provider_before_pool_creation():
    def provider(period_index):
        return _tiny_period(period_index=period_index)

    with pytest.raises(ValueError, match="period_provider must be pickleable when workers > 1"):
        compute_hosting_capacity_matrix(provider, period_indices=[0], workers=2)


@pytest.mark.skipif(importlib.util.find_spec("highspy") is None, reason="highspy is not installed")
def test_matrix_driver_spawn_workers_match_single_worker_without_cross_process_solver_objects():
    provider = _TwoPeriodProvider()
    single = compute_hosting_capacity_matrix(
        provider,
        period_indices=[0, 1],
        bus_ids=["b1", "b2"],
        solver_backend="highs",
        workers=1,
    )
    try:
        parallel = compute_hosting_capacity_matrix(
            provider,
            period_indices=[0, 1],
            bus_ids=["b1", "b2"],
            solver_backend="highs",
            workers=2,
        )
    except OSError as error:
        pytest.skip(f"spawn process execution unavailable in this environment: {error}")

    np.testing.assert_allclose(parallel.capacity_mw, single.capacity_mw)
    np.testing.assert_allclose(parallel.objective_value, single.objective_value)
    np.testing.assert_array_equal(parallel.status, single.status)


def test_validate_period_preserves_bus_order_and_normalizes_empty_injections():
    period = validate_period(
        _tiny_period(
            buses=[Bus("b1", 0.0, reference=True), Bus("b2", 5.0)],
            fixed_injection_mw_by_bus={},
            source_metadata={"source": "test"},
        )
    )

    assert tuple(bus.bus_id for bus in period.buses) == ("b1", "b2")
    assert period.fixed_injection_mw_by_bus == {}
    assert isinstance(period.buses, tuple)
    assert isinstance(period.fixed_injection_mw_by_bus, dict) is False
    with pytest.raises(FrozenInstanceError):
        period.period_index = 1


def test_validate_period_defaults_first_bus_as_reference():
    period = validate_period(
        _tiny_period(
            buses=[Bus("b1", 0.0), Bus("b2", 5.0)],
        )
    )

    assert period.buses[0].reference is True
    assert period.buses[1].reference is False


def test_statuses_are_exact_and_point_serializes_without_binding_resources():
    assert HOSTING_CAPACITY_STATUSES == frozenset(
        {
            "optimal",
            "infeasible_base_case",
            "unbounded",
            "numerical_failure",
            "invalid_input",
            "solver_error",
            "interrupted",
        }
    )
    point = HostingCapacityPoint(
        period_index=0,
        bus_id="b2",
        hosting_capacity_mw=12.5,
        status="optimal",
        objective_value=100.0,
        solve_time_s=0.25,
        binding_resources=("g1:pmax",),
    )
    assert point.to_record() == {
        "period_index": 0,
        "bus_id": "b2",
        "hosting_capacity_mw": 12.5,
        "status": "optimal",
        "objective_value": 100.0,
        "solve_time_s": 0.25,
    }


def test_validate_period_rejects_duplicate_bus_ids():
    with pytest.raises(ValueError, match="duplicate bus ID.*b1"):
        validate_period(
            _tiny_period(
                buses=(Bus("b1", 0.0, reference=True), Bus("b1", 5.0)),
            )
        )


def test_validate_period_rejects_unknown_injection_bus():
    with pytest.raises(ValueError, match="fixed injection.*unknown bus.*missing"):
        validate_period(_tiny_period(fixed_injection_mw_by_bus={"missing": 1.0}))


def test_reusable_model_exposes_candidate_demand_probe_contract():
    constructor_parameters = inspect.signature(ReusableDCOPFModel).parameters
    method_parameters = inspect.signature(ReusableDCOPFModel.max_additional_demand).parameters

    assert "candidate_demand_bus_id" in constructor_parameters
    assert "enable_candidate_demand_probes" in constructor_parameters
    assert tuple(method_parameters)[:3] == ("self", "buses", "bus_id")
    assert method_parameters["upper_bound_mw"].default is None
    assert method_parameters["fixed_injection_mw_by_bus"].default is None


@pytest.mark.parametrize(
    ("status", "expected"),
    (("optimal", "optimal"), ("infeasible", "infeasible_base_case"),
     ("unbounded", "unbounded"), ("ambiguous", "solver_error"),
     ("numerical", "numerical_failure"), ("interrupted", "interrupted"),
     ("other", "solver_error")),
)
def test_candidate_probe_status_mapping_is_backend_safe(status, expected):
    status_mapper = getattr(dcopf, "_candidate_probe_status", None)
    assert status_mapper is not None
    assert status_mapper(status, base_case=True) == expected
    assert status_mapper(status, base_case=False) == (
        "solver_error" if status == "infeasible" else expected
    )


@pytest.mark.parametrize("status_name", ("kTimeLimit", "kInterrupt"))
def test_highs_interruption_statuses_map_without_highspy(status_name):
    status_objects = type(
        "FakeHighsModelStatus",
        (),
        {
            "kOptimal": object(),
            "kTimeLimit": object(),
            "kInterrupt": object(),
            "kUnbounded": object(),
            "kInfeasible": object(),
        },
    )

    status = getattr(status_objects, status_name)

    assert dcopf._highs_candidate_probe_status(status, status_objects, base_case=False) == "interrupted"


@pytest.mark.parametrize("status_name", ("TIME_LIMIT", "INTERRUPTED", "USER_OBJ_LIMIT"))
def test_gurobi_interruption_statuses_map_without_gurobipy(status_name):
    grb = type(
        "FakeGRB",
        (),
        {
            "OPTIMAL": 2,
            "INFEASIBLE": 3,
            "INF_OR_UNBD": 4,
            "UNBOUNDED": 5,
            "TIME_LIMIT": 9,
            "INTERRUPTED": 11,
            "USER_OBJ_LIMIT": 15,
            "NUMERIC": 12,
        },
    )

    assert dcopf._gurobi_candidate_probe_status(
        getattr(grb, status_name), grb, base_case=False
    ) == "interrupted"


@pytest.mark.skipif(importlib.util.find_spec("highspy") is None, reason="highspy is not installed")
def test_solve_hosting_capacity_at_bus_returns_tiny_period_capacity():
    point = solve_hosting_capacity_at_bus(_tiny_period(), "b2", solver_backend="highs")

    assert point.period_index == 0
    assert point.bus_id == "b2"
    assert point.hosting_capacity_mw == pytest.approx(15.0)
    assert point.status == "optimal"
    assert point.objective_value == pytest.approx(15.0)
    assert point.solve_time_s is not None and point.solve_time_s >= 0.0
    assert point.binding_resources == ()


def test_solve_hosting_capacity_at_bus_unknown_bus_is_invalid_input():
    point = solve_hosting_capacity_at_bus(_tiny_period(), "missing", solver_backend="highs")

    assert point.status == "invalid_input"
    assert point.hosting_capacity_mw is None
    assert point.objective_value is None
    assert point.binding_resources == ()


@pytest.mark.skipif(importlib.util.find_spec("highspy") is None, reason="highspy is not installed")
def test_solve_hosting_capacity_at_bus_maps_base_infeasibility():
    period = _tiny_period(
        generators=(Generator("g1", "b1", 0.0, 4.0, 10.0),),
    )

    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")

    assert point.status == "infeasible_base_case"
    assert point.hosting_capacity_mw is None
    assert point.objective_value is None


@pytest.mark.skipif(importlib.util.find_spec("highspy") is None, reason="highspy is not installed")
def test_solve_hosting_capacity_at_bus_maps_disconnected_candidate():
    period = _tiny_period(branches=())

    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")

    assert point.status == "infeasible_base_case"
    assert point.hosting_capacity_mw is None


def test_solve_hosting_capacity_at_bus_maps_malformed_values_without_zero_capacity():
    point = solve_hosting_capacity_at_bus(
        _tiny_period(), "b2", solver_backend="highs", upper_bound_mw=math.nan
    )

    assert point.status == "invalid_input"
    assert point.hosting_capacity_mw is None
    assert point.objective_value is None


def test_solve_hosting_capacity_at_bus_uses_resolved_auto_backend(monkeypatch):
    class FakeModel:
        def __init__(self, **kwargs):
            assert kwargs["candidate_demand_bus_id"] == "b2"

        def max_additional_demand(self, buses, bus_id, *, upper_bound_mw, fixed_injection_mw_by_bus):
            assert bus_id == "b2"
            return 7.5, "optimal"

    monkeypatch.setattr(dcopf, "resolve_solver_backend", lambda backend: "highs")
    monkeypatch.setattr(dcopf, "ReusableDCOPFModel", FakeModel)

    point = solve_hosting_capacity_at_bus(_tiny_period(), "b2", solver_backend="auto")

    assert point.status == "optimal"
    assert point.hosting_capacity_mw == pytest.approx(7.5)


@pytest.mark.parametrize(
    "failure_status", ("solver_error", "interrupted", "numerical_failure", "unbounded")
)
def test_solve_hosting_capacity_at_bus_maps_backend_statuses(monkeypatch, failure_status):
    class FakeModel:
        def __init__(self, **kwargs):
            pass

        def max_additional_demand(self, *args, **kwargs):
            return math.nan, failure_status

    monkeypatch.setattr(dcopf, "resolve_solver_backend", lambda backend: "highs")
    monkeypatch.setattr(dcopf, "ReusableDCOPFModel", FakeModel)

    point = solve_hosting_capacity_at_bus(_tiny_period(), "b2", solver_backend="auto")

    assert point.status == failure_status
    assert point.hosting_capacity_mw is None
    assert point.objective_value is None


def test_solve_hosting_capacity_at_bus_preserves_backend_diagnostic_without_serializing_it(monkeypatch):
    class FakeModel:
        def __init__(self, **kwargs):
            pass

        def max_additional_demand(self, *args, **kwargs):
            raise RuntimeError("backend exploded at /private/tmp/secret")

    monkeypatch.setattr(dcopf, "resolve_solver_backend", lambda backend: "highs")
    monkeypatch.setattr(dcopf, "ReusableDCOPFModel", FakeModel)

    point = solve_hosting_capacity_at_bus(_tiny_period(), "b2", solver_backend="auto")

    assert point.status == "solver_error"
    assert point.diagnostic is not None
    assert "solver execution failed" in point.diagnostic
    assert "RuntimeError" in point.diagnostic
    assert "backend exploded" in point.diagnostic
    assert "/private/tmp/secret" in point.diagnostic
    assert "diagnostic" not in point.to_record()


def test_solve_hosting_capacity_at_bus_maps_unhashable_backend_status(monkeypatch):
    class FakeModel:
        def __init__(self, **kwargs):
            pass

        def max_additional_demand(self, *args, **kwargs):
            return math.nan, ["malformed"]

    monkeypatch.setattr(dcopf, "resolve_solver_backend", lambda backend: "highs")
    monkeypatch.setattr(dcopf, "ReusableDCOPFModel", FakeModel)

    point = solve_hosting_capacity_at_bus(_tiny_period(), "b2", solver_backend="auto")

    assert point.status == "solver_error"
    assert point.hosting_capacity_mw is None
    assert point.diagnostic is not None
    assert "malformed backend status" in point.diagnostic
    assert "malformed" in point.diagnostic


@pytest.mark.skipif(importlib.util.find_spec("highspy") is None, reason="highspy is not installed")
def test_hosting_capacity_boundary_uses_public_task2_model_api():
    period = _tiny_period()
    model = ReusableDCOPFModel(
        buses=tuple(period.buses),
        generators=tuple(period.generators),
        branches=tuple(period.branches),
        candidate_demand_bus_id="b2",
    )
    capacity, status = model.max_additional_demand(
        tuple(period.buses),
        "b2",
        fixed_injection_mw_by_bus=period.fixed_injection_mw_by_bus,
    )

    assert status == "optimal"
    assert capacity == pytest.approx(15.0, abs=1e-7)
    at_capacity = _tiny_period(buses=(Bus("b1", 0.0, reference=True), Bus("b2", 20.0)))
    above_capacity = _tiny_period(buses=(Bus("b1", 0.0, reference=True), Bus("b2", 20.0 + 1e-5)))
    assert model.solve(tuple(at_capacity.buses)).status == "optimal"
    with pytest.raises(DCOPFInfeasible):
        model.solve(tuple(above_capacity.buses), return_result=False)
