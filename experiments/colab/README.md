# Half-year DCOPF Colab benchmark

`half_year_dcopf_benchmark.ipynb` is a reproducible, measurement-only benchmark for the repository's ACTIVSg2000 DCOPF path. It is intentionally not a checked-in result table: run the notebook in Colab to produce the measurements for the selected runtime, Python, HiGHS, and repository revision.

## Run it

1. Open `half_year_dcopf_benchmark.ipynb` in Google Colab.
2. Run all cells from top to bottom on a CPU runtime.
3. Allow the setup cell to clone the repository and install the project dependencies. The project declares `highspy`; the notebook also installs the small benchmark-only helpers `psutil`, `pandas`, and `matplotlib`.
4. Keep the default six horizons unless you are doing a short smoke test. The required horizons are 1, 24, 168, 720, 4,368, and 4,416 hours.
5. Download `experiments/colab/results/dcopf_benchmark_results.csv` and `dcopf_benchmark_results.json` from the cloned runtime directory after the run. These files are checkpoints, so an interrupted run can be resumed by rerunning the notebook.

The setup cell fails with an actionable error if `highspy` cannot be imported. A successful package installation alone is not treated as a successful solver run.

## What is measured

The no-storage benchmark has three execution variants:

| Variant | Meaning |
| --- | --- |
| `serial` | One prepared-period solve at a time with the artifact and static topology caches cleared before each hour. This is a deliberately cold serial baseline. |
| `sparse_reuse` | The same public hourly solve with the prepared artifact and static network indexing warmed and reused. This is prepared-data/topology reuse; it does not claim that HiGHS reuses a factorization or model matrix. |
| `chunked_parallel` | Existing threaded worker support, with each worker processing a deterministic chunk of hourly period indices. |

The `storage_horizon` variant calls the existing coupled `solve_dcopf_horizon` path with one small team-owned four-hour storage asset. All six storage horizons run by default when `highspy` is available. A user may explicitly set `STORAGE_MAX_HORIZON` to skip longer cases; every explicit skip is recorded with `run_status=skipped` and a reason. There is no implicit 720-hour cap. The coupled storage solve is one horizon-level MIP, so per-hour median/P95 solve times are recorded as null rather than inventing an hourly decomposition.

Each measured row records the horizon, artifact block, variant, storage flag, repeat index, wall time, median/P95 hourly solve time when available, sampled peak process RSS and Python allocation peak, RSS sampling interval and limitation, worker count, pre-presolve variable and constraint counts, network-variable count, solver status, exact prepared solver path, and any error/skip reason. Repeated runs are configured separately for short and long horizons; the output also includes per-variant median and P95 wall-time statistics across repeats.

The notebook loads the prepared artifact with `load_prepared_artifact`, uses source status/PMIN/nameplate PMAX limits without synthetic hourly availability caps, and then calls `solve_dcopf` or `solve_dcopf_horizon`. This is the exact benchmark path; the production prepared entry points additionally require a validated manifest, while the checked-in benchmark artifacts are intentionally fail-closed until release validation.

## Reproducibility controls

The first cell exposes `REPO_URL`, `REPO_REF`, and `ARTIFACT_ROOT` overrides. `CHECKPOINT_DIR` changes the checkpoint location and `RSS_SAMPLING_INTERVAL_S` changes the process-RSS polling interval. The notebook prints the repository revision, Python version, platform, HiGHS version, and benchmark configuration. The benchmark is deterministic in period ordering and uses the checked-in scenario artifacts; it does not include precomputed outputs.

After each case, JSON and CSV are rewritten through temporary files and `os.replace`. Each row carries a stable `configuration_signature` derived from the artifact paths and manifest identities, official horizons, storage policy/max horizon, variants, solver/model revision, and sampler settings. On resume, rows are matched by both that signature and the stable horizon/variant/repeat `case_key`; missing-signature, mismatched, or legacy rows are stale and are recomputed. Completed, errored, infeasible, and explicitly skipped rows are preserved only when they belong to the active configuration. JSON is preferred, with CSV as a recovery fallback; CSV values are restored to typed integers, floats, and booleans before recommendation logic runs.

For a quick notebook smoke test, set `REPEATS_SHORT = 1`, `REPEATS_LONG = 1`, and temporarily choose a short horizon in `BENCHMARK_HORIZONS`. Restore the required six-horizon tuple before treating a run as a benchmark report.

## Interpreting the official horizon

The notebook prints a performance recommendation only from measured 4,416-hour no-storage `sparse_reuse` rows with `run_status=completed`, `solver_status=optimal`, and numeric wall-time/RSS fields. Missing, non-optimal, skipped, errored, or dependency-limited evidence produces `dependency_limited_unverified`. If verified results exceed a budget, the output recommends screening or shrinking only as an operational fallback while retaining full 4,416-hour remote instructor evaluation; it does not change evaluator semantics.

No result is asserted in this repository. Do not copy timings, memory values, solver statuses, or horizon recommendations from an unrun notebook into a report.

The student-laptop fallback is a recommendation for local screening only. It must not be used to change the official scoring horizon or production evaluator behavior.
