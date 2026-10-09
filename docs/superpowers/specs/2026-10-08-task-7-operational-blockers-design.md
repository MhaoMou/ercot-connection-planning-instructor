# Task 7 Operational Blockers Design

## Goal

Repair persisted classroom resume behavior and make privacy-safe classroom output replacement deterministic and narrowly scoped, without changing economics, DCOPF, projection, or manuals.

## Resume behavior

`evaluate_classroom` will validate the caller-provided manifest against the complete released six-round sequence, load the persisted `GameState` once, and select only the suffix beginning at `state.next_round_key`. Completed rounds will never be replayed. A missing state starts at round one; a partially completed state evaluates the next round through the end of the supplied schedule; a complete state returns an empty suffix result without changing state. The returned `ClassroomEvaluationResult` and CLI artifacts describe only rounds processed by that invocation. `evaluate_round` remains the single-round transaction boundary, preserving weekly ledger resets, predecessor checks, and idempotent state commits.

The CLI will keep loading all six manifest entries so the manifest remains the schedule contract, then pair each returned result with its matching input by `round_key` rather than positional zip. It will report the number of newly processed half-year submissions. The documented resume contract is suffix-only: previously evaluated results are not reconstructed from the state file.

## Privacy output namespace

`write_classroom_outputs` will clear only its own known generated namespace inside the exact requested output directory before writing. The namespace includes the raw batch artifacts `leaderboard.csv`, `submission_manifest.csv`, `evaluation.json`, `evaluation_sha256.txt`, `dispatch/`, and `diagnostics/`, plus classroom artifacts `shared/` and `teams/`. Removal will operate on those exact paths, tolerate absent paths, and leave unrelated files in the output directory untouched. The writer will then create only the privacy-safe shared and team outputs and their hash manifest.

## Testing

Add focused regressions for:

1. interruption after an earlier committed round and resume from the next round without replaying it;
2. preservation of six-round validation, predecessor rejection, weekly reset behavior, and idempotent completed-state handling;
3. CLI result/output alignment for a resumed suffix and suffix-only summary;
4. stale raw output removal plus preservation of an unrelated user file in the requested output directory.

Run the focused tests and the full suite, inspect the final diff/status, and commit only the scoped source/tests/design changes. Existing dirty instructor-manual artifacts remain untouched.
