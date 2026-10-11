# Prompt metadata

- Prompt ID: `dcopf-formulation`
- Module: DCOPF formulation
- Sources: `README.md`, `docs/student_manual/README.md`

## Goal

Translate a verified DCOPF concept into a transparent optimization formulation without inventing evaluator details.

## Context supplied to the AI

Use repository sources for current workflow facts and a clearly labeled three-bus fixture for illustrative mathematics. Distinguish verified facts, interpretations, and unknowns.

## Prompt

Act as a student-facing AI teaching assistant. Write an explicit small DCOPF formulation with objective, nodal balance, linearized branch-flow equations, thermal limits, generator limits, and a reference-angle constraint. Give executable code for the toy fixture only, with paths and units, and include a small assertion before any larger example. Do not invent cost coefficients, solver settings, or evaluator behavior; distinguish verified facts, interpretations, and unknowns and provide a verification checklist.

## Expected evidence

Check dimensions, signs, units, feasibility, reference-angle choice, solver status, and objective consistency on the toy case.

## Privacy constraints

Do not include private plans, proprietary solver logs, credentials, or instructor-only evaluation data.
