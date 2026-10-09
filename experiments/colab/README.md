# Half-year DCOPF Colab benchmark

`half_year_dcopf_benchmark.ipynb` is a reproducible, measurement-only benchmark for the repository's ACTIVSg2000 DCOPF path. It is intentionally not a checked-in result table: run the notebook in Colab to produce the measurements for the selected runtime, Python, HiGHS, and repository revision.

## Run it

1. Open `half_year_dcopf_benchmark.ipynb` in Google Colab.
2. Run all cells from top to bottom on a CPU runtime.
3. Allow the setup cell to clone the repository and install the project dependencies. The project declares `highspy`; the notebook also installs the small benchmark-only helpers `psutil`, `pandas`, and `matplotlib`.
4. Keep the default six horizons unless you are doing a short smoke test. The required horizons are 1, 24, 168, 720, 4,368, and 4,416 hours.
5. Download `experiments/colab/results/dcopf_benchmark_results.csv` and `dcopf_benchmark_results.json` from the cloned runtime directory after the run.

The setup cell fails with an actionable error if `highspy` cannot be imported. A successful package installation alone is not treated as a successful solver run.

## What is measured

The no-storage benchmark has three execution variants:

| Variant | Meaning |
| --- | --- |
| `serial` | One prepared-period solve at a time with the artifact and static topology caches cleared before each hour. This is a deliberately cold serial baseline. |
| `sparse_reuse` | The same public hourly solve with the prepared artifact and static network indexing warmed and reused. This is prepared-data/topology reuse; it does not claim that HiGHS reuses a factorization or model matrix. |
| `chunked_parallel` | Existing threaded worker support, with each worker processing a deterministic chunk of hourly period indices. |

The `storage_horizon` variant calls the existing coupled `solve_dcopf_horizon` path with one small team-owned four-hour storage asset. It is run by default through the configured storage feasibility cap (720 hours by default); longer storage cases are recorded as `skipped` with a reason instead of being silently omitted. The coupled storage solve is one horizon-level MIP, so per-hour median/P95 solve times are recorded as null rather than inventing an hourly decomposition.

Each measured row records the horizon, artifact block, variant, storage flag, repeat index, wall time, median/P95 hourly solve time when available, peak RSS and Python allocation peak, worker count, pre-presolve variable and constraint counts, network-variable count, solver status, and any error/skip reason. Repeated runs are configured separately for short and long horizons; the output also includes per-variant median and P95 wall-time statistics across repeats.

## Reproducibility controls

The first cell exposes `REPO_URL`, `REPO_REF`, and `ARTIFACT_ROOT` overrides. The notebook prints the repository revision, Python version, platform, HiGHS version, and benchmark configuration. The benchmark is deterministic in period ordering and uses the checked-in scenario artifacts; it does not include precomputed outputs.

For a quick notebook smoke test, set `REPEATS_SHORT = 1`, `REPEATS_LONG = 1`, and temporarily choose a short horizon in `BENCHMARK_HORIZONS`. Restore the required six-horizon tuple before treating a run as a benchmark report.

## Interpreting the official horizon

The notebook prints a recommendation only from measured 4,416-hour no-storage results and configurable wall-time/RSS budgets. It recommends retaining the official horizon when the full horizon completes within those measured budgets. If it does not, the output recommends screening or shrinking only as an operational fallback while retaining full 4,416-hour remote instructor evaluation; it does not change evaluator semantics.

No result is asserted in this repository. Do not copy timings, memory values, solver statuses, or horizon recommendations from an unrun notebook into a report.

The student-laptop fallback is a recommendation for local screening only. It must not be used to change the official scoring horizon or production evaluator behavior.
