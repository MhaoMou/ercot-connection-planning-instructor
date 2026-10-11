# Task 3 Quality-Review Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline execution selected by the user) to implement this plan task-by-task with verification checkpoints.

**Goal:** Harden tutorial execution and validation, convert the three-bus example to a real solver-backed DCOPF, add notebook IDs/dependency/docs, and commit the verified changes.

**Architecture:** The runner executes source notebooks in a temporary run root and publishes a complete result set only after success. The validator owns the canonical strategy CSV contract. The three-bus notebook imports the existing `Bus`, `Generator`, `Branch`, and `solve_dcopf` API, keeping tutorial math aligned with production code.

**Tech Stack:** Python 3.11, nbclient, nbformat, pytest, HiGHS via highspy, matplotlib.

---

### Task 1: Add regression tests for runner safety and notebook identity

**Files:**
- Modify: `tests/test_student_tutorial_notebooks.py`
- Modify: `tests/test_student_tutorial_artifacts.py`

- [ ] Add tests that call the runner with the same path and nested input/output paths and assert nonzero exit plus an overlap error.
- [ ] Add a deterministic-rerun test that executes the same inputs twice into separate directories and compares notebook JSON plus all expected artifact bytes.
- [ ] Add a test that every notebook cell has a non-empty unique string `id`.
- [ ] Run the focused tests and observe the new behavior tests fail before implementation.

### Task 2: Harden `execute_notebooks.py`

**Files:**
- Modify: `docs/student_tutorial/scripts/execute_notebooks.py`

- [ ] Add a path helper that rejects equal paths and either path being a parent of the other after `resolve()`.
- [ ] Create a temporary run root beside the destination, execute with `resources.metadata.path` set to an isolated working directory, and set `TUTORIAL_OUTPUT_ROOT` to a run-local artifact directory.
- [ ] Do not mutate the parent process environment; pass a copied environment through `NotebookClient(..., resources=...)`/kernel manager environment support.
- [ ] Execute all sources before touching the final destination; write notebooks and artifacts into the temporary run tree, then replace the destination contents only after all notebooks pass.
- [ ] Ensure stale `.ipynb` files and artifacts cannot remain in a successful destination by publishing a clean complete tree.
- [ ] Run the runner safety and smoke tests.

### Task 3: Tighten CSV validation and add the tutorial dependency

**Files:**
- Modify: `docs/student_tutorial/scripts/validate_tutorial_examples.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_student_tutorial_notebooks.py`

- [ ] Require `reader.fieldnames == CANONICAL_COLUMNS`.
- [ ] Reject rows whose raw field count is not exactly seven, missing/extra fields, blank IDs, non-finite numeric capacity values, or negative capacities.
- [ ] Add malformed-header, malformed-row, and negative/non-numeric capacity tests.
- [ ] Add `matplotlib>=3.8,<4` to `[project.optional-dependencies].tutorial`.
- [ ] Run validator-focused tests.

### Task 4: Convert the three-bus notebook to genuine DCOPF

**Files:**
- Modify: `docs/student_tutorial/notebooks/03_three_bus_dcopf.ipynb`

- [ ] Import the project solver types and define a three-bus demand profile, two generators, and two branches with finite limits.
- [ ] Call `solve_dcopf(buses=..., generators=..., branches=...)` and assert optimal status, generator bounds, nodal balances, `theta_reference == 0`, branch flow equation `b * base_mva * (theta_from - theta_to)`, and thermal limits.
- [ ] Serialize status, objective, generation, angles, flows, balance residuals, and a note identifying the result as the solver-backed educational fixture.
- [ ] Run the notebook smoke test and inspect the generated JSON.

### Task 5: Add explicit IDs and clarify preview semantics

**Files:**
- Modify: all seven files under `docs/student_tutorial/notebooks/*.ipynb`
- Modify: `tests/test_student_preview.py` if needed for the wording contract

- [ ] Assign stable unique string IDs to every cell in every notebook without changing cell order or existing artifact names.
- [ ] Update the preview markdown/environment output to say `ACTIVSG2000_ARTIFACT_DIR` is informational unless explicitly consumed by a preview integration; preserve current fixture-only behavior.
- [ ] Run notebook metadata and preview tests.

### Task 6: Full verification and commit

**Files:**
- All intended files from Tasks 1–5.

- [ ] Run `pytest -q tests/test_student_tutorial_notebooks.py tests/test_student_tutorial_artifacts.py tests/test_student_preview.py tests/test_csv_validator.py`.
- [ ] Run `pytest -q` for the full repository suite.
- [ ] Review `git diff --check`, `git status`, and the staged diff; preserve unrelated untracked LaTeX artifacts.
- [ ] Commit with `git commit -m "Harden Task 3 tutorial quality review"`.
