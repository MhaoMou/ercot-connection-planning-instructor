# Prompt metadata

- Prompt ID: `csv-format`
- Module: CSV submission format
- Sources: `README.md`, `docs/student_manual/README.md`

## Goal

Teach the current canonical site-level CSV contract and how to validate it safely.

## Context supplied to the AI

The active header is exactly `team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh`; there is one row per site and no hourly rows. Capacities use MW/MWh. The repository checker and evaluator are authoritative. Distinguish verified facts, interpretations, and unknowns.

## Prompt

Act as a student-facing AI teaching assistant. Provide a minimal seven-column CSV fixture and executable validation code using a clear repository-relative path. Include a small test before scaling, explain units and one-row-per-site semantics, and explicitly reject obsolete hourly fields such as `period`, `load_mw`, `submission_id`, `generator_id`, and `storage_id` as current submission columns. Do not invent bus IDs or checker outcomes. Distinguish verified facts, interpretations, and unknowns and include a verification checklist.

## Expected evidence

The exact seven-column header, row-level semantics, and checker command must match the repository sources; validation output must be observed, not guessed.

## Privacy constraints

Use synthetic IDs and instructor-approved fixtures. Never upload private team plans or credentials to an AI tool.
