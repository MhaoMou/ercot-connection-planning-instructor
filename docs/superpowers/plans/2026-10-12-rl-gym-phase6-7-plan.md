# RL Gym Phase 6–7 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven development with specification and code-quality review after every task.

**Goal:** Finish the approved research-readiness roadmap around the stable Phase 1–5 RL release and prepare, but do not silently implement, hourly operation.

**Architecture:** Add independent experiment-facing modules around the existing `rl_gym` APIs. Multi-agent replay consumes `ParallelConnectionPlanningEnv`; experiment reports consume replay/benchmark APIs; the training scaffold consumes the single-agent environment. No domain/evaluator code is duplicated.

**Tech Stack:** Python 3.11+, standard-library JSON/dataclasses/pathlib, existing NumPy/Gymnasium optional boundary, pytest.

---

### Task 1: Multi-agent replay

**Files:** Create `rl_gym/parallel_replay.py`; modify `rl_gym/__init__.py`; create `tests/test_rl_gym_parallel_replay.py`.

- [ ] Define JSON-safe `ParallelEpisodeManifest`, `ParallelEpisodeStep`, and `ParallelEpisodeTrace` with sorted team IDs, release manifest identity, per-team public data, and aggregate metadata.
- [ ] Implement `run_parallel_episode`, `save_parallel_replay`, `load_parallel_replay`, and `replay_parallel_episode`.
- [ ] Reject missing/extra teams, changed team order, manifest mismatch, malformed terminal steps, and tampered rewards before action replay.
- [ ] Add privacy tests proving plans, team-keyed projection data, and raw evaluator output are absent.
- [ ] Run all RL tests and `git diff --check`.

### Task 2: Reproducible experiment runner

**Files:** Create `rl_gym/experiments.py`; create `examples/rl_gym/run_experiments.py`; create `tests/test_rl_gym_experiments.py`.

- [ ] Define an experiment configuration containing policy name, years, blocks, team count, episodes, seed, and optional evaluator boundary.
- [ ] Run deterministic policy episodes over selected half-year blocks and emit JSON-safe records containing manifest digest, timing, rewards, statuses, and aggregate counts.
- [ ] Ensure default experiments are solver-free and never expose private plan state.
- [ ] Add a CLI and tests for deterministic repeated runs and invalid configuration handling.

### Task 3: Baseline comparison reports

**Files:** Create `rl_gym/reporting.py`; create `examples/rl_gym/compare_baselines.py`; create `tests/test_rl_gym_reporting.py`.

- [ ] Aggregate experiment records by policy, scenario block, and team count.
- [ ] Report means, standard deviations, termination rates, action counts, and latency summaries without inventing score semantics.
- [ ] Preserve raw manifest digests and distinguish `not_evaluated` from evaluator-backed results.
- [ ] Add a compact JSON/Markdown report CLI and tests for empty, single-policy, and mixed-status inputs.

### Task 4: Training scaffold

**Files:** Create `examples/rl_gym/train_policy.py`; create `rl_gym/training.py`; create `tests/test_rl_gym_training.py`; update `rl_gym/RELEASE.md` and `examples/rl_gym/README.md`.

- [ ] Implement a dependency-light policy-loop trainer with deterministic seeds, configurable episodes, and JSON output metadata.
- [ ] Provide a learner callback seam rather than hard-coding a third-party RL algorithm.
- [ ] Save only public observations/actions/rewards and release metadata; do not serialize private multi-team state.
- [ ] Add tests for deterministic output, callback invocation, and invalid training configuration.

### Task 5: Final Phase 6 integration and Phase 7 gate

**Files:** Modify `rl_gym/README.md`; create `rl_gym/PHASE7_HOURLY_GATE.md`; modify/add focused tests.

- [ ] Document the implemented Phase 6 APIs and commands.
- [ ] Document the explicit Phase 7 hourly-operation design gate and acceptance criteria.
- [ ] Run the complete RL/documentation test matrix and `git diff --check`.
- [ ] Inspect the diff to confirm no evaluator, dispatch, projection, scoring, or network changes.
