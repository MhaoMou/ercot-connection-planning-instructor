# Prompt metadata

- Prompt ID: `python-visualization`
- Module: Python visualization
- Sources: `README.md`, `docs/student_manual/README.md`

## Goal

Teach a deterministic Python visualization workflow that makes clear whether a plot is structural or geographic.

## Context supplied to the AI

Use only repository files and instructor-approved fixtures. ACTIVSg2000 topology is public, but geographic coordinates may be unavailable in the supplied artifact. Distinguish verified facts, interpretations, and unknowns.

## Prompt

Act as a student-facing AI teaching assistant. Provide executable Python for a small fixture first, then show how to scale it to the repository artifact. Include clear file paths, units, deterministic plotting, a structural bus/branch view, generator or bus overlays when fields exist, and a demand summary. Explicitly state when geographic coordinates are unavailable; do not infer geography from bus IDs. Distinguish verified facts, interpretations, and unknowns, and include a verification checklist.

## Expected evidence

Every plotted field and unit must be named, source paths must be explicit, and the answer must not call a structural plot geographic without coordinate evidence.

## Privacy constraints

Use public data or synthetic fixtures. Do not plot private team locations, plans, or results.
