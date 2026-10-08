# Task 4 Storage Dispatch Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair centralized multi-period storage dispatch so SOC, operating mode, and team/site ownership constraints are enforced by the actual DCOPF formulation.

**Architecture:** Keep the existing HiGHS path and use its supported binary variables for exact charge/discharge mode selection; add a terminal SOC lower bound and per-team/per-bus discharge rows. Preserve the plan adapter, persisted SOC semantics, scoring, and non-artifact scheduler.

**Tech Stack:** Python 3.11+, pytest, HiGHS through `highspy` when installed.

---

### Task 1: Add solver-independent storage contract tests

**Files:**
- Create: `tests/test_dcopf_storage_contract.py`

- [x] **Step 1: Write tests for solver-boundary validation and ownership grouping**

  Add tests that call `solve_dcopf_horizon` with invalid initial SOC values below half-full, above capacity, non-finite values, and invalid dimensions, asserting `ValueError` before any solver import is required. Add a test that exposes the internal owner grouping helper and verifies assets are grouped by `(team_id, bus_id)` rather than team-only; include a group with no same-team load and assert its cap is zero.

- [x] **Step 2: Run the new structural tests and verify the intended failures**

  Run `python -m pytest -q tests/test_dcopf_storage_contract.py`. The validation tests should fail because the current horizon solver accepts initial SOC below half-full; the grouping test should fail because no per-team/per-bus helper exists.

### Task 2: Implement the LP storage formulation and validation

**Files:**
- Modify: `tools/activsg2000/dcopf.py:58-70, 487-794`

- [x] **Step 1: Add the smallest solver-boundary validation changes**

  Reject non-finite or out-of-range initial SOC unless `0.5 * energy_mwh <= initial_soc_mwh <= energy_mwh`. Keep omitted persisted SOC behavior outside this solver API unchanged.

- [x] **Step 2: Add binary operating-mode columns alongside charge/discharge columns**

  Import HiGHS’ integer variable type and create a binary `mode` column for each storage/period, with `mode=1` meaning discharge. Add `charge + power * mode <= power` and `discharge - power * mode <= 0`, keep the existing ideal SOC balance and discharge-availability row, and give the final SOC column a lower bound of `0.5 * energy_mwh` with upper bound `energy_mwh`.

- [x] **Step 3: Add per-team/per-bus discharge caps**

  Normalize owner loads as currently, group storage assets by `(team_id, bus_id)`, and add one row per group/period limiting the sum of each group’s positive discharge to that team’s same-bus owner load. Keep the owner-load argument required for storage-bearing solves.

- [x] **Step 4: Preserve the existing result fields from solved charge/discharge columns**

  Return the existing rounded nonnegative charge/discharge maps from their respective columns and preserve all result keys and final SOC exposure.

- [x] **Step 5: Run structural tests and focused existing tests**

  Run `python -m pytest -q tests/test_dcopf_storage_contract.py tests/test_dcopf.py tests/test_plan_adapter_storage.py tests/test_asset_state.py tests/test_round_coordinator.py`. Expected: structural tests and all available solver-backed tests pass; solver-backed tests may be skipped only if `highspy` is unavailable.

### Task 3: Add solver-backed regressions and documentation

**Files:**
- Modify: `tests/test_dcopf.py`
- Modify: `tools/activsg2000/dcopf.py` docstrings/comments as needed

- [x] **Step 1: Add failing solver-backed tests**

  Add tests for an initial SOC below half-full being rejected, terminal SOC remaining at least half-full after profitable discharge pressure, no period returning both positive charge and discharge, and cross-bus/cross-team owner loads not permitting discharge at a bus without same-team load.

- [x] **Step 2: Run the tests to verify the regressions fail before the implementation**

  Run the targeted node IDs with `python -m pytest -q tests/test_dcopf.py -k 'soc or charge or owner or team or bus'`. Confirm failures are constraint failures rather than collection or import errors; if `highspy` is unavailable, retain these tests under the existing module-level skip and rely on structural tests locally.

- [x] **Step 3: Update solver documentation without changing the student manual**

  State that the centralized storage-bearing solve uses HiGHS binary mode variables to prohibit simultaneous charge/discharge, and that this makes those horizons MIPs requiring `highspy`.

- [x] **Step 4: Run focused tests after the implementation**

  Re-run the structural and solver-backed focused command and inspect the full output for failures and unexpected warnings.

### Task 4: Full verification, diff audit, and commit

**Files:**
- Verify all modified files; do not include the pre-existing instructor-manual build artifacts.

- [x] **Step 1: Run the full test suite**

  Run `python -m pytest -q`. Expected: zero failures; only tests explicitly guarded by missing `highspy` may be skipped.

- [x] **Step 2: Audit the diff and status**

  Run `git diff --check`, `git diff --stat`, and `git status --short`. Confirm only the solver, tests, documentation, and workflow design/plan files are changed; leave existing generated manual files untouched.

- [x] **Step 3: Commit the implementation**

  Run `git add tools/activsg2000/dcopf.py tests/test_dcopf.py tests/test_dcopf_storage_contract.py docs/student_manual/HYPERPARAMETERS.md docs/superpowers/specs/2026-10-08-task-4-storage-dispatch-repair-design.md docs/superpowers/plans/2026-10-08-task-4-storage-dispatch-repair.md && git commit -m "Repair Task 4 storage dispatch constraints"`.

- [x] **Step 4: Verify the commit and report concrete semantics**

  Run `git status --short --branch` and `git show --stat --oneline HEAD`. Report the signed-LP semantics, initial/terminal SOC bounds, per-team/per-bus ownership coupling, test counts/skips, commit hash, and any highspy availability limitation.
