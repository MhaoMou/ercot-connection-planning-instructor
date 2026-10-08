# Instructor batch examples

These deterministic CSVs are historical ten-column hourly compatibility
fixtures. They are not current student submissions. They are deliberately
small one-period smoke fixtures for demonstrating parser errors and the
failure-isolation path. Current submissions use the canonical seven-column,
one-row-per-site format shown in the student and instructor manuals.

The files demonstrate the approved joint-infeasibility behavior:

- `team-a.csv` is a moderate, valid one-period submission that can be scored.
- `team-b.csv` is a valid but intentionally oversized one-period submission.
  When both are evaluated together, the evaluator first attempts fair
  continuous projection; if projection is unavailable, deterministic fallback
  identifies the batch-blocking team and preserves valid-team scoring.
- `invalid-negative-load.csv` has the same schema but an intentionally invalid
  negative load. It is retained in `submission_manifest.csv`, excluded from
  the DCOPF, and assigned the configured infeasible score.

Historical fixture header (not the active production header):

```text
team_id,submission_id,period,bus_id,load_mw,generator_id,generator_capacity_mw,storage_id,storage_power_mw,storage_energy_mwh
```

The historical examples use Zenodo bus `3001003`, generator `14023`, and period
`1`, so they can be evaluated with a one-period smoke-test horizon. Do not copy
this header into a current submission; the canonical format has no hourly
period/load rows or public generator ID field.
There are no curtailment or load-shedding fields, and the fixed submitted load
is not curtailed.

From the repository root, an example run is:

```bash
ercot-game-batch run \
  --submissions-dir examples/instructor_batch \
  --artifact-dir data/processed/texas_dispatch_source_v1 \
  --rules data/teaching/dispatch_scoring_rules_jan_jun_v1.json \
  --horizon 1 \
  --period-indices 0 \
  --submission-number 2 \
  --output-dir work/instructor_batch_example_results
```

The shipped files use the real catalog ID and bus schema. The intentionally
invalid fixture is included so the same command also demonstrates per-file
rejection and manifest retention. Do not use this one-period command for
classroom scoring. A production CSV contains one row per site and no hourly
rows; the evaluator selects the released year-specific half-year horizon on
its side (4,344, 4,368, or 4,416 one-hour periods, depending on the calendar).
