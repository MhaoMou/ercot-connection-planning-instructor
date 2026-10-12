# RL Gym Phase 5 Research Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven development to implement this plan task-by-task with review checkpoints.

**Goal:** Add reproducible release metadata, manifest hashing, executable examples, and release documentation around the validated Phase 1--4 RL APIs.

**Architecture:** Keep `rl_gym` as a thin wrapper over the existing evaluator. Add one focused provenance module, extend replay metadata compatibly, place examples under `examples/rl_gym`, and document the release contract in `rl_gym/README.md` and `rl_gym/RELEASE.md`. No dispatch or scoring logic changes.

**Tech Stack:** Python 3.11+, standard-library `json`/`hashlib`/`dataclasses`, existing RL fallback spaces, pytest.

---

### Task 1: Release metadata and canonical manifest hashing

**Files:**
- Create: `rl_gym/release.py`
- Modify: `rl_gym/__init__.py`
- Test: `tests/test_rl_gym_release.py`

- [ ] **Step 1: Write failing tests** for a release manifest containing
  `release_version`, `environment_version`, `plan_schema_version`,
  `scenario_year`, `scenario_block`, `evaluator_version`, and optional rule/config
  hashes. Test that mapping key order does not change the digest, that
  `to_dict()` is JSON serializable, and that unsupported years/blocks are
  rejected through the existing calendar contract.

- [ ] **Step 2: Run the focused tests and verify they fail** with missing-module
  or missing-symbol errors:

  ```bash
  /opt/anaconda3/bin/python -m pytest tests/test_rl_gym_release.py -q
  ```

- [ ] **Step 3: Implement `rl_gym/release.py`** with:
  - constants `RELEASE_VERSION`, `ENVIRONMENT_VERSION`, and
    `PLAN_SCHEMA_VERSION`;
  - `canonical_json(value)` using sorted keys, compact separators, and UTF-8;
  - `stable_hash(value)` returning a lowercase SHA-256 hex digest;
  - frozen `ReleaseManifest` with `to_dict()`, `from_dict()`, and `digest()`;
  - `make_release_manifest(...)` validating scenario metadata through
    `half_year_calendar` and accepting only JSON-compatible rule/config metadata.

- [ ] **Step 4: Export the public constants, class, and functions from
  `rl_gym/__init__.py`.** Do not change existing exports.

- [ ] **Step 5: Run the focused tests and the existing RL tests.** Expected:
  `tests/test_rl_gym_release.py` passes and no Phase 1--4 behavior changes.

---

### Task 2: Replay and environment provenance integration

**Files:**
- Modify: `rl_gym/replay.py`
- Modify: `rl_gym/env.py`
- Modify: `rl_gym/parallel_env.py`
- Modify: `tests/test_rl_gym_phase3.py`
- Modify: `tests/test_rl_gym_phase4.py`

- [ ] **Step 1: Add failing tests** that a single-agent trace can carry a
  `ReleaseManifest`, that replay validation rejects a mismatched environment
  version, and that parallel reset/terminal metadata exposes only sanitized
  release identifiers and hashes.

- [ ] **Step 2: Run the focused tests to confirm the new assertions fail.**

- [ ] **Step 3: Extend replay manifest construction** with an optional release
  manifest serialized as ordinary JSON. Preserve loading of existing replay
  files that lack the field. When a target environment has a release manifest,
  validate equality before consuming actions.

- [ ] **Step 4: Add an optional `release_manifest` constructor argument to both
  environments.** Store it defensively and expose only its public metadata in
  `reset`/terminal info; never include plans, scores, projection diagnostics, or
  evaluator internals.

- [ ] **Step 5: Run Phase 1--4 and new provenance tests.** Confirm old replay
  files still load and existing privacy/order/terminal tests remain unchanged.

---

### Task 3: Executable research examples

**Files:**
- Create: `examples/rl_gym/single_agent_baseline.py`
- Create: `examples/rl_gym/competitive_parallel.py`
- Create: `examples/rl_gym/README.md`
- Test: `tests/test_rl_gym_examples.py`

- [ ] **Step 1: Write smoke tests** that execute each example with a temporary
  output path and assert a compact JSON summary is produced without invoking a
  solver.

- [ ] **Step 2: Implement the single-agent example** using the released
  `GreedyPolicy`, `run_episode`, `save_replay`, and `make_release_manifest`.
  It must accept `--year`, `--block`, `--seed`, and `--replay-out`; use no
  evaluator by default; and print only JSON-serializable summary fields.

- [ ] **Step 3: Implement the parallel example** with two deterministic teams,
  private add/finish actions, and no evaluator by default. Print team IDs,
  rewards, dispatch statuses, and aggregate counts only; never print plans or
  raw evaluator results.

- [ ] **Step 4: Document installation, command examples, evaluator injection,
  supported half-year blocks, and the fact that examples are smoke/research
  scaffolds rather than official classroom scoring tools.

- [ ] **Step 5: Run both examples from the repository root and run their tests.**

---

### Task 4: Release documentation and final verification

**Files:**
- Create: `rl_gym/RELEASE.md`
- Modify: `rl_gym/README.md`
- Modify: `pyproject.toml` only if package discovery needs the examples or
  metadata exposed as package data
- Test: `tests/test_rl_gym_release.py`

- [ ] **Step 1: Replace stale Phase 5 deferred wording** with the implemented
  release surface, while retaining clearly marked future work for hourly
  operation and full training algorithms.

- [ ] **Step 2: Write `rl_gym/RELEASE.md`** covering installation, supported
  scenarios, version fields, manifest/hash semantics, replay compatibility,
  example commands, privacy boundary, solver/evaluator boundary, and known
  limitations.

- [ ] **Step 3: Add documentation assertions** for release versioning, manifest
  provenance, both examples, and explicit non-goals.

- [ ] **Step 4: Run the complete RL suite, documentation tests, examples, and
  formatting checks:**

  ```bash
  /opt/anaconda3/bin/python -m pytest tests/test_rl_gym*.py tests/test_documentation_consistency.py -q
  git diff --check
  ```

- [ ] **Step 5: Perform a final review** confirming that no evaluator, DCOPF,
  storage, projection, or network files were modified by Phase 5.
