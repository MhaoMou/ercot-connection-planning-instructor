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
unavailable optional `highspy` dependency. The backend version is therefore
`unavailable`, and this row is evidence of the attempted stage timing and
failure accounting only; it is not a solver-performance claim.

## ACTIVSg2000 and horizon status

The ACTIVSg2000 one-period attempt was blocked before its stage began because
the prepared package manifest had
`validation_status=unverified_dependency`. Consequently, its JSONL file has no
stage row. The block's classification is dependency-limited because `highspy`
was unavailable, not because a network or solver failure was observed.

The 24-period, 168-period, and full-horizon stages were not run. No artifact or
full-horizon benchmark rows are published here.

## Exact follow-up procedure

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

Then run the complete staged benchmark sequence, including the 1-, 24-, 168-,
and full-horizon stages:

```bash
PYTHONPATH=.:src python3 tools/benchmarks/benchmark_hosting_capacity.py \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2028/jan_jun \
  --solver-backend highs \
  --solver-threads 1 \
  --workers 1 \
  --full-horizon \
  --output-dir results/hosting_capacity_benchmark_2028_jan_jun
```

The follow-up results should be reported only from the resulting stage rows;
this report does not predict their timings, memory values, point counts, or
hosting-capacity values.
