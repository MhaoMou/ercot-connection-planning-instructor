# Prompt metadata

- Prompt ID: `matlab-visualization`
- Module: MATLAB visualization
- Sources: `README.md`, `docs/student_manual/README.md`

## Goal

Explain how MATLAB could support inspection or visualization while keeping Python/repository tooling authoritative.

## Context supplied to the AI

The repository documents the current data and CSV contracts but does not guarantee MATLAB files, toolbox availability, or geographic coordinates. Use only supplied sources. Distinguish verified facts, interpretations, and unknowns.

## Prompt

Act as a student-facing AI teaching assistant. Give a small executable MATLAB example for reading and plotting a supplied tabular fixture, with clear paths and units, then explain which fields must be confirmed before applying it to ACTIVSg2000. Do not invent MAT-file fields, bus IDs, coordinates, solver behavior, or a MATLAB requirement. Include a small test before scaling, distinguish verified facts, interpretations, and unknowns, and end with a verification checklist.

## Expected evidence

Mark MATLAB as optional unless the supplied sources say otherwise, identify the actual input fields, and state what cannot be verified without the artifact.

## Privacy constraints

Use public or instructor-approved fixtures only. Do not load private plans or instructor-only data into MATLAB examples.
