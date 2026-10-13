# Exact Multi-Storage Benders Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the instructor evaluator's memory-intensive storage-coupled horizon LP with an exact multi-storage Benders route while preserving dispatch, feasibility, ownership, terminal-SOC, and scoring semantics.

**Architecture:** Keep `solve_dcopf_horizon` as the small-fixture reference implementation. Generalize the existing one-storage Benders solver in `tools/activsg2000/dcopf.py` so the master owns one power/SOC trajectory per storage asset and each hourly reusable DCOPF subproblem receives the aggregate storage injection. Route prepared batch evaluations containing storage through the generalized solver; retain the monolithic path behind an explicit reference option for parity tests and debugging.

**Tech Stack:** Python 3, HiGHS/highspy, dataclasses, `ThreadPoolExecutor`, pytest, existing ACTIVSg2000 prepared-artifact adapter, Electrify for long-running validation.

---

## Files and responsibilities

- Modify `tools/activsg2000/dcopf.py`: extract the existing one-storage Benders machinery into a generalized multi-storage implementation; add deterministic diagnostics and preserve the existing public single-storage API as a compatibility wrapper.
- Modify `tools/activsg2000/plan_adapter.py`: prepare multi-team storage assets and route the central storage path to Benders by default, with an explicit `storage_solver="monolithic"` reference option.
- Modify `src/ercot_game/batch_evaluation.py`: pass the storage solver option and preserve unresolved results as evaluation failures rather than scores.
- Modify `tests/test_dcopf.py`: add failing parity, ownership, SOC, parallel, deduplication, and unresolved-status tests before implementation.
- Modify `tests/test_plan_adapter_storage.py`: add failing adapter routing and multi-storage attribution tests.
- Modify `tests/test_batch_evaluation.py`: add failing official-score rejection coverage for unresolved storage dispatch.
- Create `docs/superpowers/reports/2026-10-13-multi-storage-benders-benchmark.md`: record local parity and Electrify benchmark evidence only after implementation and verification.

### Task 1: Lock the generalized solver contract with tests

**Files:** `tests/test_dcopf.py`, `tools/activsg2000/dcopf.py`

- [ ] **Step 1: Write failing tests for two-storage parity.** Add a small two-bus, four-hour fixture with two storage assets on distinct buses and one owner-load mapping per team. Call the new API as:

```python
result = solve_dcopf_horizon_benders(
    periods=periods,
    storage_assets=(storage_a, storage_b),
    owner_load_mw_by_team={"A": owner_a, "B": owner_b},
    return_result=True,
)
reference = solve_dcopf_horizon(
    periods=periods,
    storage_assets=(storage_a, storage_b),
    owner_load_mw_by_team={"A": owner_a, "B": owner_b},
)
assert result.status == reference.status == "optimal"
assert [p.objective_cost for p in result.periods] == pytest.approx(
    [p.objective_cost for p in reference.periods]
)
assert result.final_soc_mwh_by_storage == pytest.approx(reference.final_soc_mwh_by_storage)
```

- [ ] **Step 2: Run only the new test and verify the expected red failure.**

```bash
pytest tests/test_dcopf.py -k "two_storage_parity" -q
```

Expected: FAIL because the existing Benders implementation rejects more than one storage asset.

- [ ] **Step 3: Add failing tests for owner isolation and terminal SOC.** Assert that each storage's discharge is capped by its own team's bus load, that both assets finish at least `0.5 * energy_mwh`, and that changing team B's load does not alter team A's owner cap.

- [ ] **Step 4: Add failing tests for status and diagnostics.** Use `max_iterations=1` and `wall_time_limit_seconds=0` and assert `DCOPFUnresolved`, never an optimal result. Define the expected diagnostic fields: route, iterations, recourse solves, cuts, objective gap, and elapsed time.

- [ ] **Step 5: Run the new tests and confirm they fail for missing generalized behavior, not test errors.**

### Task 2: Generalize the Benders master and recourse interface

**Files:** `tools/activsg2000/dcopf.py`, `tests/test_dcopf.py`

- [ ] **Step 1: Introduce a stable result diagnostic type next to `DCOPFHorizonResult`.** Use immutable fields with defaults so existing callers remain valid:

```python
@dataclass(frozen=True)
class DCOPFBendersDiagnostics:
    route: str = "benders"
    iterations: int = 0
    recourse_solves: int = 0
    optimality_cuts: int = 0
    feasibility_cuts: int = 0
    lower_bound: float | None = None
    upper_bound: float | None = None
    relative_gap: float | None = None
    elapsed_seconds: float = 0.0

@dataclass(frozen=True)
class DCOPFHorizonResult:
    status: str
    periods: tuple[DCOPFResult, ...]
    final_soc_mwh_by_storage: dict[str, float]
    diagnostics: DCOPFBendersDiagnostics | None = None
```

Preserve positional construction compatibility by appending `diagnostics` only at the end.

- [ ] **Step 2: Replace scalar Benders state with deterministic storage tuples.** Normalize `storage_assets` sorted by `storage_id`, validate unique IDs, and build:

```python
storage_ids = tuple(storage.storage_id for storage in ordered_storage_assets)
power_columns[storage_id][period_index]
soc_columns[storage_id][period_index]
```

Use one SOC transition row per `(storage_id, period_index)` and keep the existing initial-SOC and terminal-SOC rules exactly.

- [ ] **Step 3: Build each recourse injection from the whole master point.** For period `t`, sum each storage's signed power at its bus, preserving fixed injections:

```python
injections = dict(fixed[t])
for storage in ordered_storage_assets:
    injections[storage.bus_id] = injections.get(storage.bus_id, 0.0) + point[storage.storage_id, t]
```

Use one reusable `ReusableDCOPFModel` per worker and the fixed topology from the first period. Do not allocate one network model per hour.

- [ ] **Step 4: Generalize dual optimality cuts.** For each storage `s` at period `t`, use the balance dual at `s.bus_id` as the subgradient contribution; combine contributions when multiple storage assets share a bus. Add one cut on `theta[t]` and all `p[s,t]` variables:

```python
theta[t] - sum(gradient[s] * power[s, t] for s in storage_ids) >=
    recourse_cost - sum(gradient[s] * point[s, t] for s in storage_ids)
```

Use the existing HiGHS row API and preserve its sign convention, verified by the parity tests.

- [ ] **Step 5: Generalize feasible-bound tightening.** Probe each storage bus independently only for physically valid bounds, retain the current owner-load upper bound, and never use heuristic bounds in the official path. If a recourse point is infeasible, tighten the relevant storage bounds and rerun the master.

- [ ] **Step 6: Add deterministic cut deduplication.** Normalize coefficients and RHS by rounding to a fixed tolerance, key by `(period_index, normalized coefficients, normalized rhs)`, and skip an already-seen cut. Add the corresponding test to prove objective equivalence.

- [ ] **Step 7: Run the focused DCOPF tests and fix implementation errors until green.**

```bash
pytest tests/test_dcopf.py -q
```

### Task 3: Preserve compatibility and verify parallel execution

**Files:** `tools/activsg2000/dcopf.py`, `tests/test_dcopf.py`

- [ ] **Step 1: Make the one-storage public call delegate to the generalized implementation.** Keep all existing keyword arguments and error messages compatible where tests depend on them.

- [ ] **Step 2: Add serial/parallel equivalence tests for two storage assets.** Run with `parallel_workers=1` and `parallel_workers=2`; compare status, objective, SOC, generation, and flow within existing tolerances.

- [ ] **Step 3: Add a worker-model reuse test.** Monkeypatch `ReusableDCOPFModel.__init__` and assert construction count is at most the worker count, independent of horizon length.

- [ ] **Step 4: Ensure deadline checks close the executor.** On `DCOPFUnresolved`, call `shutdown(wait=True)` and re-raise without leaving worker threads alive.

- [ ] **Step 5: Run the focused suite.**

```bash
pytest tests/test_dcopf.py -k "benders or storage" -q
```

### Task 4: Route the plan adapter without changing plan semantics

**Files:** `tools/activsg2000/plan_adapter.py`, `tests/test_plan_adapter_storage.py`

- [ ] **Step 1: Add a failing route test.** Monkeypatch the Benders and monolithic solver entry points and assert a batch with storage calls Benders; a batch without storage remains on the existing hourly route.

- [ ] **Step 2: Add explicit adapter parameters.** Extend `evaluate_plans_with_prepared_dcopf` and `_evaluate_batch_with_central_storage` with:

```python
storage_solver: str = "benders"
storage_wall_time_limit_seconds: float | None = None
storage_max_iterations: int = 200
```

Validate values and accept only `"benders"` and `"monolithic"`.

- [ ] **Step 3: Materialize all storage assets with team ownership.** Build `StorageDispatchAsset` entries using stable IDs, bus locations, power/energy capacities, and initial SOC. Build `owner_load_mw_by_team` from the submitted site load at each period. Do not expose storage actions in the student CSV.

- [ ] **Step 4: Call the generalized prepared Benders wrapper.** Add `solve_prepared_horizon_benders` beside `solve_prepared_horizon` in `tools/activsg2000/dcopf.py`, reusing `_prepared_period_inputs` and adding the submitted demand/generator data per period.

- [ ] **Step 5: Attribute dispatch back to teams.** Preserve `BatchPeriodResult` fields, map storage charge/discharge/SOC by owner, and keep generation attribution unchanged.

- [ ] **Step 6: Keep `storage_solver="monolithic"` as a reference-only option.** Use it only for small fixtures and explicit diagnostics; do not silently fall back to it after a Benders timeout.

- [ ] **Step 7: Run adapter tests.**

```bash
pytest tests/test_plan_adapter_storage.py -q
```

### Task 5: Enforce official unresolved handling in the batch evaluator

**Files:** `src/ercot_game/batch_evaluation.py`, `tests/test_batch_evaluation.py`

- [ ] **Step 1: Add a failing test that a storage dispatch timeout is not scored.** The result must be recorded as unresolved/evaluation failure and must not receive the infeasible fallback score or a valid team score.

- [ ] **Step 2: Extend `_dispatch_plans` with storage solver controls.** Pass `storage_solver`, `storage_wall_time_limit_seconds`, and `storage_max_iterations` while retaining the compatibility fallback for test doubles that do not accept new keywords.

- [ ] **Step 3: Preserve current fair continuous projection behavior only for true joint infeasibility.** A Benders numerical/time-limit termination must remain unresolved; it must not trigger projection or a score.

- [ ] **Step 4: Add instructor-facing diagnostics to the existing evaluation metadata without including another team's private plan.**

- [ ] **Step 5: Run the batch suite.**

```bash
pytest tests/test_batch_evaluation.py -q
```

### Task 6: Add parity, route, and regression coverage

**Files:** `tests/test_dcopf.py`, `tests/test_plan_adapter_storage.py`, `tests/test_batch_evaluation.py`

- [ ] **Step 1: Test two teams with two storages on the same bus.** Verify aggregate injection is correct and each team's owner-load cap is applied independently.

- [ ] **Step 2: Test terminal SOC and initial SOC overrides.** Compare Benders and monolithic period-by-period SOC values.

- [ ] **Step 3: Test no-storage route regression.** Ensure no-storage evaluations do not instantiate Benders or storage state variables.

- [ ] **Step 4: Test malformed storage dimensions and duplicate IDs.** Preserve existing validation errors.

- [ ] **Step 5: Run all relevant unit tests.**

```bash
pytest tests/test_dcopf.py tests/test_plan_adapter_storage.py tests/test_batch_evaluation.py -q
```

### Task 7: Document solver behavior and benchmark harness

**Files:** `docs/COLAB_DCOPF_BENCHMARK.md`, `docs/superpowers/reports/2026-10-13-multi-storage-benders-benchmark.md`, `experiments/colab/accelerated_retry.py`

- [ ] **Step 1: Document that Benders is exact, not a rolling-horizon approximation.** Explain the master/subproblem split, convergence certificate, unresolved status, and reference monolithic route.

- [ ] **Step 2: Add a benchmark mode that records route, horizon, number of assets, wall time, peak RSS, iterations, recourse solves, and final status.** Keep the full benchmark opt-in; do not run it during ordinary tests.

- [ ] **Step 3: Add the report template with explicit `PASS`, `UNRESOLVED`, or `NOT RUN` fields.** Do not populate a full-horizon claim until Electrify returns a result.

### Task 8: Verification and remote benchmark

**Files:** no new production files; update benchmark report only with measured results.

- [ ] **Step 1: Run formatting/static checks and the focused test suite.**

```bash
python -m compileall tools/activsg2000 src/ercot_game
pytest tests/test_dcopf.py tests/test_plan_adapter_storage.py tests/test_batch_evaluation.py -q
```

- [ ] **Step 2: Run the existing native Gymnasium tests to confirm the evaluator integration did not break the environment.**

```bash
pytest tests/test_rl_gym.py tests/test_rl_gym_native.py -q
```

- [ ] **Step 3: Sync only the implementation and benchmark files to Electrify.** Run a small two-storage parity benchmark first, then the 4,368-hour and 4,416-hour storage-enabled benchmarks with an explicit wall-time limit. Record actual status and peak memory.

- [ ] **Step 4: If the full run is unresolved, preserve the evidence and report the exact remaining bottleneck; do not claim release readiness.**

- [ ] **Step 5: Run `git diff --check`, inspect `git diff --name-only`, and commit only the scoped solver/adapter/test/docs files.** Leave unrelated student tutorial and generated instructor artifacts untouched.

## Self-review

- Spec coverage: generalized master, exact cuts, feasibility handling, reuse/parallelism, diagnostics, routing, unresolved handling, tests, and remote acceptance are covered in Tasks 1–8.
- Placeholder scan: all implementation steps name concrete files, APIs, commands, and expected behavior; no `TBD`, `TODO`, or “appropriate handling” placeholders are used.
- Type consistency: `DCOPFBendersDiagnostics` is appended to `DCOPFHorizonResult`; adapter options use the same names throughout routing and batch evaluation; `storage_solver` has the values `"benders"` and `"monolithic"` in every task.
