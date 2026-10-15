# Instructor Evaluator Acceleration

This note records the exact acceleration work for the storage-enabled,
multi-team instructor evaluator. The authoritative mathematical route remains
the HiGHS multi-storage Benders formulation. The optimizations below preserve
the dispatch, LMP, feasibility, and scoring semantics.

## Implemented changes

1. **Instrumentation.** `DCOPFBendersDiagnostics` now records preparation,
   initial-recourse, candidate-recourse, finalization, iteration, cut, solve,
   gap, and cache-hit counts. Batch output stores those diagnostics in
   `evaluation_metadata`.
2. **Compact prepared horizons.** Fixed topology, generator data, and branch
   data are retained once. Hourly demand is passed as compact bus mappings;
   the old materialized-period path remains available when hourly network data
   actually varies.
3. **Constant-plan reuse.** Released site-only submissions reuse invariant
   connection, generator, and storage tuples across the horizon. Legacy
   profile-bearing submissions retain the compatible path.
4. **Reusable LP models.** Each recourse worker reuses one DCOPF model and
   updates demand and fixed-injection right-hand sides rather than rebuilding
   the network model for every hour.
5. **Compact output.** The CLI supports `--output-mode compact`, which keeps
   scores, manifests, team diagnostics, aggregate diagnostics, hashes, and
   selected binding information while omitting the very large per-period
   dispatch export. Use `--output-mode full` for detailed visualization or
   forensic analysis.
6. **Optional Gurobi backend.** The CLI supports
   `--solver-backend gurobi` when `gurobipy` and a valid license are present.
   The default `auto` mode selects Gurobi when possible and falls back to
   HiGHS so the evaluator remains usable without a commercial solver license.
7. **Safe Benders reuse.** Successful recourse results are cached by period
   and storage-injection point. No hourly recourse solve is skipped unless its
   result is already known exactly; an attempted speculative selective solve
   was rejected because it added overhead for the observed one-iteration case
   without a certification benefit.

## Recommended instructor command

```bash
PYTHONPATH=src:. python3 -m ercot_game.batch_cli run \
  --submissions-dir docs/instructor_manual/examples/eight_team_round/submissions \
  --artifact-dir work/repro-artifact-2028-jan-jun \
  --rules data/teaching/dispatch_scoring_rules_jan_jun_v1.json \
  --horizon 4368 \
  --workers 4 \
  --solver-backend auto \
  --output-mode compact \
  --submission-number 2 \
  --output-dir work/evaluation-output
```

`auto` uses Gurobi when its package and license can be started, and otherwise
falls back to HiGHS. Use `--solver-backend highs` or
`--solver-backend gurobi` to force a backend.

## Electrify benchmark

The benchmark used the checked-in eight-team fixture and the validated
2028-January--June artifact. All runs used compact output and one Benders
iteration for this fixture. The short HiGHS worker sweep was:

| Horizon | Workers | Wall time | Peak child RSS* |
|---:|---:|---:|---:|
| 24 h | 2 | 8.2 s | 519 MB |
| 24 h | 4 | 8.2 s | 543 MB |
| 24 h | 6 | 8.2 s | 561 MB |
| 24 h | 8 | 8.3 s | 586 MB |
| 168 h | 2 | 47.4 s | 875 MB |
| 168 h | 4 | 46.9 s | 903 MB |
| 168 h | 6 | 47.0 s | 919 MB |
| 168 h | 8 | 47.1 s | 936 MB |
| 720 h | 2 | 200.8 s | 2.23 GB |
| 720 h | 4 | 208.7 s | 2.23 GB |
| 720 h | 6 | 204.8 s | 2.26 GB |
| 720 h | 8 | 208.6 s | 2.28 GB |

\* The benchmark records the Python child-process maximum resident set size;
for the multi-process worker run it is an upper-bound-style process metric,
not a separately isolated per-worker allocation.

The exact 4,368-hour HiGHS run with four workers completed in approximately
1,151 seconds of solver/evaluator time (about 20.5 minutes including output
handling). It produced 8 accepted teams and reproduced the checked-in scores
with a maximum absolute difference of approximately `$4.8\times10^{-7}`.

The compact `evaluation.json` was approximately 442 MB, compared with about
1.37 GB for the prior full-output reproduction. The compact result contains no
per-period JSON payload (`periods: []`); it is therefore the recommended mode
for routine scoring.

## Gurobi comparison

On the same Electrify host and fixture:

| Horizon | Workers | Backend | Wall time | Solver elapsed | Peak child RSS |
|---:|---:|---|---:|---:|---:|
| 24 h | 4 | Gurobi | 2.5 s | — | 538 MB |
| 168 h | 4 | Gurobi | 6.3 s | — | 735 MB |
| 4,368 h | 4 | Gurobi | about 146 s | 83.7 s | about 5 GB |

The 24-hour and 168-hour runs matched HiGHS team scores within numerical
tolerance; the maximum absolute difference at 168 hours was approximately
`$4.4\times10^{-5}`. The full 4,368-hour Gurobi run differed from the
checked-in HiGHS scores by at most about `$0.51`, with identical feasibility and
team ranking on the tested fixture. This is numerically negligible relative to
the score scale; `auto` selects Gurobi on the licensed instructor host and
retains HiGHS as the portable fallback.

## Verification

The focused evaluator and backend suite passed remotely on Electrify:

```text
151 passed in 3.75s
```

The broad repository suite was also exercised. It reported 793 passed and 1
skipped, plus 8 failures outside the modified evaluator path:
documentation/example fixtures, the tutorial LaTeX toolchain, notebook
fixture hygiene, and an environment mismatch where the remote Python 3.10
does not expose stdlib `tomllib` (although `tomli` is installed). Those issues
were not changed as part of evaluator acceleration.

## Operational recommendation

- Use **auto + compact output** as the default. It selects Gurobi on a licensed
  instructor host and falls back to HiGHS elsewhere.
- Use **Gurobi + compact output** explicitly when a fixed licensed backend is
  required for a run.
- Keep `--output-mode full` for debugging, visual inspection, or a deliberate
  audit export; it is not appropriate for every routine round because of the
  multi-hundred-megabyte result size.
- Keep the worker count near 4 unless a new fixture shows a different scaling
  curve. The benchmarked eight-team fixture was effectively solver-bound, so
  increasing workers from 2 to 8 did not improve wall time.
