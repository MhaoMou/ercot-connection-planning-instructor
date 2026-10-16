# Hosting-Capacity CLI Resource Metrics Design

## Goal

Make the hosting-capacity CLI summaries auditable for memory usage without
changing the existing JSON schema fields, exit codes, or raw benchmark
evidence workflow.

## Scope and semantics

The CLI will add two top-level, additive summary fields to both normal and
interrupted JSON output:

- `peak_rss_mb`: the absolute post-compute `ru_maxrss` value in MiB, using the
  platform's native `ru_maxrss` units and `null` when resource usage cannot be
  read.
- `rss_delta_mb`: the nonnegative stage-local delta in MiB, computed from
  self and child `ru_maxrss` snapshots immediately before and after
  `compute_hosting_capacity_matrix`. The reported delta is the maximum of the
  available nonnegative self/child deltas; it is `null` when no complete
  self/child delta is available.

Argument parsing, provider setup, validation, and other pre-compute work are
excluded from the delta measurement. If compute raises `KeyboardInterrupt`,
the interrupted summary uses the latest available post/interruption snapshot.
The existing summary fields and normal/interrupted exit behavior remain
unchanged.

## Implementation boundary

Resource snapshot and conversion helpers will live in
`src/ercot_game/hosting_capacity_cli.py`, matching the benchmark helper's
platform conversion and unavailable-resource behavior without coupling the
CLI to benchmark code. `main` will take one snapshot immediately before the
compute call and one immediately after it, then pass the derived values to the
normal and interrupted summary dictionaries.

## Verification

Focused tests will cover available and unavailable resource readings, normal
summary fields, and interrupted summary fields. Existing CLI tests will retain
their assertions for JSON fields and exit codes. Documentation in the hosting
capacity README and benchmark report will state that CLI summaries record both
metrics and that independent raw `/usr/bin/time -l` logs are retained as
evidence.
