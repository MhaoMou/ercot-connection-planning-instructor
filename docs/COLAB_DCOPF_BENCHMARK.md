# Colab DCOPF complexity benchmark

## Purpose

This benchmark measures the operational cost of the existing ACTIVSg2000 DCOPF implementation after the game-round/economic-model update. It is deliberately separate from evaluator math: the notebook imports the existing solver and records what happens when the solver is run on a Colab CPU runtime. It does not patch, approximate, or replace the evaluator.

The required horizon set is:

```text
1, 24, 168, 720, 4368, 4416 hours
```

The 4,368-hour case uses the checked-in 2028 January–June scenario block. The 4,416-hour case uses the checked-in 2027 July–December scenario block. Shorter cases use the 4,368-hour artifact and its first `H` periods. The notebook prints the exact artifact path and Git revision used by a run.

## Measurement contract

The notebook writes one row per variant, horizon, and repeat to `experiments/colab/results/dcopf_benchmark_results.csv` and a JSON copy beside it. A row contains at least:

- wall-clock seconds for the complete case;
- median and P95 hourly solve seconds for independent hourly cases;
- peak resident memory (RSS) and peak Python allocation memory;
- worker count;
- pre-presolve model variable and constraint counts;
- network-variable count;
- solver status, repeat index, and error/skip reason.

The storage case is one coupled horizon solve. It therefore reports the horizon wall time and memory, but leaves hourly median/P95 fields null. A null percentile means “not decomposable for this solver call,” not zero and not a missing measurement.

The benchmark records actual outcomes only. Solver exceptions, infeasibility, and skipped cases remain rows with explicit statuses. The notebook has no embedded timings or fabricated output cells, and this document makes no performance claim without a generated result artifact.

## Model-size accounting

Counts are pre-presolve dimensions implied by the model builder, so they remain available even when a solve fails. For each period, with `B` active buses, `G` active generators, `L` active branches, `S` storage assets, and `K` owner/team-bus storage groups:

```text
no storage variables       = G + (B - 1)
no storage constraints     = B + L
storage variables          = G + (B - 1) + 4S
storage constraints        = B + L + 4S + K
```

The four storage variables are charge, discharge, binary mode, and state of charge. The four storage constraints are charge-mode, discharge-mode, SOC transition, and discharge-from-previous-SOC; `K` is the owner-load discharge cap. Horizon counts multiply the per-period counts. These are model-builder counts before HiGHS presolve, not claims about the reduced internal matrix.

## Execution variants

The no-storage rows compare three paths already supported by the repository:

1. `serial` clears the prepared-artifact and static topology caches before each hourly solve. It is a cold serial baseline.
2. `sparse_reuse` warms the prepared artifact and static topology index, then calls the same public hourly solver repeatedly. The label means sparse/static input reuse only; it does not claim matrix-factorization reuse.
3. `chunked_parallel` uses the existing threaded-worker pattern with deterministic chunks and records the worker count. It is not used for the coupled storage model because that model must remain one horizon solve.

`storage_horizon` uses the existing storage-aware `solve_dcopf_horizon` entry point with one four-hour, team-dedicated asset. By default, storage horizons above 720 hours are represented as explicit skipped rows because a long mixed-integer run may exceed a practical Colab budget. Set the notebook's storage cap higher when a runtime can support those measurements; a skipped row is not evidence that the solver failed.

## Repeats and environment

The notebook defaults to three repeats for short horizons and one repeat for long horizons. Increase `REPEATS_LONG` when the runtime budget permits. It prints Python, platform, repository revision, and HiGHS versions. `highspy` is a declared project dependency and is checked explicitly after installation; the notebook stops with a clear message if it is unavailable.

Because peak RSS includes native HiGHS allocations, compare results only across similar runtime types. The Python allocation peak is included as a secondary diagnostic. Do not treat Colab's first-run package-install time as solver time: installation happens before measurement.

## Horizon recommendation rule

The notebook applies a configurable operational screen to the measured 4,416-hour, no-storage `sparse_reuse` rows:

- if a completed full-horizon measurement is within both configured wall-time and peak-RSS budgets, recommend retaining the official 4,416-hour horizon;
- if it exceeds a budget or is not completed, recommend a screening/shrunk local run only as an operational fallback and retain full remote instructor evaluation;
- if no 4,416-hour result exists, report that no recommendation can be made.

This rule is intentionally evidence-based. The official horizon must not be reduced based on intuition, an unrun notebook, a student laptop estimate, or a partial smoke test.

## Student-laptop fallback

A student may use a shorter local screening horizon or fewer repeats to iterate on plans. That is a usability recommendation only. It must not alter the official horizon, scoring semantics, storage model, solver status handling, or instructor-side evaluation.
