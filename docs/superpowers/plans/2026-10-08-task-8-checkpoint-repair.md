# Task 8 Checkpoint Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Task 8 benchmark checkpoints safe to reuse only for the exact benchmark configuration and recover CSV rows with the types required by recommendation logic.

**Architecture:** Put deterministic signature construction, checkpoint row coercion, and signature filtering in a small standard-library helper so the behavior is directly unit-testable. The notebook will construct a configuration containing artifact/manifest identity, horizon and storage policy, variants, solver/model revision, and sampler settings; it will stamp every row and ignore rows with missing or mismatched signatures. Documentation will describe stale invalidation and CSV recovery.

**Tech Stack:** Python standard library, Jupyter notebook JSON, pytest, Markdown.

---

### Task 1: Add failing checkpoint regression tests

**Files:**
- Modify: `tests/test_colab_benchmark_package.py`
- Create: `experiments/colab/checkpointing.py`

- [ ] **Step 1: Add the CSV recovery and signature regression tests**

Test that CSV-loaded `horizon_hours`, integer model counts, wall/memory/P95 fields, and optional booleans have native types, then pass the recovered optimal full-horizon sparse-reuse row to the notebook `recommend()` AST fixture and expect the verified recommendation. Test that a row written with a configuration whose `storage_max_horizon` is `720` is not selected for the otherwise identical default configuration whose cap is `None`.

- [ ] **Step 2: Run the focused tests and confirm the expected failure**

Run: `pytest -q tests/test_colab_benchmark_package.py`

Expected: the new tests fail because the helper module, typed recovery, and configuration-aware reuse do not yet exist.

### Task 2: Implement typed recovery and signature matching

**Files:**
- Create: `experiments/colab/checkpointing.py`

- [ ] **Step 1: Implement canonical signature hashing**

Add a function that serializes a JSON-compatible configuration with sorted keys and compact separators and returns a SHA-256 hex digest.

- [ ] **Step 2: Implement tolerant typed CSV row coercion**

Convert known horizon/count fields to `int`, timing/memory/P95/sampler fields to `float`, and known boolean fields from `true`/`false` or `1`/`0`; convert empty typed cells to `None` while preserving status and identity strings.

- [ ] **Step 3: Implement JSON-first/CSV-fallback loading and signature filtering**

Load JSON rows when valid and otherwise load CSV rows, coercing both paths consistently. Return only rows whose `case_key` is expected and whose row-level `configuration_signature` exactly equals the active signature.

- [ ] **Step 4: Re-run the focused tests**

Run: `pytest -q tests/test_colab_benchmark_package.py`

Expected: helper-level tests pass; notebook source-contract tests for the integration remain failing until the notebook is updated.

### Task 3: Make notebook checkpointing configuration-aware

**Files:**
- Modify: `experiments/colab/half_year_dcopf_benchmark.ipynb`

- [ ] **Step 1: Build the full benchmark configuration**

Add artifact path plus manifest/scenario metadata hashes, official horizons, variants, storage policy and cap, repeat/worker settings, solver/model revision fields, and RSS sampler settings to `_configuration()`. Compute one canonical `CONFIGURATION_SIGNATURE` from it.

- [ ] **Step 2: Stamp and persist the signature**

Add `configuration_signature` to measured and skipped rows and to the JSON checkpoint envelope. Use the helper’s JSON-first/CSV-fallback loader and write the current signature with every atomic checkpoint.

- [ ] **Step 3: Filter reuse by signature**

Update `run_benchmark()` so only rows with the current signature and expected case keys enter `existing`. Treat absent signatures and mismatches as stale and recompute them; print a concise stale-row count when one is found. Preserve the existing per-case checkpointing and explicit skip behavior.

- [ ] **Step 4: Re-run the focused tests**

Run: `pytest -q tests/test_colab_benchmark_package.py`

Expected: all notebook contract, configuration-regression, CSV-recovery, and recommendation tests pass.

### Task 4: Update benchmark documentation and verify the repository

**Files:**
- Modify: `experiments/colab/README.md`
- Modify: `docs/COLAB_DCOPF_BENCHMARK.md`

- [ ] **Step 1: Document checkpoint identity and recovery**

State that a checkpoint is reusable only when its configuration signature matches artifact/manifest identity, horizons, storage policy/cap, variants, solver/model revision, and sampler settings; otherwise rows are stale and recomputed. State that CSV fallback restores typed numeric/boolean values before recommendation filtering.

- [ ] **Step 2: Run focused and full tests**

Run: `pytest -q tests/test_colab_benchmark_package.py` and `pytest -q`.

Expected: both commands exit 0 without generating benchmark results.

- [ ] **Step 3: Run diff checks and commit only scoped changes**

Run `git diff --check` and inspect `git status --short`. Stage only the helper, notebook, benchmark docs, tests, and this plan; preserve all pre-existing instructor-manual edits/generated files. Commit with `git commit -m "Repair Task 8 checkpoint reuse"`.
