# Prompt metadata

- Prompt ID: `three-bus-implementation`
- Module: Three-bus DCOPF implementation
- Sources: `README.md`, `docs/student_manual/README.md`

## Goal

Provide a minimal, executable three-bus DCOPF implementation that students can inspect before using repository-scale data.

## Context supplied to the AI

Use a fully specified synthetic three-bus fixture, not fabricated ACTIVSg2000 rows. The repository package and evaluator remain authoritative for production behavior. Distinguish verified facts, interpretations, and unknowns.

## Prompt

Act as a student-facing AI teaching assistant. Write executable Python for a three-bus DCOPF fixture that prints solver status, objective, generation, branch flows, and nodal balance values. Include file paths, units, a small assertion test, and a clear path to later adaptation without claiming the toy solution is an ACTIVSg2000 result. Distinguish verified facts, interpretations, and unknowns and end with a verification checklist.

## Expected evidence

The fixture must have finite values, satisfied balance and line limits, a fixed reference angle, and a solver status that is checked rather than assumed.

## Privacy constraints

Use synthetic public fixture data only. Never embed team-specific plans or private evaluation outputs.
