# DCOPF Colab Acceleration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make repeated hourly DCOPF evaluation practical on Colab without changing the official 4,368-hour and 4,416-hour game horizons.

**Architecture:** Add a reusable single-period HiGHS model that keeps the static network matrix and variable layout alive while changing only hourly balance bounds and generator bounds. Keep the existing public `solve_dcopf` API as the correctness reference, and use the reusable path only in the benchmark/evaluator path after equivalence tests pass. Separate solver timing from memory instrumentation, reduce redundant benchmark cases, and validate a continuous storage formulation before using it for long horizons.

**Tech Stack:** Python, HiGHS/highspy, pytest, Jupyter/Google Colab.

---

### Task 1: Add failing tests for reusable-model equivalence

**Files:**
- Create: `tests/test_dcopf_reuse.py`
- Inspect: `tools/activsg2000/dcopf.py`

- [ ] **Step 1: Write tests** for a reusable solver matching `solve_dcopf` on objective, status, generation, angles, flows, and balance duals across two different demand vectors, and for a reusable model rejecting changed topology inputs.

- [ ] **Step 2: Run the focused tests** and confirm they fail because the reusable solver API does not exist.

Run: `pytest tests/test_dcopf_reuse.py -q`

Expected: collection/import failure naming the missing reusable-model symbol.

### Task 2: Implement the reusable single-period DCOPF model

**Files:**
- Modify: `tools/activsg2000/dcopf.py`
- Test: `tests/test_dcopf_reuse.py`

- [ ] **Step 1: Add an immutable prepared-model class** that builds the HiGHS variables and static coefficient matrix once, stores balance-row indices, and exposes `solve(demand_by_bus, fixed_injection_mw_by_bus=None)`.

- [ ] **Step 2: Update balance row bounds and generator bounds in place** before each `highs.run()`; retain the HiGHS object and basis between solves where highspy supports it.

- [ ] **Step 3: Keep result reconstruction optional** through `return_result=True`; benchmark timing uses status/objective only while production callers receive the existing `DCOPFResult` shape.

- [ ] **Step 4: Run the focused tests** and confirm equivalence tests pass.

Run: `pytest tests/test_dcopf_reuse.py -q`

Expected: all focused tests pass.

### Task 3: Add benchmark helpers using model reuse

**Files:**
- Modify: `experiments/colab/half_year_dcopf_benchmark.ipynb`
- Modify: `experiments/colab/README.md`
- Test: `tests/test_colab_benchmark_package.py`

- [ ] **Step 1: Add a reusable hourly benchmark function** that prepares one model per artifact and updates only hourly inputs.

- [ ] **Step 2: Remove `tracemalloc` and continuous RSS sampling from timed solver calls**; retain a separate optional memory probe for representative cases.

- [ ] **Step 3: Add an optimized variant name** such as `model_reuse` and document that it is the intended long-horizon measurement.

- [ ] **Step 4: Run package/notebook integrity tests.**

Run: `pytest tests/test_colab_benchmark_package.py -q`

Expected: package tests pass and the notebook contains the optimized path.

### Task 4: Validate storage continuous formulation before use

**Files:**
- Modify: `tools/activsg2000/dcopf.py`
- Modify: `tests/test_dcopf_storage_contract.py`
- Create: `tests/test_dcopf_storage_lp_equivalence.py`

- [ ] **Step 1: Add a failing equivalence test** showing that, under nonnegative generation costs and the existing storage constraints, an LP storage solve can produce the same optimal objective as the binary-mode formulation on representative cases.

- [ ] **Step 2: Implement a selectable continuous storage mode** without changing the existing default contract.

- [ ] **Step 3: Run storage contract and equivalence tests.**

Run: `pytest tests/test_dcopf_storage_contract.py tests/test_dcopf_storage_lp_equivalence.py -q`

Expected: existing contract tests remain green; the new equivalence tests pass.

### Task 5: Reduce the official Colab benchmark matrix

**Files:**
- Modify: `experiments/colab/half_year_dcopf_benchmark.ipynb`
- Modify: `experiments/colab/README.md`

- [ ] **Step 1: Make the default matrix measure** 720-hour `model_reuse`, 4,368-hour `model_reuse`, 4,416-hour `model_reuse`, a parallel comparison at 720 hours, and one representative storage case.

- [ ] **Step 2: Keep cold serial and broad short-horizon repeats opt-in** through environment variables, not in the default long run.

- [ ] **Step 3: Preserve checkpoint compatibility** and record the variant, solver revision, highspy version, and artifact hashes.

### Task 6: Verify locally without running the long benchmark

**Files:**
- No new files.

- [ ] **Step 1: Run focused solver and benchmark tests.**

Run: `pytest tests/test_dcopf_reuse.py tests/test_dcopf_storage_contract.py tests/test_colab_benchmark_package.py -q`

- [ ] **Step 2: Run the full local regression suite if dependencies permit.**

Run: `pytest -q`

- [ ] **Step 3: Do not run the 4,368/4,416-hour benchmark locally; prepare a fresh Colab bundle instead.**

### Task 7: Retry the optimized benchmark in Colab

**Files:**
- Create/update: `/private/tmp/ercot_colab_benchmark_bundle.zip`

- [ ] **Step 1: Build a minimal bundle from the changed solver, benchmark notebook, tests, and required scenario artifacts.**

- [ ] **Step 2: Upload the bundle to the existing Colab notebook and run setup.**

- [ ] **Step 3: Run the reduced optimized benchmark matrix in Colab only.**

- [ ] **Step 4: Verify the checkpoint CSV/JSON and report measured wall time, status, and memory for each completed case.**

### Task 8: Commit only acceleration files

**Files:**
- Modify only the solver, benchmark, focused tests, and benchmark documentation.

- [ ] **Step 1: Review `git diff` and exclude unrelated existing LaTeX/generated changes.**

- [ ] **Step 2: Run final focused verification.**

- [ ] **Step 3: Commit with message:** `Accelerate long-horizon DCOPF benchmarking`.

