# Prompt metadata

- Prompt ID: `dataset-inspection`
- Module: ACTIVSg2000 data inspection
- Sources: `README.md`, `docs/ACTIVSG2000_SCENARIO_PACKAGE.md`

## Goal

Show how to inspect the released ACTIVSg2000 scenario package and verify provenance without overstating validation.

## Context supplied to the AI

Use only the supplied repository sources and an instructor-approved fixture. The package has fixed ACTIVSg2000 topology and six half-year blocks, while checked-in manifests may report `unverified_dependency`. Distinguish verified facts, interpretations, and unknowns.

## Prompt

Act as a student-facing AI teaching assistant. Give executable Python that reports package file names, shapes, keys, missing values, duplicate keys, bus references, horizon metadata, and manifest validation status. Use clear repository-relative file paths and units, and begin with a small fixture test before scaling to ACTIVSg2000. Do not fabricate file contents or claim validation unless a manifest says `validation_status: passed`. Distinguish verified facts, interpretations, and unknowns, and include a verification checklist.

## Expected evidence

The answer must identify the manifest and catalog files it reads, report their actual observed values, and preserve `unverified_dependency` as non-passed status.

## Privacy constraints

Use public scenario artifacts or instructor-approved fixtures only. Do not inspect or transmit private team submissions.
