# Task 6 Fair Projection Repair Implementation Plan

> **For agentic workers:** Implement this plan task-by-task with TDD and fresh verification before completion.

**Goal:** Replace the heuristic Task 6 projection with a deterministic, certified-to-tolerance continuous optimizer and correct fairness, caps, and MWh diagnostics.

**Architecture:** Keep `_dispatch_plans` as the sole feasibility oracle. Normalize valid plans once with explicit caps, then solve the alpha vector using monotone branch-and-bound for total requested MWh and a second branch-and-bound for proportional-fair utility on the total-optimal band. Keep all integration and isolation boundaries in `batch_evaluation.py` unchanged.

**Tech Stack:** Python standard library, existing pytest fixtures, existing dispatch adapter; no new dependency.

---

### Task 1: Lock the failure cases with red tests

**Files:**
- Modify: `tests/test_batch_evaluation.py`

- [ ] Add a three-team coupled-oracle test for pairwise `alpha_i + alpha_j <= 1`, expecting total load `1.5`, fractions `0.5` each, and positive finite PF utility.
- [ ] Add a non-greedy coupled-oracle test whose optimum is not any greedy coordinate endpoint.
- [ ] Add a test where the total-optimal face supports positive service for all eligible teams and assert no team is dropped.
- [ ] Add a test where no positive all-team total-optimal vector exists and assert the diagnostic utility is `-math.inf`, not `0.0`.
- [ ] Add a 10,000-versus-1 MW request test asserting the capped/projected oversized request cannot exceed the small request's cap or projected service.
- [ ] Add a `time_step_hours=2.0` test asserting requested/projected/total diagnostics are MWh with the factor applied.

### Task 2: Verify the new tests fail for the base implementation

**Files:**
- Test: `tests/test_batch_evaluation.py`

- [ ] Run `PYTHONPATH=.:src pytest -q tests/test_batch_evaluation.py -k 'projection'`.
- [ ] Confirm failures are caused by the greedy candidate search, zeroed `-inf` utility, weak cap behavior, and missing time-step parameter rather than fixture errors.

### Task 3: Implement deterministic total-load branch-and-bound

**Files:**
- Modify: `src/ercot_game/batch_evaluation.py`

- [ ] Replace `_projection_orders`, `max_component`, the two-team ternary search, and pairwise grid updates with a cached oracle evaluator and a deterministic box queue.
- [ ] Use lower/upper corner certification from coordinatewise downward closure, objective upper bounds, deterministic widest-dimension splitting, and the explicit absolute/relative total-load tolerance.
- [ ] Seed the incumbent with zero and a positive common-service bisection when available, while returning `None` only when the zero endpoint itself is unavailable.
- [ ] Preserve continuous alpha scaling and keep every oracle dispatch free of integer or binary curtailment.

### Task 4: Implement proportional-fair tie-break, caps, and MWh diagnostics

**Files:**
- Modify: `src/ercot_game/batch_evaluation.py`

- [ ] Apply the fixed site cap and equalized smallest-positive-request cap before optimization; record all policy values in `cap_policy`.
- [ ] Run a second certified box search over the total-load-optimal tolerance band using sum-log served MWh, with positive common-service seeding and deterministic tie-breaking.
- [ ] Preserve `-math.inf` when no positive total-optimal vector exists; never convert it to zero.
- [ ] Add `time_step_hours` to the projection boundary, validate it, pass `rules.time_step_hours` from batch evaluation, and multiply all projection load diagnostics by it.

### Task 5: Run focused tests and refactor only after green

**Files:**
- Modify: `tests/test_batch_evaluation.py` only if assertion wording/API fixtures need alignment.

- [ ] Run `PYTHONPATH=.:src pytest -q tests/test_batch_evaluation.py -k 'projection'` and fix production code until green.
- [ ] Run the complete `tests/test_batch_evaluation.py` and `tests/test_round_coordinator.py` suites.
- [ ] Remove dead heuristic helpers and keep existing compatibility tuple behavior green.

### Task 6: Audit, full verify, and commit

**Files:**
- No Task 7, benchmark, or manual files.

- [ ] Run `PYTHONPATH=.:src pytest -q` and record the exit status and test count.
- [ ] Run `git diff --check` and inspect `git diff -- src/ercot_game/batch_evaluation.py tests/test_batch_evaluation.py docs/superpowers/specs/2026-10-08-task-6-fair-projection-repair-design.md docs/superpowers/plans/2026-10-08-task-6-fair-projection-repair.md`.
- [ ] Confirm pre-existing instructor-manual edits remain untouched and commit only the Task 6 repair files.
- [ ] Report the exact branch-and-bound algorithm, downward-closure/tolerance limitation, cap policy, test commands, and commit hash.

