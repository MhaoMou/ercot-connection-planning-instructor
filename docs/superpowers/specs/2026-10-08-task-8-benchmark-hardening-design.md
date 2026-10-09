# Task 8 benchmark hardening design

## Scope

Harden the Colab ACTIVSg2000 DCOPF benchmark introduced by `99daa59` without
changing evaluator behavior, production scoring, or instructor manuals. The
benchmark must report measured outcomes only and remain resumable after a
Colab interruption.

## Measurement and solver-path design

`timed()` will start a daemon sampler immediately before the case function and
poll the current process RSS with `psutil.Process().memory_info().rss` at a
configurable interval. It will retain the maximum observed RSS, stop and join
the sampler in `finally`, and record the interval plus a limitation explaining
that sampling can miss a short-lived peak between polls. `tracemalloc` remains
as a secondary Python-allocation diagnostic. The row will also record the exact
prepared artifact path and solver path used by the case.

Notebook inputs will use the checked-in prepared artifact through the same
production `load_prepared_artifact` path. The benchmark will not apply hourly
generator availability caps when the prepared production artifact intentionally
uses nameplate limits. The input contract and this semantic distinction will
be explicit in code and documentation.

## Case matrix, skips, and resume

The default horizon set remains `1, 24, 168, 720, 4368, 4416`. When `highspy`
is available, storage cases are scheduled for all six horizons by default.
An explicit user configuration may provide a storage horizon cap or skip
policy; each omitted case becomes a row with `run_status="skipped"` and a
machine-readable reason. No implicit cap is used.

Each case is identified by horizon, variant, and repeat index. Before running a
case, the notebook loads existing JSON/CSV checkpoint rows and skips an already
completed, infeasible, error, or explicitly skipped case. After each case it
atomically rewrites both checkpoint files, so interruption preserves all rows
written before the interruption. The final export adds repeat statistics and
the recommendation without inventing results.

## Recommendation contract

`recommend()` considers only rows that are completed and have
`solver_status == "optimal"` for the target full-horizon sparse-reuse case.
Missing, non-optimal, skipped, errored, or otherwise dependency-limited rows
return an explicit `dependency_limited_unverified` recommendation with a
reason. Budget-based performance conclusions are emitted only from the
verified optimal candidate set.

## Verification

Add notebook contract tests for RSS sampling/checkpoint metadata, explicit
storage scheduling and skip policy, prepared nameplate inputs, resume behavior,
and optimal-status recommendation gating. Update the benchmark documentation
to describe these contracts. Run the notebook contract/full tests and the
repository diff check, then commit only the design/benchmark changes while
preserving pre-existing unrelated working-tree changes.
