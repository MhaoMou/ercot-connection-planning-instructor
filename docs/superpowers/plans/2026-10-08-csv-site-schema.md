# Canonical Site-Level CSV Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the seven-column one-row-per-site CSV the default student submission format and validation contract.

**Architecture:** `csv_submission.py` owns strict canonical parsing and isolates the historical long-form parser. `csv_validator.py` performs user-facing issue collection against the canonical schema and delegates successful semantic parsing to the canonical parser. The package-independent wrappers invoke the same validator.

**Tech Stack:** Python 3.11, dataclasses, standard-library `csv`, pytest.

---

### Task 1: Add red tests for the canonical parser

**Files:**
- Modify: `tests/test_csv_submission.py`

- [ ] Add helpers that write the exact seven-column header and canonical rows.
- [ ] Add tests for a valid multi-site file with generator and storage capacity, zero generator/storage capacity, and `submission_id is None`.
- [ ] Add tests asserting rejection of an old header, missing/extra columns, duplicate site rows, malformed IDs, nonnumeric/NaN/infinite/negative capacities, generator capacity above connection capacity, and storage dimensions that violate the zero-or-four-hour rule.
- [ ] Add a malformed-quoting and oversized-field test using the new header.

### Task 2: Run parser red tests

**Files:**
- Test: `tests/test_csv_submission.py`

- [ ] Run `pytest tests/test_csv_submission.py -q`.
- [ ] Confirm new canonical tests fail because the current parser expects hourly rows and legacy columns, while unrelated catalog tests remain diagnostic context.

### Task 3: Implement canonical parsing and compatibility projection

**Files:**
- Modify: `src/ercot_game/csv_submission.py`

- [ ] Define canonical and clearly named legacy column constants, ID validation, finite-number parsing, capacity validation, and the four-hour storage check.
- [ ] Make `read_student_csv` parse exactly the canonical header by default, without using `horizon` or requiring complete hourly coverage.
- [ ] Build `SitePlan` rows and deterministic compatibility `ConnectionPlan`, `GeneratorPlan`, and `StoragePlan` views for the existing evaluator API.
- [ ] Keep `CSVSubmission.submission_id` typed as optional and return `None` for canonical rows.
- [ ] Move the old long-form implementation behind an explicitly named legacy parser used only by historical callers/tests; do not make it the default validation route.
- [ ] Preserve catalog loaders and byte-level SHA-256 behavior.

### Task 4: Run parser green tests and refactor

**Files:**
- Test: `tests/test_csv_submission.py`

- [ ] Run `pytest tests/test_csv_submission.py -q` and fix implementation failures without weakening tests.
- [ ] Refactor only after the focused parser suite is green; rerun it after each cleanup.

### Task 5: Add red tests for validator and CLI

**Files:**
- Modify: `tests/test_csv_validator.py`

- [ ] Replace canonical validator fixtures with seven-column site rows.
- [ ] Assert valid multi-site summaries report sites, generators, and storage assets without a horizon requirement.
- [ ] Assert the validator rejects the legacy header, missing/extra columns, duplicates, malformed IDs, bad capacities, generator overflow, and invalid storage dimensions with actionable messages and a valid-format example.
- [ ] Assert the CLI accepts a compact multi-site file even when a large horizon is supplied and emits the seven-column format in its guidance.

### Task 6: Run validator red tests

**Files:**
- Test: `tests/test_csv_validator.py`

- [ ] Run `pytest tests/test_csv_validator.py -q`.
- [ ] Confirm canonical tests fail against the old validator and that failures identify missing implementation rather than test setup errors.

### Task 7: Implement canonical validator and checker wrapper

**Files:**
- Modify: `src/ercot_game/csv_validator.py`
- Create: `tools/check_submission.py`
- Preserve: `scripts/check_submission.py`

- [ ] Validate exact canonical headers and canonical row semantics, without complete-horizon checks.
- [ ] Reuse the parser’s rules while collecting multiple row-level issues where safe.
- [ ] Update report fields/messages from hourly periods/buses to site rows and include a compact valid CSV example.
- [ ] Add `tools/check_submission.py` as a package-independent wrapper to the same `main` entry point.

### Task 8: Run focused and full verification

**Files:**
- Test: `tests/test_csv_submission.py`, `tests/test_csv_validator.py`, full test suite

- [ ] Run focused parser and validator tests.
- [ ] Run the full pytest suite and preserve compatibility for existing non-CSV model/evaluator callers.
- [ ] Run `git diff --check`.
- [ ] Inspect the final diff for scope violations, then commit the implementation and tests.
