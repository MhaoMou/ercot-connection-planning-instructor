# Colab DCOPF complexity benchmark

## Purpose

This benchmark measures the operational cost of the existing ACTIVSg2000 DCOPF implementation after the game-round/economic-model update. It is deliberately separate from evaluator math: the notebook imports the existing solver and records what happens when the solver is run on a Colab CPU runtime. It does not patch, approximate, or replace the evaluator.

The required horizon set is:

```text
1, 24, 168, 720, 4368, 4416 hours
```

The 4,368-hour case uses the checked-in 2028 January–June scenario block. The 4,416-hour case uses the checked-in 2027 July–December scenario block. Shorter cases use the 4,368-hour artifact and its first `H` periods. The notebook prints the exact artifact path and Git revision used by a run.

## Measurement contract

The notebook writes one row per variant, horizon, and repeat to `experiments/colab/results/dcopf_benchmark_results.csv` and a JSON copy beside it after every case. A row contains at least:

- wall-clock seconds for the complete case;
- median and P95 hourly solve seconds for independent hourly cases;
- sampled peak resident memory (RSS), peak Python allocation memory, RSS sampling interval, and sampling limitation;
- worker count;
- pre-presolve model variable and constraint counts;
- network-variable count;
- solver status, repeat index, and error/skip reason.

RSS is sampled from the process with `psutil`, so it includes native HiGHS allocations. Sampling can miss a shorter-lived peak between polling intervals; the configured interval and this limitation are recorded rather than implying an exact allocator high-water mark.

The storage case is one coupled horizon solve. It therefore reports the horizon wall time and memory, but leaves hourly median/P95 fields null. A null percentile means “not decomposable for this solver call,” not zero and not a missing measurement.

The benchmark records actual outcomes only. Solver exceptions, infeasibility, and skipped cases remain rows with explicit statuses. The notebook has no embedded timings or fabricated output cells, and this document makes no performance claim without a generated result artifact.

## Model-size accounting

Counts are pre-presolve dimensions implied by the model builder, so they remain available even when a solve fails. For each period, with `B` active buses, `G` active generators, `L` active branches, `S` storage assets, and `K` owner/team-bus storage groups:

```text
no storage variables       = G + (B - 1)
no storage constraints     = B + L
storage variables          = G + (B - 1) + 2S
storage constraints        = B + L + 2S + K
```

The storage formulation is continuous: each asset has one signed net-power variable and one state-of-charge variable per period. The current horizon builder therefore uses `2S` storage variables and `2S + K` storage-related rows per period: the SOC transition and the owner-load discharge cap, with the terminal SOC lower bound represented in the final SOC variable bound. There is no charge/discharge binary and no storage MIP. Horizon counts multiply the per-period counts. These are model-builder counts before HiGHS presolve, not claims about the reduced internal matrix.

## Execution variants

The no-storage rows compare three paths already supported by the repository:

1. `serial` clears the prepared-artifact and static topology caches before each hourly solve. It is a cold serial baseline.
2. `sparse_reuse` warms the prepared artifact and static topology index, then calls the same public hourly solver repeatedly. The label means sparse/static input reuse only; it does not claim matrix-factorization reuse.
3. `chunked_parallel` uses the existing threaded-worker pattern with deterministic chunks and records the worker count. It is not used for the coupled storage model because that model must remain one horizon solve.

`storage_horizon` uses the existing storage-aware `solve_dcopf_horizon` entry point with one four-hour, team-dedicated asset. The accelerated retry script additionally exposes the exact one-storage Benders path, which reuses one fixed-topology hourly model per worker, starts from physical storage-power bounds, and tightens only periods that prove infeasible. `--parallel-workers` controls Python recourse workers; `--solver-threads` controls HiGHS threads per worker, so users should increase one dimension at a time to avoid oversubscription. `--wall-time-limit-seconds` defaults to 1,500 seconds for storage Benders and records a safe `timeout` result instead of allowing a notebook to run indefinitely. `--tighten-feasible-bounds` restores the more expensive two-LP-per-hour interval prepass for diagnostic comparisons.

The Benders path is exact when it returns `optimal`; a timeout is unresolved and must not be used as a score. The generalized decomposition supports multiple team-owned storage assets and is the production route for joint storage evaluation. The coupled evaluator remains the correctness reference for small parity fixtures and debugging, not the default full-horizon route. An unresolved Benders result must be surfaced explicitly rather than silently falling back to an approximate score.

## Repeats and environment

The notebook defaults to three repeats for short horizons and one repeat for long horizons. Increase `REPEATS_LONG` when the runtime budget permits. It prints Python, platform, repository revision, HiGHS versions, prepared solver path, generator-limit semantics, and RSS sampling configuration. `highspy` is a declared project dependency and is checked explicitly after installation; the notebook stops with a clear message if it is unavailable rather than producing unverified solver measurements.

The notebook loads inputs through `load_prepared_artifact`, applies source status/PMIN/nameplate PMAX limits without synthetic hourly availability caps, and calls `solve_dcopf` or `solve_dcopf_horizon`; this is the exact benchmark path. The production prepared entry points additionally require a validated manifest, while the checked-in benchmark artifacts are intentionally fail-closed until release validation, so the notebook mirrors their model-input semantics with the low-level public solver calls. Compare sampled RSS only across similar runtime types. The Python allocation peak is a secondary diagnostic. Do not treat Colab's first-run package-install time as solver time: installation happens before measurement.

The JSON and CSV files are atomic per-case checkpoints. A stable `case_key` identifies each horizon/variant/repeat, and every row also carries a deterministic `configuration_signature`. That signature covers the artifact paths and manifest identities, official horizons, storage policy and `STORAGE_MAX_HORIZON`, variants, solver/model revision, and RSS sampler settings. Rerunning the notebook loads valid JSON rows, falls back to CSV if JSON is unavailable, and reuses a row only when both its case key and signature match. Missing-signature, mismatched, and legacy rows are stale and are recomputed, so a checkpoint created with `STORAGE_MAX_HORIZON=720` cannot suppress the default full-horizon storage cases. CSV recovery restores typed horizon/count integers, timing/memory/P95 floats, and booleans before recommendation filtering.

## Horizon recommendation rule

The notebook applies a configurable operational screen only to measured 4,416-hour, no-storage `sparse_reuse` rows that are completed with `solver_status == "optimal"` and have numeric performance fields:

- if a completed full-horizon measurement is within both configured wall-time and peak-RSS budgets, recommend retaining the official 4,416-hour horizon;
- if it exceeds a budget or is not completed, recommend a screening/shrunk local run only as an operational fallback and retain full remote instructor evaluation;
- if no such optimal result exists, report `dependency_limited_unverified` and do not make a performance conclusion.

This rule is intentionally evidence-based. The official horizon must not be reduced based on intuition, an unrun notebook, a student laptop estimate, or a partial smoke test.

## Student preview and local fallback

A student-facing Colab preview is available through `ercot-game-preview`. It
retains the complete official horizon index space, samples one representative
hour every six hours by default, and scales the sampled totals to the
represented hours. It deliberately leaves storage idle. This is a conservative
screening estimate: submitted storage is validated and capital-costed, while
its operational benefit is omitted. The exact storage-coupled evaluator is
still used for instructor-side official scores. Preview output must not be
used for final ranking.

A student may also use a shorter local screening horizon or fewer repeats to
iterate on plans. These are usability recommendations only and must not alter
the official horizon, scoring semantics, storage model, solver status handling,
or instructor-side evaluation.
