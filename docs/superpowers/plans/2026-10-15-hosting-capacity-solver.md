# Hosting Capacity Solver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable, exact DCOPF-based solver that computes the maximum additional demand at each candidate bus for every requested operating period, with resumable multi-period output for IEEE-14 and validated ACTIVSg2000 artifacts.

**Architecture:** Add a focused hosting-capacity module above the existing `tools.activsg2000.dcopf` primitives. The low-level model will expose a candidate-demand probe with the same nodal-balance sign convention as the current DCOPF; the hosting-capacity driver will reuse one LP per period, activate one bus at a time, and parallelize only across independent periods. Dataset adapters will normalize IEEE-14 and prepared ACTIVSg2000 inputs into one period-provider interface, while atomic checkpoints and deterministic metadata make the approximately 8.7-million-point ACTIVSg2000 run restartable and auditable.

**Tech Stack:** Python 3.11, `highspy`/HiGHS as the portable default, optional `gurobipy`, NumPy, standard-library `multiprocessing`, CSV/JSON, pytest, and the existing prepared-scenario validation utilities.

---

## File map and ownership

The implementation must keep responsibilities separate:

- Create `tools/hosting_capacity.py` for normalized period inputs, result types, reusable candidate-demand solving, matrix orchestration, checkpointing, and output serialization.
- Modify `tools/activsg2000/dcopf.py` only to expose the existing prepared-period conversion and to add a candidate-demand probe to both reusable solver backends. Do not alter storage-enabled evaluator semantics.
- Create `src/ercot_game/hosting_capacity_cli.py` for the command-line interface and argument validation.
- Modify `pyproject.toml` to expose `ercot-game-hosting-capacity` and make the matrix/output dependency explicit with `numpy>=1.24,<3`; do not add a solver dependency beyond the existing HiGHS and optional Gurobi paths.
- Create `tests/test_hosting_capacity.py` for analytical, status, sign-convention, persistence, and orchestration tests.
- Create `tests/test_hosting_capacity_prepared_artifact.py` for the prepared ACTIVSg2000 adapter contract using a small generated artifact fixture.
- Create `tools/benchmarks/benchmark_hosting_capacity.py` for staged measurements; it must write results only to a caller-selected output directory.
- Create `docs/hosting_capacity/README.md` for the user-facing algorithm, dataset contract, output schema, exactness statement, and benchmark procedure.

The implementation must not modify the batch evaluator, student CSV schema, storage dispatch, fair projection, or RL environment behavior. Hosting capacity is a separate diagnostic built on the existing DCOPF model.

## Task 1: Define normalized inputs, results, and failure statuses

**Files:**

- Create: `tools/hosting_capacity.py`
- Test: `tests/test_hosting_capacity.py`

- [ ] **Step 1: Write failing type and validation tests**

Add tests that establish the public contract before implementation:

~~~python
import pytest

from tools.activsg2000.dcopf import Branch, Bus, Generator
from tools.hosting_capacity import (
    HOSTING_CAPACITY_STATUSES,
    HostingCapacityPeriod,
    HostingCapacityPoint,
    validate_period,
)


def _tiny_period() -> HostingCapacityPeriod:
    return HostingCapacityPeriod(
        period_index=0,
        buses=(Bus("b1", 0.0, reference=True), Bus("b2", 5.0)),
        generators=(Generator("g1", "b1", 0.0, 20.0, 10.0),),
        branches=(Branch("br1", "b1", "b2", 1.0, 20.0),),
        fixed_injection_mw_by_bus={},
        source_metadata={"scenario": "unit-test"},
    )


def test_period_validation_preserves_bus_order_and_accepts_empty_fixed_injections():
    period = validate_period(_tiny_period())
    assert period.period_index == 0
    assert tuple(bus.bus_id for bus in period.buses) == ("b1", "b2")
    assert period.fixed_injection_mw_by_bus == {}


def test_point_statuses_are_closed_and_point_is_serializable():
    assert HOSTING_CAPACITY_STATUSES == {
        "optimal",
        "infeasible_base_case",
        "unbounded",
        "numerical_failure",
        "invalid_input",
        "solver_error",
        "interrupted",
    }
    point = HostingCapacityPoint(
        period_index=0,
        bus_id="b2",
        hosting_capacity_mw=15.0,
        status="optimal",
        objective_value=15.0,
        solve_time_s=0.01,
        binding_resources=(),
    )
    assert point.to_record()["hosting_capacity_mw"] == 15.0


def test_invalid_period_rejects_duplicate_buses_and_unknown_fixed_injection_bus():
    period = _tiny_period()
    duplicate = period.__class__(
        period_index=0,
        buses=(period.buses[0], period.buses[0]),
        generators=period.generators,
        branches=period.branches,
        fixed_injection_mw_by_bus={},
        source_metadata={},
    )
    with pytest.raises(ValueError, match="duplicate bus"):
        validate_period(duplicate)
    unknown_injection = period.__class__(
        period_index=0,
        buses=period.buses,
        generators=period.generators,
        branches=period.branches,
        fixed_injection_mw_by_bus={"missing": 1.0},
        source_metadata={},
    )
    with pytest.raises(ValueError, match="unknown bus"):
        validate_period(unknown_injection)
~~~

- [ ] **Step 2: Run the focused tests and verify they fail for missing symbols**

Run:

~~~bash
python -m pytest tests/test_hosting_capacity.py -q
~~~

Expected result: collection fails because `tools.hosting_capacity` does not yet exist.

- [ ] **Step 3: Implement the normalized data and result types**

Define these exact public symbols:

~~~python
from dataclasses import dataclass
from typing import Literal, Mapping

HostingCapacityStatus = Literal[
    "optimal",
    "infeasible_base_case",
    "unbounded",
    "numerical_failure",
    "invalid_input",
    "solver_error",
    "interrupted",
]

HOSTING_CAPACITY_STATUSES = frozenset({
    "optimal",
    "infeasible_base_case",
    "unbounded",
    "numerical_failure",
    "invalid_input",
    "solver_error",
    "interrupted",
})


@dataclass(frozen=True)
class HostingCapacityPeriod:
    period_index: int
    buses: tuple[Bus, ...]
    generators: tuple[Generator, ...]
    branches: tuple[Branch, ...]
    fixed_injection_mw_by_bus: Mapping[str, float]
    source_metadata: Mapping[str, str]


@dataclass(frozen=True)
class HostingCapacityPoint:
    period_index: int
    bus_id: str
    hosting_capacity_mw: float | None
    status: HostingCapacityStatus
    objective_value: float | None
    solve_time_s: float
    binding_resources: tuple[str, ...]

    def to_record(self) -> dict[str, object]:
        return {
            "period_index": self.period_index,
            "bus_id": self.bus_id,
            "hosting_capacity_mw": self.hosting_capacity_mw,
            "status": self.status,
            "objective_value": self.objective_value,
            "solve_time_s": self.solve_time_s,
        }
~~~

`validate_period` must call the existing DCOPF validation path or reproduce only its public checks: positive finite base data, unique bus/generator/branch IDs, valid branch endpoints, exactly one reference bus after defaulting, valid generator limits, valid branch limits, and fixed-injection keys contained in the bus set. It must return an immutable normalized copy with a deterministic bus order.

- [ ] **Step 4: Run the focused tests**

Run:

~~~bash
python -m pytest tests/test_hosting_capacity.py -q
~~~

Expected result: all Task 1 tests pass.

- [ ] **Step 5: Commit the contract**

~~~bash
git add tools/hosting_capacity.py tests/test_hosting_capacity.py
git commit -m "Add hosting capacity data contracts"
~~~

## Task 2: Add an exact candidate-demand probe to the reusable DCOPF

**Files:**

- Modify: `tools/activsg2000/dcopf.py:432-713`
- Modify: `tools/activsg2000/dcopf.py:714-951`
- Test: `tests/test_hosting_capacity.py`
- Test: `tests/test_dcopf_reuse.py`

- [ ] **Step 1: Write sign-convention and equivalence tests**

Use a two-bus network where the reference generator has 20 MW available, the base load is 5 MW, and the branch limit is 20 MW. The additional demand at `b2` must be 15 MW, not `-15 MW`, and a fresh LP must match the probe result:

~~~python
def test_candidate_demand_is_positive_load_and_matches_fresh_lp():
    period = _tiny_period()
    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")
    assert point.status == "optimal"
    assert point.hosting_capacity_mw == pytest.approx(15.0)


def test_candidate_demand_is_limited_by_branch_capacity():
    period = HostingCapacityPeriod(
        period_index=0,
        buses=(Bus("b1", 0.0, reference=True), Bus("b2", 5.0)),
        generators=(Generator("g1", "b1", 0.0, 50.0, 10.0),),
        branches=(Branch("br1", "b1", "b2", 1.0, 9.0),),
        fixed_injection_mw_by_bus={},
        source_metadata={},
    )
    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")
    assert point.hosting_capacity_mw == pytest.approx(4.0)
~~~

Add the same candidate-demand expectation to `tests/test_dcopf_reuse.py` through the public reusable model API. Include one test that compares HiGHS reusable results to `solve_dcopf` on a base case and on a case with demand increased by the returned capacity.

- [ ] **Step 2: Run the tests and verify the new probe API is absent**

Run:

~~~bash
python -m pytest tests/test_hosting_capacity.py tests/test_dcopf_reuse.py -q
~~~

Expected result: the new hosting-capacity tests fail because the candidate-demand API is not defined; all unrelated existing tests remain unchanged.

- [ ] **Step 3: Extend the HiGHS reusable model without changing ordinary dispatch**

Add a `candidate_demand_bus_id` option to `ReusableDCOPFModel.__init__` and a method with this exact contract:

~~~python
def max_additional_demand(
    self,
    buses: tuple[Bus, ...],
    bus_id: str,
    *,
    upper_bound_mw: float | None = None,
    fixed_injection_mw_by_bus: Mapping[str, float] | None = None,
) -> tuple[float, str]:
    """Return (maximum additional load in MW, solver status)."""
~~~

Implement the candidate load as a nonnegative LP column with coefficient `-1.0` in the selected bus balance equation and objective coefficient `-1.0` for the minimization-form API used by HiGHS. The row right-hand side must remain `base_demand - fixed_injection`, so the model equation is exactly the existing balance equation plus `-h` on the left. The candidate column is bounded by `[0, +inf]` unless the caller supplies a finite upper bound. Existing generator objective coefficients must remain unchanged; the method must not mutate them after returning.

Return `"optimal"` with the finite candidate value, `"unbounded"` when the backend reports an unbounded maximization, `"infeasible_base_case"` when a solve with candidate load fixed at zero is infeasible, and `"numerical_failure"` or `"solver_error"` for the corresponding backend states. Restore the candidate bounds and objective after the call so ordinary `solve()` calls behave exactly as before.

- [ ] **Step 4: Mirror the same contract in the optional Gurobi reusable model**

Add the same constructor option and method to `GurobiReusableDCOPFModel`. Use `ModelSense = GRB.MAXIMIZE` only while solving the candidate-demand objective, then restore the original objective and model sense. Keep explicit `solver_backend="gurobi"` behavior unchanged when `gurobipy` is unavailable. Do not import Gurobi at module import time.

- [ ] **Step 5: Run the focused and existing reusable-model tests**

Run:

~~~bash
python -m pytest tests/test_hosting_capacity.py tests/test_dcopf_reuse.py tests/test_dcopf_gurobi_optional.py -q
~~~

Expected result: HiGHS tests pass; the Gurobi test is skipped only when `gurobipy` is not installed. Existing dispatch and backend-selection behavior remains passing.

- [ ] **Step 6: Commit the reusable probe**

~~~bash
git add tools/activsg2000/dcopf.py tests/test_hosting_capacity.py tests/test_dcopf_reuse.py
git commit -m "Add reusable candidate demand DCOPF probe"
~~~

## Task 3: Implement the single-point hosting-capacity API

**Files:**

- Modify: `tools/hosting_capacity.py`
- Test: `tests/test_hosting_capacity.py`

- [ ] **Step 1: Add tests for the public single-point function and statuses**

Add tests for:

~~~python
def test_single_point_returns_capacity_and_timing():
    point = solve_hosting_capacity_at_bus(_tiny_period(), "b2", solver_backend="highs")
    assert point.period_index == 0
    assert point.bus_id == "b2"
    assert point.status == "optimal"
    assert point.solve_time_s >= 0.0


def test_unknown_bus_returns_invalid_input_status():
    point = solve_hosting_capacity_at_bus(_tiny_period(), "missing", solver_backend="highs")
    assert point.status == "invalid_input"
    assert point.hosting_capacity_mw is None


def test_infeasible_base_case_is_not_reported_as_zero_capacity():
    period = HostingCapacityPeriod(
        period_index=0,
        buses=(Bus("b1", 0.0, reference=True), Bus("b2", 10.0)),
        generators=(Generator("g1", "b1", 0.0, 5.0, 10.0),),
        branches=(Branch("br1", "b1", "b2", 1.0, 20.0),),
        fixed_injection_mw_by_bus={},
        source_metadata={},
    )
    point = solve_hosting_capacity_at_bus(period, "b2", solver_backend="highs")
    assert point.status == "infeasible_base_case"
    assert point.hosting_capacity_mw is None
~~~

Add tests for a disconnected island, an unbounded candidate load, and invalid finite values. These tests must inspect the status rather than accepting a silent `0.0` replacement.

- [ ] **Step 2: Run the tests and confirm the driver is incomplete**

~~~bash
python -m pytest tests/test_hosting_capacity.py -q
~~~

Expected result: the newly added single-point tests fail until the driver is implemented.

- [ ] **Step 3: Implement the single-point driver**

Implement:

~~~python
def solve_hosting_capacity_at_bus(
    period: HostingCapacityPeriod,
    candidate_bus_id: str,
    *,
    solver_backend: str = "auto",
    solver_threads: int = 1,
    upper_bound_mw: float | None = None,
) -> HostingCapacityPoint:
~~~

The function must validate the period, resolve the backend through `tools.activsg2000.dcopf.resolve_solver_backend`, build one reusable candidate-demand model, perform a base feasibility solve at `h=0`, and then maximize `h`. Use `time.perf_counter()` for `solve_time_s`. Map backend exceptions to the declared status values and preserve the exception text only in an internal diagnostic field or log; do not put tracebacks in the dense result matrix. Binding-resource diagnostics are optional in the first implementation and must be an empty tuple unless a deterministic, tested extraction is available.

For `solver_backend="auto"`, use the existing resolver. For `solver_backend="highs"`, use HiGHS. For `solver_backend="gurobi"`, use the Gurobi reusable model. A single-point call must never include student-built generators, storage, or intertemporal state.

- [ ] **Step 4: Add boundary-feasibility tests**

For a finite optimum, solve the base network with candidate demand fixed at the reported `H` and assert optimality. Solve again with `H + 1e-6` or a scaled tolerance and assert infeasibility or a solver status that violates the capacity tolerance. Use an explicit tolerance helper:

~~~python
def assert_capacity_boundary(period, bus_id, capacity_mw):
    assert solve_candidate_fixed_load(period, bus_id, capacity_mw).status == "optimal"
    assert solve_candidate_fixed_load(period, bus_id, capacity_mw + 1e-6).status != "optimal"
~~~

The helper must be implemented in the test module using the public probe/model API; do not add a production-only test hook.

- [ ] **Step 5: Run all single-point tests and commit**

~~~bash
python -m pytest tests/test_hosting_capacity.py tests/test_dcopf_reuse.py tests/test_dcopf_gurobi_optional.py -q
git add tools/hosting_capacity.py tests/test_hosting_capacity.py
git commit -m "Implement single point hosting capacity"
~~~

## Task 4: Add normalized adapters for IEEE-14 and prepared ACTIVSg2000 periods

**Files:**

- Modify: `tools/activsg2000/dcopf.py` near `_prepared_period_inputs`
- Modify: `tools/hosting_capacity.py`
- Create: `tests/test_hosting_capacity_prepared_artifact.py`
- Modify: `tests/test_activsg2000_validation.py` only if a public prepared-period helper needs a focused contract test

- [ ] **Step 1: Write adapter contract tests**

Create a small validated prepared artifact fixture using the repository’s existing artifact writer/fixture conventions. The fixture must contain two periods, two buses, one generator, one branch, period-specific bus load, generator limits, branch limit, and fixed injection data. Test:

~~~python
def test_prepared_artifact_provider_returns_normalized_periods(tmp_path):
    artifact_dir = write_small_validated_artifact(tmp_path)
    provider = prepared_artifact_period_provider(artifact_dir)
    first = provider(0)
    second = provider(1)
    assert first.period_index == 0
    assert second.period_index == 1
    assert tuple(bus.bus_id for bus in first.buses) == tuple(bus.bus_id for bus in second.buses)
    assert first.buses[1].demand_mw != second.buses[1].demand_mw
    assert first.source_metadata["artifact_dir"] == str(artifact_dir)


def test_prepared_provider_rejects_unvalidated_or_wrong_period_length(tmp_path):
    artifact_dir = write_small_unvalidated_artifact(tmp_path)
    with pytest.raises(ValueError, match="validated"):
        prepared_artifact_period_provider(artifact_dir)
~~~

The fixture helper should use the same `scenario_package` validation and metadata format as production, not an ad hoc parser-only fixture.

- [ ] **Step 2: Expose the existing prepared-period conversion through a public helper**

In `tools/activsg2000/dcopf.py`, add a public wrapper around the current private `_prepared_period_inputs` conversion:

~~~python
def prepared_period_inputs(
    artifact: PreparedArtifact,
    period_index: int,
) -> tuple[tuple[Bus, ...], tuple[Generator, ...], tuple[Branch, ...], Mapping[str, float]]:
    """Return validated buses, generators, branches, and fixed injections for one period."""
~~~

The wrapper must call `require_validated_block` before conversion and preserve the existing period indexing, per-unit conversion, generator availability, branch limits, DC-line injections, reference bus, and source metadata. Do not duplicate the CSV/parquet parsing logic in the new hosting module.

- [ ] **Step 3: Implement the provider factories**

Add these functions to `tools/hosting_capacity.py`:

~~~python
@dataclass(frozen=True)
class _StaticPeriodProvider:
    periods: tuple[HostingCapacityPeriod, ...]

    def __call__(self, period_index: int) -> HostingCapacityPeriod:
        return _period_at_index(self.periods, period_index)


@dataclass(frozen=True)
class _PreparedArtifactPeriodProvider:
    artifact_dir: str

    def __call__(self, period_index: int) -> HostingCapacityPeriod:
        artifact = _load_cached_artifact(self.artifact_dir)
        return _period_from_prepared_artifact(artifact, period_index)


def static_period_provider(
    period: HostingCapacityPeriod,
) -> Callable[[int], HostingCapacityPeriod]:
    return _StaticPeriodProvider((period,))


def prepared_artifact_period_provider(
    artifact_dir: str | Path,
) -> Callable[[int], HostingCapacityPeriod]:
    _load_cached_artifact(str(artifact_dir))
    return _PreparedArtifactPeriodProvider(str(artifact_dir))


def ieee14_period_provider(
    periods: Sequence[HostingCapacityPeriod] | None = None,
) -> Callable[[int], HostingCapacityPeriod]:
    normalized = tuple(periods) if periods is not None else (_load_builtin_ieee14_period(),)
    return _StaticPeriodProvider(normalized)
~~~

`prepared_artifact_period_provider` must load the artifact once, cache immutable topology data, validate requested indices, and create only the period-specific `Bus`/`Generator`/`Branch` tuples on demand. It must not load a complete 4,368-hour matrix into every worker process. `static_period_provider` must reject indices other than `0` unless explicit repeated periods were supplied.

The built-in providers must be pickleable callable objects rather than nested closures: store the artifact directory or normalized period tuple as instance state and implement `__call__(period_index)`. The matrix driver may accept an arbitrary callable when `workers=1`; when `workers>1`, require a pickleable provider and raise `ValueError("period_provider must be pickleable when workers > 1")` before creating a process pool if serialization fails.

Implement `_load_cached_artifact` with `functools.lru_cache(maxsize=4)` so each worker loads each artifact at most once. The parent process should validate the artifact before starting workers, while each spawned worker lazily opens its own read-only artifact handle.

- [ ] **Step 4: Test adapters and run the existing artifact suite**

~~~bash
python -m pytest tests/test_hosting_capacity_prepared_artifact.py tests/test_activsg2000_validation.py tests/test_scenario_package.py -q
~~~

Expected result: adapter tests and all existing artifact-validation tests pass.

- [ ] **Step 5: Commit the adapter layer**

~~~bash
git add tools/activsg2000/dcopf.py tools/hosting_capacity.py tests/test_hosting_capacity_prepared_artifact.py tests/test_activsg2000_validation.py
git commit -m "Add hosting capacity period adapters"
~~~

## Task 5: Implement the period-by-bus matrix driver and atomic checkpoints

**Files:**

- Modify: `tools/hosting_capacity.py`
- Test: `tests/test_hosting_capacity.py`

- [ ] **Step 1: Define the matrix result and checkpoint tests**

Add tests for the public `compute_hosting_capacity_matrix` signature defined in Task 5. The first test should call it with `period_provider`, optional `bus_ids` and `period_indices`, `solver_backend`, `solver_threads`, `workers`, `checkpoint_dir`, and `output_dir`; the implementation must accept those keyword names exactly.

Tests must verify:

~~~python
def test_matrix_has_deterministic_period_and_bus_order(tmp_path):
    result = compute_hosting_capacity_matrix(
        provider_for_two_periods(),
        period_indices=[1, 0],
        bus_ids=["b2", "b1"],
        solver_backend="highs",
        checkpoint_dir=tmp_path / "checkpoints",
        output_dir=tmp_path / "results",
    )
    assert result.period_indices == (0, 1)
    assert result.bus_ids == ("b1", "b2")
    assert result.capacity_mw.shape == (2, 2)
    assert result.status.shape == (2, 2)
    assert (tmp_path / "results" / "hosting_capacity_mw.npy").exists()
    assert (tmp_path / "results" / "hosting_capacity_metadata.json").exists()


def test_checkpoint_resume_skips_completed_period(tmp_path, monkeypatch):
    first = compute_hosting_capacity_matrix(
        provider_for_two_periods(),
        period_indices=[0],
        solver_backend="highs",
        checkpoint_dir=tmp_path / "checkpoints",
        output_dir=tmp_path / "results",
    )
    calls = []
    monkeypatch.setattr("tools.hosting_capacity.solve_hosting_capacity_at_bus", record_call(calls))
    resumed = compute_hosting_capacity_matrix(
        provider_for_two_periods(),
        period_indices=[0, 1],
        solver_backend="highs",
        checkpoint_dir=tmp_path / "checkpoints",
        output_dir=tmp_path / "results",
    )
    assert resumed.capacity_mw[0].tolist() == first.capacity_mw[0].tolist()
    assert all(period_index == 1 for period_index, _bus_id in calls)
~~~

Add a process-worker test using `workers=2` and compare it elementwise with `workers=1`. The test must not share a live HiGHS/Gurobi object between processes.

- [ ] **Step 2: Run the matrix tests before implementation**

~~~bash
python -m pytest tests/test_hosting_capacity.py -q
~~~

Expected result: matrix and checkpoint tests fail because `HostingCapacityMatrix` and the orchestration function are not yet defined.

- [ ] **Step 3: Implement deterministic matrix orchestration**

Define `HostingCapacityMatrix` with:

~~~python
import numpy as np


@dataclass(frozen=True)
class HostingCapacityMatrix:
    period_indices: tuple[int, ...]
    bus_ids: tuple[str, ...]
    capacity_mw: np.ndarray
    status: np.ndarray
    objective_value: np.ndarray
    solve_time_s: np.ndarray
    metadata: Mapping[str, object]
~~~

Sort and de-duplicate requested period indices and bus IDs before solving. Validate that every requested bus exists in every selected period and that all periods have identical bus order. For each period, create one reusable model and solve all requested buses sequentially; do not rebuild the network model per bus. Use `multiprocessing.get_context("spawn")` for `workers > 1`, pass serializable period-provider arguments to workers, and construct solver models inside each worker. `solver_threads` defaults to `1`; reject `workers < 1` and reject nested parallelism when `workers > 1` and `solver_threads > 1` unless the caller explicitly opts in through a documented argument. After each candidate solve, retain the backend basis/warm-start state when the backend exposes a supported API; if a backend cannot safely reuse it, continue with a clean solve and record `warm_start_used=false` in metadata rather than changing the mathematical problem.

- [ ] **Step 4: Implement checkpoint identity and atomic writes**

For each completed period, write a temporary NumPy/JSON checkpoint in `checkpoint_dir` and replace it with `os.replace`. The checkpoint identity must include:

~~~json
{
  "schema_version": 1,
  "period_indices": [0, 1],
  "bus_ids": ["b1", "b2"],
  "solver_backend": "highs",
  "solver_threads": 1,
  "formulation": "one_bus_additional_demand_v1",
  "source_identity": "sha256 digest computed from the normalized source artifact"
}
~~~

On resume, compare all identity fields and raise `ValueError` on a mismatch. A partially written checkpoint must be ignored unless its metadata and array shapes are valid. Do not infer completion from file existence alone.

- [ ] **Step 5: Implement output serialization**

Write exactly:

~~~text
hosting_capacity_mw.npy
hosting_capacity_status.npy
hosting_capacity_metadata.json
hosting_capacity_summary.csv
hosting_capacity_long.csv.gz   # optional, enabled by a CLI flag
~~~

The summary CSV must include one row per period with `period_index`, `optimal_count`, `failed_count`, `min_hosting_capacity_mw`, `median_hosting_capacity_mw`, and `max_hosting_capacity_mw`. The long form must include `period_index,bus_id,hosting_capacity_mw,status,solve_time_s`. Use UTF-8, stable column order, and no platform-specific path in the result data.

Store `np.nan` in `hosting_capacity_mw.npy` and `objective_value` for non-optimal points, store the exact status string in `hosting_capacity_status.npy`, and exclude non-optimal values from summary statistics. Never coerce a failed or unbounded point to zero.

- [ ] **Step 6: Run matrix, resume, and parallel tests**

~~~bash
python -m pytest tests/test_hosting_capacity.py -q
~~~

Expected result: all single-point, matrix, checkpoint, and process-parity tests pass.

- [ ] **Step 7: Commit the matrix driver**

~~~bash
git add tools/hosting_capacity.py tests/test_hosting_capacity.py
git commit -m "Add resumable hosting capacity matrix driver"
~~~

## Task 6: Add the CLI and benchmark harness

**Files:**

- Create: `src/ercot_game/hosting_capacity_cli.py`
- Modify: `pyproject.toml`
- Create: `tools/benchmarks/benchmark_hosting_capacity.py`
- Test: `tests/test_hosting_capacity.py`

- [ ] **Step 1: Write CLI parsing tests**

Test the parser without running an ACTIVSg2000 solve:

~~~python
def test_hosting_capacity_cli_accepts_prepared_artifact_arguments():
    args = parse_args([
        "--artifact-dir", "data/processed/2028/jan_jun",
        "--horizon", "4368",
        "--solver-backend", "auto",
        "--workers", "4",
        "--output-dir", "results/hosting_capacity",
    ])
    assert args.horizon == 4368
    assert args.solver_backend == "auto"
    assert args.workers == 4
~~~

Test that `--workers 0`, `--solver-threads 0`, negative horizon, and missing mutually exclusive input modes fail with `SystemExit` and a useful parser message.

- [ ] **Step 2: Implement the CLI**

Support these options:

~~~text
--artifact-dir PATH
--ieee14
--horizon INTEGER
--start-period INTEGER
--period-count INTEGER
--bus-id BUS_ID       # repeatable; default all buses
--solver-backend {auto,highs,gurobi}
--solver-threads INTEGER
--workers INTEGER
--checkpoint-dir PATH
--output-dir PATH
--write-long-form
~~~

`--artifact-dir` and `--ieee14` are mutually exclusive. The CLI must derive a normalized period provider, validate the requested range before creating workers, run `compute_hosting_capacity_matrix`, and print a concise JSON summary containing output directory, backend, requested periods, bus count, completed point count, failed point count, wall time, and resume status. It must exit nonzero for invalid input or a failed complete run, but it must preserve partial checkpoints when interrupted.

Add the entry point:

~~~toml
ercot-game-hosting-capacity = "ercot_game.hosting_capacity_cli:main"
~~~

Add `numpy>=1.24,<3` to the main `[project].dependencies` list rather than hiding it in the `rl` extra, because the hosting-capacity result format uses NumPy arrays and `.npy` files.

- [ ] **Step 3: Implement staged benchmark harness**

The benchmark script must accept a provider/artifact path and run exactly these stages: IEEE-14/all buses/1 period; ACTIVSg2000/all buses/1 period; 24 periods; 168 periods; and user-requested full horizon. Record JSON Lines with `stage`, `backend`, `workers`, `solver_threads`, `wall_time_s`, `peak_rss_mb`, `model_build_time_s`, `mean_point_time_s`, `p50_point_time_s`, `p95_point_time_s`, `completed_points`, and `failed_points`. Use `resource.getrusage` where available and label unavailable memory measurements as `null` rather than inventing values.

The harness must never run the full 4,368-hour ACTIVSg2000 workload by default. Full-horizon execution requires an explicit `--full-horizon` flag and a writable output directory.

- [ ] **Step 4: Run CLI and benchmark tests**

~~~bash
python -m pytest tests/test_hosting_capacity.py -q
python -m ercot_game.hosting_capacity_cli --help
~~~

Expected result: parser tests pass and the help text lists all options without importing Gurobi unless selected.

- [ ] **Step 5: Commit the CLI and benchmark harness**

~~~bash
git add src/ercot_game/hosting_capacity_cli.py pyproject.toml tools/benchmarks/benchmark_hosting_capacity.py tests/test_hosting_capacity.py
git commit -m "Add hosting capacity CLI and benchmarks"
~~~

## Task 7: Add numerical, backend, and regression coverage

**Files:**

- Modify: `tests/test_hosting_capacity.py`
- Create: `tests/test_hosting_capacity_regression.py`

- [ ] **Step 1: Add analytical network cases**

Cover the eight required cases with explicit expected statuses or capacities:

1. Uncongested two-bus system: capacity equals generator headroom.
2. One bottleneck branch: capacity equals branch headroom.
3. Generator minimum output: base feasibility and upper capacity respect `p_min_mw`.
4. Parallel paths: both branch limits are respected.
5. Disconnected candidate bus: status is `infeasible_base_case` or `unbounded` according to the constructed base case, never a fabricated finite value.
6. Infeasible base: status is `infeasible_base_case`.
7. Unbounded candidate load: status is `unbounded` when generation and network limits permit it.
8. Invalid nonfinite input: status is `invalid_input` or validation raises before a solve, according to the public API contract.

- [ ] **Step 2: Add backend parity tests**

Reuse the existing optional-Gurobi style:

~~~python
@pytest.mark.skipif(importlib.util.find_spec("gurobipy") is None, reason="gurobipy is not installed")
def test_hosting_capacity_gurobi_matches_highs_on_small_case():
    highs = solve_hosting_capacity_at_bus(_tiny_period(), "b2", solver_backend="highs")
    gurobi = solve_hosting_capacity_at_bus(_tiny_period(), "b2", solver_backend="gurobi")
    assert highs.status == gurobi.status == "optimal"
    assert highs.hosting_capacity_mw == pytest.approx(gurobi.hosting_capacity_mw, abs=1e-7)
~~~

Also test `solver_backend="auto"` by monkeypatching `resolve_solver_backend`, matching `tests/test_dcopf_gurobi_optional.py`.

- [ ] **Step 3: Add non-regression checks for the existing evaluator**

Run the existing DCOPF and batch-evaluation tests without changing their fixtures:

~~~bash
python -m pytest tests/test_dcopf.py tests/test_dcopf_reuse.py tests/test_dcopf_compact_horizon.py tests/test_dcopf_storage_contract.py tests/test_batch_evaluation.py -q
~~~

Expected result: all existing evaluator tests pass, proving that the candidate-demand extension has not changed storage or multi-team semantics.

- [ ] **Step 4: Run the complete test suite and commit**

~~~bash
python -m pytest -q
git add tests/test_hosting_capacity.py tests/test_hosting_capacity_regression.py
git commit -m "Test hosting capacity solver and backend parity"
~~~

## Task 8: Document the solver and exactness boundary

**Files:**

- Create: `docs/hosting_capacity/README.md`

- [ ] **Step 1: Write the documentation**

Document, in this order:

1. Definition: `H_{s,t}` is the maximum additional demand at one bus in one period while existing generation redispatches optimally.
2. What it does not mean: individual capacities are not simultaneously feasible.
3. Included inputs: base load, existing generator limits/costs, branch limits, reference bus, fixed injections, and dataset metadata.
4. Excluded first-release inputs: student-built generators, student-built storage, and intertemporal storage state.
5. Solver behavior: existing DCOPF formulation, candidate load entered with the existing balance sign, reusable period model, HiGHS default, optional Gurobi, and process-level period parallelism.
6. Status meanings and how failed points appear in outputs.
7. Output files and deterministic row/column order.
8. Checkpoint identity and safe resume procedure.
9. IEEE-14 example command.
10. ACTIVSg2000 24-hour smoke-test command.
11. Full-horizon command with an explicit warning that approximately 8.7 million bus-period point solves are expected.
12. Benchmark interpretation: report measured wall time and memory; do not infer production runtime from the IEEE-14 case.

Include this exact example command:

~~~bash
ercot-game-hosting-capacity \
  --artifact-dir data/processed/2028/jan_jun \
  --horizon 4368 \
  --solver-backend auto \
  --workers 4 \
  --output-dir results/hosting_capacity_2028_jan_jun
~~~

State clearly that the first release is exact relative to the supplied LP and solver tolerances; PTDF screening, active-set reuse, bus grouping, and parametric approximations are not silently enabled.

- [ ] **Step 2: Run documentation and packaging checks**

~~~bash
python -m pytest tests/test_student_tutorial_artifacts.py -q
python -m build --wheel --no-isolation
~~~

If the repository does not have the `build` module installed, run the project’s documented package-build command and record the missing optional tool rather than modifying dependencies for this feature.

- [ ] **Step 3: Commit the documentation**

~~~bash
git add docs/hosting_capacity/README.md
git commit -m "Document hosting capacity solver"
~~~

## Task 9: Run measured benchmarks and perform release review

**Files:**

- Modify: `docs/hosting_capacity/README.md` only with measured results
- Create: `docs/hosting_capacity/benchmark-results.jsonl`
- Create: `docs/hosting_capacity/benchmark-report.md`

- [ ] **Step 1: Run the small benchmark sequence**

Run from the repository root:

~~~bash
python tools/benchmarks/benchmark_hosting_capacity.py \
  --ieee14 \
  --period-count 1 \
  --solver-backend highs \
  --workers 1 \
  --output-dir /private/tmp/hosting-capacity-benchmark/ieee14
~~~

Run the prepared-artifact 1-, 24-, and 168-period stages only after confirming the artifact directory is validated and available. Record actual results; never insert an estimate into the report.

- [ ] **Step 2: Run a full-horizon pilot before the production workload**

Run a one-bus and ten-bus pilot for the first 24 periods, then a complete one-period all-bus run. Confirm output shape, checkpoint resume, peak memory, and status distribution before requesting the full 4,368-hour workload. The full run must be explicitly launched by the operator after reviewing the pilot results.

- [ ] **Step 3: Execute the full-horizon benchmark only with explicit approval at implementation time**

Use the exact command recorded in the documentation, with a durable output directory and checkpoint directory. Capture wall time, process count, solver threads, backend version, completed/failed counts, and peak memory. If the workload is interrupted, resume from the verified checkpoint and report both attempts rather than presenting the sum as one uninterrupted timing.

- [ ] **Step 4: Perform final verification**

Run:

~~~bash
python -m pytest -q
python -m ercot_game.hosting_capacity_cli --help
git diff --check
git status --short
~~~

The final review must confirm that no generated benchmark outputs, local solver licenses, prepared datasets, or private filesystem paths were staged. The benchmark report must identify any unavailable full-horizon measurement instead of claiming completion.

- [ ] **Step 5: Commit the measured report**

~~~bash
git add docs/hosting_capacity/README.md docs/hosting_capacity/benchmark-results.jsonl docs/hosting_capacity/benchmark-report.md
git commit -m "Record hosting capacity benchmark results"
~~~

## Final acceptance checklist

- [ ] The single-point result matches the Appendix A DCOPF and reports positive additional load.
- [ ] Existing generator redispatch is allowed; student-built generators and storage are excluded from this first diagnostic.
- [ ] Candidate capacity is computed one bus at a time; no simultaneous-capacity claim is made.
- [ ] HiGHS works without optional dependencies; Gurobi parity is tested when licensed.
- [ ] Reusable and freshly built LPs agree on selected IEEE-14 and ACTIVSg2000 cases.
- [ ] Base infeasibility, unboundedness, invalid input, numerical failure, solver failure, and interruption are distinguishable.
- [ ] Period input is normalized and validated before a large run.
- [ ] Checkpoints are atomic and reject mismatched artifact identity, bus order, horizon, solver configuration, or formulation version.
- [ ] Output arrays, metadata, summary CSV, and optional long CSV have deterministic schemas.
- [ ] Process workers construct their own solver models; no solver object crosses a process boundary.
- [ ] Existing batch evaluation, storage dispatch, fair projection, and RL tests pass unchanged.
- [ ] Full-horizon runtime and memory are measured, not guessed.
- [ ] The repository contains no generated solver output or license material from the implementation.
