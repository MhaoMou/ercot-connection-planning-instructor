# Canonical CSV Quality Blockers Design

## Goal

Make the public student-submission contract unambiguous and make the standalone
submission checker usable with only a canonical CSV path, while preserving the
retired hourly CSV API names and parser behavior for historical compatibility.

## Contract

The active submission format is exactly one row per site with these seven
columns, in this order:

```text
team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh
```

The canonical public constant is `CANONICAL_CSV_COLUMNS`. `CSV_COLUMNS` and
`BUS_CSV_COLUMNS` remain explicit compatibility aliases to the same tuple, so
existing imports continue to work and cannot silently drift from the active
contract. The retired ten-column hourly names remain under
`LEGACY_CSV_COLUMNS` and `LEGACY_BUS_CSV_COLUMNS` for opt-in historical parsing;
they are not valid current submissions.

## Implementation

1. Document the canonical header, one-row-per-site semantics, and absence of
   hourly period/horizon rows in the root README, the student manual source and
   README, the instructor manual source and README, and active example guides.
   Where checked-in examples are retained for historical pipeline comparison,
   label them explicitly as legacy and do not present them as current student
   input.
2. Add explicit module documentation for the canonical constant and both legacy
   aliases. Add tests that assert exact seven-column values and object identity
   compatibility for all three public names.
3. Update the validator CLI so `--file canonical.csv` is sufficient. Keep
   `--artifact-dir`, `--horizon`, `--year`, and `--round` as optional context;
   when supplied they continue to provide catalog validation or report context,
   but their absence does not block canonical validation. Do not change the
   validator’s row/value checks or any evaluator path.

## Verification

Run the focused CSV submission and validator tests, then the complete pytest
suite. Review the final diff and status to confirm only intended source,
documentation, tests, and plan/spec files changed; pre-existing generated
LaTeX artifacts and `example_score_table.tex` remain unmodified and untracked
generated files remain uncommitted.
