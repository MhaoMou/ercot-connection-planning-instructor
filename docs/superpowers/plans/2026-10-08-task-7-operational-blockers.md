# Task 7 Operational Blockers Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Resume classroom evaluation from the persisted next round and replace only the classroom writer's known generated output namespace before writing privacy-safe artifacts.

**Architecture:** Keep GameState and evaluate_round as the sequencing and transactional boundaries. evaluate_classroom will validate the released schedule, load the state, slice the unprocessed suffix, and aggregate only that suffix; the CLI will match returned results by round_key. write_classroom_outputs will remove a fixed list of files/directories under its requested root, then recreate the privacy-safe tree.

**Tech Stack:** Python 3, pytest, pathlib, dataclasses, existing GameStateStore and classroom evaluator/writer APIs.

---

### Task 1: Add failing coordinator resume and idempotence tests

**Files:**
- Modify: tests/test_round_coordinator.py near the existing classroom coordinator tests

- [ ] Step 1: Add test_classroom_resume_starts_at_persisted_next_round_without_replay.

Construct all six ClassroomRoundInput objects. Use an evaluator that records Path(args[0]).name, returns _UsableEvaluation for the first call, and raises RuntimeError("simulated interruption") for the second call. Assert the first invocation records submissions-0 and submissions-1. Invoke evaluate_classroom again with the same six inputs and a non-failing evaluator; assert it records submissions-1 through submissions-5, never submissions-0, and the final state has next_round_key equal to None.

- [ ] Step 2: Run the new test to verify RED.

Run: pytest -q tests/test_round_coordinator.py::test_classroom_resume_starts_at_persisted_next_round_without_replay

Expected: FAIL because the current second invocation sends 2027_jan_jun to evaluate_round while persisted state expects 2027_jul_dec.

- [ ] Step 3: Add test_classroom_resume_is_idempotent_after_all_rounds_are_complete.

Evaluate all six rounds once, save GameStateStore(state_path).load(), then call evaluate_classroom again with an evaluator that raises AssertionError if called. Assert result.submissions == (), result.state equals the saved complete state, and no evaluator call occurs.

- [ ] Step 4: Run both new tests to verify the intended RED failures.

Run: pytest -q tests/test_round_coordinator.py::test_classroom_resume_starts_at_persisted_next_round_without_replay tests/test_round_coordinator.py::test_classroom_resume_is_idempotent_after_all_rounds_are_complete

Expected: both fail because the current function replays the first manifest item.

### Task 2: Implement suffix-only classroom resume

**Files:**
- Modify: src/ercot_game/round_coordinator.py in evaluate_classroom

- [ ] Step 1: Load the persisted state once after existing prefix validation.

Use GameStateStore(state_path), load an existing state or GameState.new() for a missing file, and compute completed_count = len(loaded_state.completed_rounds).

- [ ] Step 2: Slice only pending inputs and preserve the existing single-round transaction loop.

Set pending_inputs = round_inputs[completed_count:]. If it is empty, return ClassroomEvaluationResult(submissions=(), weekly_rankings=(), final_rankings=(), state=loaded_state). Otherwise call evaluate_round for each pending input exactly as today. Keep existing released-prefix validation, weekly grouping, ranking, weekly reset, predecessor checks, and evaluator kwargs unchanged. Update the docstring to document suffix-only results and completed-state no-op behavior.

- [ ] Step 3: Run focused coordinator verification.

Run: pytest -q tests/test_round_coordinator.py::test_classroom_resume_starts_at_persisted_next_round_without_replay tests/test_round_coordinator.py::test_classroom_resume_is_idempotent_after_all_rounds_are_complete tests/test_round_coordinator.py::test_classroom_coordinator_evaluates_all_six_rounds_and_ranks_only_scored_weeks tests/test_round_coordinator.py::test_second_submission_is_rejected_before_loading_or_evaluating_without_predecessor

Expected: PASS.

### Task 3: Add the failing CLI suffix-alignment regression

**Files:**
- Modify: tests/test_batch_cli.py near the existing classroom CLI test

- [ ] Step 1: Add test_classroom_cli_writes_only_resumed_suffix.

Monkeypatch _classroom_round_inputs to return six inputs with distinct artifact directory names. Monkeypatch evaluate_classroom to return one fake round result for 2027_jul_dec with submission_number 2. Monkeypatch write_classroom_outputs to record output_dir and scenario_version. Invoke main with classroom arguments and assert the writer receives week-1/submission-2, uses the second manifest artifact version, and stdout says summary: 1 half-year submission.

- [ ] Step 2: Run the new CLI test to verify RED.

Run: pytest -q tests/test_batch_cli.py::test_classroom_cli_writes_only_resumed_suffix

Expected: FAIL because positional zip associates the returned second-round result with the first manifest input and the CLI reports six submissions.

### Task 4: Implement CLI result alignment and suffix summary

**Files:**
- Modify: src/ercot_game/batch_cli.py in _run_classroom
- Modify: tests/test_batch_cli.py existing full-schedule classroom test if its fake result assumes zero returned submissions

- [ ] Step 1: Index manifest inputs by round_key and iterate only over result.submissions.

Create round_inputs_by_key from round_inputs, reject duplicate manifest keys, and for every returned round result retrieve the matching input by round_result.round_key. Raise ValueError for an unknown returned key. Use the matched input for horizon, scenario version, and source-period metadata. Do not write outputs for completed inputs absent from result.submissions.

- [ ] Step 2: Report the processed suffix count.

Set the summary count from len(result.submissions), using half-year submission for one and half-year submissions otherwise. Preserve the existing six-submission wording when all six rounds are processed.

- [ ] Step 3: Run classroom CLI tests.

Run: pytest -q tests/test_batch_cli.py -k classroom

Expected: PASS.

### Task 5: Add the failing stale-output cleanup regression

**Files:**
- Modify: tests/test_batch_evaluation.py near test_classroom_outputs_are_aggregate_shared_and_team_private

- [ ] Step 1: Add test_classroom_outputs_replace_only_known_namespace.

Before write_classroom_outputs, create evaluation.json, evaluation_sha256.txt, leaderboard.csv, submission_manifest.csv, dispatch/old.json, diagnostics/old.json, shared/stale.json, and teams/team-old/summary.json under the requested output root. Also create unrelated keep.txt. After writing, assert every stale named artifact is gone, new shared/summary.json and team summary files exist, and keep.txt is unchanged.

- [ ] Step 2: Run the cleanup test to verify RED.

Run: pytest -q tests/test_batch_evaluation.py::test_classroom_outputs_replace_only_known_namespace

Expected: FAIL because the current writer leaves stale raw and classroom artifacts in place.

### Task 6: Implement exact namespace cleanup

**Files:**
- Modify: src/ercot_game/batch_evaluation.py near _remove_tracked_generated_files and write_classroom_outputs

- [ ] Step 1: Define the exact namespace.

Import shutil. Define _CLASSROOM_OUTPUT_FILES = ("leaderboard.csv", "submission_manifest.csv", "evaluation.json", "evaluation_sha256.txt") and _CLASSROOM_OUTPUT_DIRECTORIES = ("dispatch", "diagnostics", "shared", "teams").

- [ ] Step 2: Add _clear_classroom_output_namespace(root).

For each exact file path, unlink files and symlinks; for each exact directory path, unlink symlinks/files or shutil.rmtree directories; ignore absent paths. Never discover deletion targets through rglob. Call the helper immediately after root.mkdir(parents=True, exist_ok=True) and before recreating shared and teams.

- [ ] Step 3: Run cleanup and privacy tests.

Run: pytest -q tests/test_batch_evaluation.py::test_classroom_outputs_replace_only_known_namespace tests/test_batch_evaluation.py::test_classroom_outputs_are_aggregate_shared_and_team_private

Expected: PASS.

### Task 7: Verify, diff-check, and commit

**Files:**
- Verify: src/ercot_game/round_coordinator.py
- Verify: src/ercot_game/batch_cli.py
- Verify: src/ercot_game/batch_evaluation.py
- Verify: tests/test_round_coordinator.py
- Verify: tests/test_batch_cli.py
- Verify: tests/test_batch_evaluation.py

- [ ] Step 1: Run focused regressions.

Run: pytest -q tests/test_round_coordinator.py -k classroom tests/test_batch_cli.py -k classroom tests/test_batch_evaluation.py -k classroom_outputs

Expected: PASS with zero failures.

- [ ] Step 2: Run the full suite.

Run: pytest -q

Expected: PASS with zero failures.

- [ ] Step 3: Check whitespace, scope, and pre-existing dirty files.

Run: git diff --check; git status --short; git diff --stat 934bc31..HEAD

Expected: no whitespace errors; only scoped source/tests/plan changes are added by this task; pre-existing docs/instructor_manual artifacts remain untouched.

- [ ] Step 4: Commit the implementation.

Run: git add src/ercot_game/round_coordinator.py src/ercot_game/batch_cli.py src/ercot_game/batch_evaluation.py tests/test_round_coordinator.py tests/test_batch_cli.py tests/test_batch_evaluation.py docs/superpowers/plans/2026-10-08-task-7-operational-blockers.md

Then run: git commit -m "Repair Task 7 resume and output cleanup"

Expected: one scoped implementation commit.
