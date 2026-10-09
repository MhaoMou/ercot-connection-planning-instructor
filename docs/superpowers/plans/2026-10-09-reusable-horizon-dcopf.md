# Reusable Horizon DCOPF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse one fixed-topology, fixed-horizon storage-coupled HiGHS LP across repeated solves while preserving the existing `solve_dcopf_horizon` API and dispatch semantics.

**Architecture:** Add a `ReusableDCOPFHorizonModel` that assembles the horizon LP once, records balance and owner-load rows plus mutable variable bounds, and exposes `solve(...)` for updating per-solve data before reoptimization. The existing function becomes a compatibility wrapper that constructs one model and solves it once. Reuse is restricted to an explicit structural signature; topology or asset changes require a new model.

**Tech Stack:** Python, HiGHS/highspy, pytest, existing `Bus`, `Generator`, `Branch`, and `StorageDispatchAsset` dataclasses.

---

### Task 1: Specify reuse behavior with tests

**Files:**
- Modify: `tests/test_dcopf_storage_contract.py`
- Test: existing storage contract suite

- [ ] Add tests that build a two-period one-bus storage model, solve it twice with different demand/owner-load right-hand sides, and verify both results are optimal.
- [ ] Add a test that changes the structural period topology and verifies the reusable model rejects it.
- [ ] Add a test that the compatibility wrapper still returns the same result shape.
- [ ] Run the focused tests and confirm the new tests fail because the reusable class does not yet exist.

### Task 2: Implement the reusable horizon model

**Files:**
- Modify: `tools/activsg2000/dcopf.py`

- [ ] Extract horizon LP construction into `ReusableDCOPFHorizonModel`.
- [ ] Store row indices for per-period balance equations and owner-load caps.
- [ ] Store mutable bounds for fixed injections, owner-load limits, and any plan-dependent variable bounds.
- [ ] Keep storage SOC recurrence, terminal half-full constraint, signed storage power, and team ownership constraints unchanged.
- [ ] Expose `solve(...)` that updates rows/bounds, runs HiGHS, and reconstructs the existing `DCOPFHorizonResult`.
- [ ] Make `solve_dcopf_horizon` construct the reusable model and delegate one solve to it.

### Task 3: Integrate the Colab benchmark

**Files:**
- Modify: `experiments/colab/accelerated_retry.py`

- [ ] Reuse one horizon model for repeated storage benchmark calls with the same horizon and storage signature.
- [ ] Keep no-storage hourly benchmarking unchanged.
- [ ] Report build time separately from repeat solve time.

### Task 4: Verify

- [ ] Run focused storage tests.
- [ ] Run the full available test suite.
- [ ] Run AST parsing and `git diff --check`.
- [ ] Upload the updated patch to Colab and run the smallest storage benchmark supported by the script.
- [ ] Record whether 24-hour storage completes and report any remaining bottleneck honestly.
