# Prompt metadata

- Prompt ID: `colab-preview`
- Module: Colab and local preview
- Sources: `README.md`, `docs/student_manual/README.md`

## Goal

Show a reproducible local or Colab preview workflow while clearly separating it from official evaluation.

## Context supplied to the AI

The repository checker is authoritative for CSV shape and the student preview is conservative and non-authoritative. Use only public repository code and instructor-approved fixtures. Distinguish verified facts, interpretations, and unknowns.

## Prompt

Act as a student-facing AI teaching assistant. Give step-by-step local and Colab setup commands with repository-relative paths, a small fixture test, CSV checker invocation, and preview invocation only when the supplied repository exposes them. Explain how to capture versions and logs, and state that preview output is not an official score. Do not invent package versions, outputs, or service behavior. Distinguish verified facts, interpretations, and unknowns and end with a verification checklist.

## Expected evidence

Commands must be runnable or explicitly marked as environment-dependent, and preview results must carry a non-authoritative warning.

## Privacy constraints

Use public fixtures. Do not paste private plans, credentials, rankings, or instructor-only diagnostics into Colab or shared AI tools.
