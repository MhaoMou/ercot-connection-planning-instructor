# Prompt metadata

- Prompt ID: `dcopf-concept`
- Module: DCOPF concepts
- Sources: `README.md`, `docs/student_manual/README.md`

## Goal

Build an intuitive but mathematically careful explanation of DCOPF balance, angles, flows, and limits.

## Context supplied to the AI

Use only the supplied repository contract and explicitly label generic power-systems interpretation. Do not infer evaluator-specific equations beyond the sources. Distinguish verified facts, interpretations, and unknowns.

## Prompt

Act as a student-facing AI teaching assistant. Explain DCOPF using a three-bus toy example, including a stated sign convention, nodal balance, branch flow relationship, generator bounds, and a reference angle. Use consistent MW and radians or clearly state another unit convention. Include executable pseudocode or code only after a small test, and distinguish verified facts, interpretations, and unknowns. End with a verification checklist.

## Expected evidence

The sign convention and balance equation must be internally consistent, and any evaluator-specific behavior not in the sources must be marked unknown.

## Privacy constraints

Use only public generic examples and repository facts. Do not use private team network assumptions or scores.
