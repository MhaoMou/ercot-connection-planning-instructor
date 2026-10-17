# Hosting-capacity release benchmark report

**Environment date:** 2026-10-15
**Classification:** dependency-limited

This report publishes only the measured IEEE-14 benchmark row. It does not
publish artifact, partial-horizon, or full-horizon result rows that were not
measured.

## Recorded command

The measured row corresponds to this repository-relative command:

```bash
PYTHONPATH=.:src python3 tools/benchmarks/benchmark_hosting_capacity.py \
  --ieee14 \
  --solver-backend highs \
  --solver-threads 1 \
  --workers 1 \
  --output-dir results/hosting_capacity_benchmark_ieee14
```

## Actual measured IEEE-14 result

The row below is also preserved verbatim in
[`benchmark-results.jsonl`](benchmark-results.jsonl).

| Field | Measured value |
| --- | ---: |
| `stage` | `ieee14-1` |
| `backend` | `highs` |
| `workers` | `1` |
| `solver_threads` | `1` |
| `wall_time_s` | `0.007359` |
| `peak_rss_mb` | `33.797` |
| `rss_delta_mb` | `0.609` |
| `model_build_time_s` | `null` |
| `resume_status` | `fresh` |
| `mean_point_time_s` | `0.000103` |
| `p50_point_time_s` | `0.000103` |
| `p95_point_time_s` | `0.000103` |
| `completed_points` | `14` |
| `failed_points` | `14` |

The 14 completed point records were all failed points, not successful hosting-
capacity solves. The IEEE-14 metadata records the failure reason as the
unavailable required project dependency `highspy`. The backend version is
therefore `unavailable`, and this row is evidence of the attempted stage timing
and failure accounting only; it is not a solver-performance claim.

## ACTIVSg2000 and horizon status

The ACTIVSg2000 one-period attempt was blocked before its stage began because
the prepared package manifest had
`validation_status=unverified_dependency`. Consequently, its JSONL file has no
stage row. The block's classification is dependency-limited because the required
project dependency `highspy` was unavailable in this environment, not because
a network or solver failure was observed.

The 24-period, 168-period, and full-horizon stages were not run. No artifact or
full-horizon benchmark rows are published here.

## Exact follow-up procedure

*Historical note:* this procedure targeted the retired hourly package
`activsg2000_scenarios_v1`. The canonical six-hour package
`activsg2000_scenarios_v2_6h` already records `validation_status: passed` for
all six blocks, and the pilot gate and full-horizon command below now use it.

After installing `highspy`, validate every released scenario block successfully
before running the benchmark:

```bash
python3 -m pip install highspy
PYTHONPATH=.:src python3 tools/activsg2000/validate_scenario_package.py \
  --package data/processed/activsg2000_scenarios_v1 \
  --workers 6
```

The validation command must finish successfully and the root manifest plus all
six block manifests must report `validation_status: passed`. A partial run,
missing dependency, or any block not reporting `passed` is insufficient.

## Task 9 release pilot gate

Before any full-horizon run, all three pilots below must pass and the operator
must record explicit operator approval. Do not start a full-horizon run before
that approval. These commands use the prepared
2028 January--June `bus.csv`: `1001` is the one-bus pilot, `1001` through
`1010` are the ten-bus pilot, and omitting `--bus-id` selects all 2,000 buses.
All generated files, checkpoints, summaries, and raw resource logs stay below
`/private/tmp/hosting-capacity-pilot`.

```bash
mkdir -p /private/tmp/hosting-capacity-pilot/{one-bus-24h,ten-bus-24h,all-bus-1h}
```

Run the one-bus pilot once from a clean output directory:

```bash
env PYTHONPATH=.:src /usr/bin/time -l -o /private/tmp/hosting-capacity-pilot/one-bus-24h/resource-usage-fresh.txt \
  python3 -m ercot_game.hosting_capacity_cli \
  --artifact-dir data/processed/activsg2000_scenarios_v2_6h/2028/jan_jun \
  --horizon 24 \
  --period-count 24 \
  --bus-id 1001 \
  --solver-backend highs \
  --solver-threads 1 \
  --workers 1 \
  --output-dir /private/tmp/hosting-capacity-pilot/one-bus-24h \
  > /private/tmp/hosting-capacity-pilot/one-bus-24h/cli-summary-fresh.json
```

Run the exact same one-bus request again against the preserved output and
checkpoint directory:

```bash
env PYTHONPATH=.:src /usr/bin/time -l -o /private/tmp/hosting-capacity-pilot/one-bus-24h/resource-usage-resumed.txt \
  python3 -m ercot_game.hosting_capacity_cli \
  --artifact-dir data/processed/activsg2000_scenarios_v2_6h/2028/jan_jun \
  --horizon 24 \
  --period-count 24 \
  --bus-id 1001 \
  --solver-backend highs \
  --solver-threads 1 \
  --workers 1 \
  --output-dir /private/tmp/hosting-capacity-pilot/one-bus-24h \
  > /private/tmp/hosting-capacity-pilot/one-bus-24h/cli-summary-resumed.json
```

Run the ten-bus pilot with the ten concrete IDs from that same `bus.csv`:

```bash
env PYTHONPATH=.:src /usr/bin/time -l -o /private/tmp/hosting-capacity-pilot/ten-bus-24h/resource-usage.txt \
  python3 -m ercot_game.hosting_capacity_cli \
  --artifact-dir data/processed/activsg2000_scenarios_v2_6h/2028/jan_jun \
  --horizon 24 \
  --period-count 24 \
  --bus-id 1001 \
  --bus-id 1002 \
  --bus-id 1003 \
  --bus-id 1004 \
  --bus-id 1005 \
  --bus-id 1006 \
  --bus-id 1007 \
  --bus-id 1008 \
  --bus-id 1009 \
  --bus-id 1010 \
  --solver-backend highs \
  --solver-threads 1 \
  --workers 1 \
  --output-dir /private/tmp/hosting-capacity-pilot/ten-bus-24h \
  > /private/tmp/hosting-capacity-pilot/ten-bus-24h/cli-summary.json
```

Run the one-period all-bus pilot without any `--bus-id` option:

```bash
env PYTHONPATH=.:src /usr/bin/time -l -o /private/tmp/hosting-capacity-pilot/all-bus-1h/resource-usage.txt \
  python3 -m ercot_game.hosting_capacity_cli \
  --artifact-dir data/processed/activsg2000_scenarios_v2_6h/2028/jan_jun \
  --horizon 1 \
  --period-count 1 \
  --solver-backend highs \
  --solver-threads 1 \
  --workers 1 \
  --output-dir /private/tmp/hosting-capacity-pilot/all-bus-1h \
  > /private/tmp/hosting-capacity-pilot/all-bus-1h/cli-summary.json
```

The pilot gate passes only when every command has exit code 0 and all of the
following checks are recorded in the release log:

- `one-bus-24h/hosting_capacity_mw.npy` and
  `hosting_capacity_status.npy` both have shape `(24, 1)`; the ten-bus files
  both have shape `(24, 10)`; and the all-bus files both have shape `(1, 2000)`.
- Each metadata file reports `complete: true`, a matching point count, and
  every matrix status is non-empty and `optimal`; no capacity value is NaN.
- The first one-bus summary reports `resume_status=fresh`, the second reports
  `resume_status=resumed`, `checkpoints/period_0.npz` is a valid completed
  checkpoint, and the resumed metadata reports
  `resumed_from_checkpoint: true`.
- Every JSON, NumPy, CSV, and checkpoint output loads successfully, has the
  documented schema and shape, and contains no malformed, partial, or
  unexpected-status output.
- Record `wall_time_s`, `peak_rss_mb`, and `rss_delta_mb` from each CLI JSON
  summary for every pilot; the summary records both auditable resource values.
  Retain the independent raw `/usr/bin/time -l` files and the before/after RSS
  evidence as raw evidence; these are recorded measurements only, with no
  invented numeric memory or time limit.

All three pilots and every criterion above are mandatory. Full-horizon
execution requires the operator's explicit approval recorded after this gate.

## Full-horizon benchmark command

Only after that approval, run the complete staged benchmark sequence, including
the 1-, 24-, 168-, and full-horizon stages:

```bash
PYTHONPATH=.:src python3 tools/benchmarks/benchmark_hosting_capacity.py \
  --artifact-dir data/processed/activsg2000_scenarios_v2_6h/2028/jan_jun \
  --solver-backend highs \
  --solver-threads 1 \
  --workers 1 \
  --full-horizon \
  --output-dir results/hosting_capacity_benchmark_2028_jan_jun
```

The follow-up results should be reported only from the resulting stage rows;
this report does not predict their timings, memory values, point counts, or
hosting-capacity values.
