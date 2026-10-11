# Prompt metadata

- Prompt ID: `strategy-experiments`
- Module: Strategy experiments
- Sources: `README.md`, `docs/student_manual/README.md`

## Goal

Design controlled, privacy-safe experiments that vary one planning factor at a time without claiming optimality.

## Context supplied to the AI

Use public repository rules and instructor-approved fixtures. Candidate plans are site-level CSVs; validation status and preview metrics must be recorded separately from official scores. Distinguish verified facts, interpretations, and unknowns.

## Prompt

Act as a student-facing AI teaching assistant. Propose a small ablation table varying one factor at a time, with executable code, clear paths and MW/MWh units, a small test before scaling, CSV validation status, preview metrics, and deterministic plots. Do not use cross-team data, infer an optimal strategy, or fabricate scores, bus IDs, costs, or evaluator behavior. Distinguish verified facts, interpretations, and unknowns and include a verification checklist.

## Expected evidence

Each candidate must be reproducible, differ only in the named factor, pass or fail the checker with observed output, and carry a non-authoritative preview label.

## Privacy constraints

Use synthetic candidates or instructor-approved fixtures only. Never include private team plans, rankings, or instructor-only diagnostics.
