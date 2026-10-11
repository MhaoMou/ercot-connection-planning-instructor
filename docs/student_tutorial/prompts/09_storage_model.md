# Prompt metadata

- Prompt ID: `storage-model`
- Module: Storage model
- Sources: `README.md`, `docs/student_manual/README.md`

## Goal

Explain storage power capacity, energy capacity, dispatch, and state of charge without confusing submission fields with hourly rows.

## Context supplied to the AI

Storage is submitted as power and energy capacity in the canonical site-level CSV and dispatched by the evaluator. Use a small synthetic time sequence for illustration; do not claim its dispatch is official. Distinguish verified facts, interpretations, and unknowns.

## Prompt

Act as a student-facing AI teaching assistant. Give executable Python for a tiny storage state-of-charge example with explicit MW/MWh units, state transitions, charge/discharge bounds, nonnegative state, and terminal-state checks. Require a clear repository-relative file path (for example, `work/tutorial/storage_soc_check.py`) and a small assertion/test that runs before any larger case. Explain what the CSV records versus what the evaluator dispatches. Do not fabricate efficiency, initial state, or evaluator parameters; distinguish verified facts, interpretations, and unknowns and provide a verification checklist.

## Expected evidence

The example must show that discharge is bounded by stored energy and that every assumed parameter is labeled as an illustrative assumption or verified repository fact.

## Privacy constraints

Use synthetic public examples only. Do not reveal private capacity plans or instructor-only dispatch results.
