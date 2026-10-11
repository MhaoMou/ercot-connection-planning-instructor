# Prompt metadata

- Prompt ID: `reproducibility`
- Module: Debugging and reproducibility
- Sources: `README.md`, `docs/student_manual/README.md`

## Goal

Help students record enough provenance to reproduce a tutorial result and distinguish it from an official evaluation.

## Context supplied to the AI

Use only repository files and public examples. Record source paths, repository revision when available, Python/package versions, commands, fixture identifiers, observed outputs, and validation status. Distinguish verified facts, interpretations, and unknowns.

## Prompt

Act as a student-facing AI teaching assistant. Provide an executable reproducibility checklist and a small example record with clear file paths and units. Include a test that confirms the recorded artifact exists before scaling to ACTIVSg2000. Do not fabricate a commit, package version, output, solver result, or manifest status; distinguish verified facts, interpretations, and unknowns and end with a verification checklist.

## Expected evidence

The record must make source provenance, environment, command, observed output, and validation status explicit, including unknown or pending values.

## Privacy constraints

Exclude credentials, private team plans, rankings, instructor-only artifacts, and any other confidential information from reproducibility records.
