# Canonical Site-Level Student CSV Design

## Goal

Replace the hourly student submission format with one canonical row per site,
while preserving enough internal model compatibility for the existing
evaluator to consume parsed plans until later tasks migrate it fully.

## Contract

The only canonical header is:

```text
team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh
```

Each data row is one unique site. A file contains one nonempty team ID and all
rows must use that team ID. Site and bus IDs must be nonempty, trimmed, and
free of whitespace/control characters and CSV delimiters. A supplied scenario
bus catalog still determines whether a bus ID exists.

All four capacities must be finite and nonnegative. Generator capacity cannot
exceed connection capacity. Storage is either disabled as `0,0` or enabled
with positive power and energy satisfying the four-hour rule:
`storage_energy_mwh == 4 * storage_power_mw`, using a small documented numeric
tolerance.

The parser returns `SitePlan` records and also constructs deterministic legacy
connection/generator/storage views for existing evaluator callers. Canonical
files have no submission ID column; `CSVSubmission.submission_id` is optional
and is `None` when absent.

## Compatibility boundary

The old long-form parser remains isolated and explicitly marked as legacy for
historical fixtures. It is not accepted by `validate_csv_submission` or the
student checker. The default checker reports the exact seven-column header,
site-row semantics, and representative valid examples.

## Verification

Tests cover exact headers and field counts, duplicate sites, malformed IDs,
nonfinite/nonnumeric/negative capacities, generator overflow, invalid storage
dimensions, malformed and oversized CSV input, zero assets, and positive
multi-site submissions.
