# Hosting-capacity solver

## 1. Definition: `H_{s,t}`

For candidate bus `s` and operating period `t`,

```text
H_{s,t} = max h
```

where `h >= 0` is additional demand at bus `s`, and the existing
generators redispatch optimally subject to the period's DCOPF balance,
generator, angle, and branch constraints. An `optimal` result is the maximum
feasible value of `h`, in MW. The period's base demand, fixed injections,
network, and existing generator fleet remain the inputs to that solve.

## 2. Non-simultaneous interpretation

The solver evaluates one candidate bus at a time. Each `H_{s,t}` assumes that
the additional load is placed only at `s`; it does not add the capacities of
several buses to one joint operating point. A column or row of individually
computed capacities is therefore not a simultaneously feasible portfolio.

## 3. Included inputs

Each normalized period contains:

- base load at every bus;
- existing generator locations, PMIN/PMAX limits, and marginal costs;
- branch topology, susceptance, and thermal limits;
- exactly one reference bus (the first bus is used when the input does not mark
  one);
- fixed injections by bus, including prepared-artifact injections; and
- source and dataset metadata used for provenance and checkpoint identity.

Requested periods must use the same bus order and fixed network topology. Load,
generator limits, branch limits, fixed injections, and other operating values
may vary by period.

## 4. Excluded first-release inputs

The first release does not model student-built generators, student-built
storage, or intertemporal storage state. It is a separate diagnostic over the
existing DCOPF model; it does not change batch evaluation, student CSV
semantics, storage dispatch, fair projection, or the RL environment. The
underlying dispatch also has no load-shedding or curtailment variable, so
inelastic demand must be served by the supplied model.

## 5. Solver behavior and exactness boundary

The candidate load uses the existing nodal-balance sign convention: its LP
column has coefficient `-1.0` in the selected bus balance row, while its
nonnegative value `h` is maximized. Thus a reported capacity is positive
additional load, not a signed injection. The base case is checked first, and
then the candidate-demand probe is solved against the same period model.

For a period, the driver constructs one reusable LP and activates one candidate
bus at a time. The reusable model changes the balance right-hand sides and
candidate column objective/bounds between probes, then restores the ordinary
generator objective and candidate bounds. Independent periods may run in
separate processes; each worker constructs its own solver model, and no solver
object crosses a process boundary. Candidate buses within one period are not
parallelized.

HiGHS is the portable default backend. The CLI accepts `highs`, `gurobi`, or
`auto`; `auto` selects licensed Gurobi when it is available and otherwise
resolves to HiGHS. Gurobi is optional and is never imported at module import
time. `workers > 1` cannot be combined with `--solver-threads > 1`, avoiding
uncontrolled nested parallelism.

The first release is exact relative to the supplied LP formulation and the
selected solver's tolerances. Reusing one LP structure is an implementation
optimization, not a mathematical approximation. PTDF screening, active-set
reuse, bus grouping, and parametric approximations are not silently enabled.
The metadata records the formulation (`one_bus_additional_demand_v1`), backend,
backend version when available, solver threads, and `warm_start_used: false`.

## 6. Statuses and failed points

Every point has one of these closed statuses:

- `optimal`: a finite maximum additional demand was returned;
- `infeasible_base_case`: the period is infeasible before adding candidate
  demand;
- `unbounded`: the candidate-demand maximization is unbounded under the
  supplied limits;
- `numerical_failure`: the backend reported a numerical failure or returned an
  invalid optimal value;
- `invalid_input`: the period, bus, limits, or solver configuration failed
  validation;
- `solver_error`: the backend failed or returned an ambiguous/unsupported
  result; and
- `interrupted`: the solve was interrupted, for example by `Ctrl-C`.

For a non-`optimal` point, the capacity and objective entries are `NaN` in the
NumPy outputs and the capacity is blank in the optional long-form CSV. The
status remains visible, and diagnostics are retained in metadata when present.
An interrupted or otherwise partial run leaves pending matrix cells with an
empty status until they are solved; metadata reports `complete: false` and
separates completed, failed, and pending counts.

## 7. Output files and deterministic order

The CLI writes these files under `--output-dir`:

| File | Contents |
| --- | --- |
| `hosting_capacity_mw.npy` | Float matrix with shape `(period_count, bus_count)`; `NaN` for non-optimal or pending points. |
| `hosting_capacity_status.npy` | String matrix with the same shape; status values, or an empty string for pending points. |
| `hosting_capacity_metadata.json` | Schema/formulation, source and scenario identity, backend/settings/tolerances, counts, diagnostics, completion flag, and ordering metadata. |
| `hosting_capacity_summary.csv` | One row per period with the exact columns `period_index,optimal_count,failed_count,min_hosting_capacity_mw,median_hosting_capacity_mw,max_hosting_capacity_mw`. |
| `hosting_capacity_long.csv.gz` | Optional output created by `--write-long-form`, with columns `period_index,bus_id,hosting_capacity_mw,status,solve_time_s`. |

Rows of the matrix and summary are in ascending requested `period_index` order.
Columns are in sorted unique `bus_id` order. The long-form CSV is ordered by
period first and then by that same sorted bus order. The metadata records these
lists under `ordering.period_indices`, `ordering.bus_ids`, and
`ordering.array_axes: ["period_index", "bus_id"]`; do not infer bus order from
the filesystem or from solver completion order.

## 8. Checkpoint identity and safe resume

When checkpointing is enabled, the default directory is
`<output-dir>/checkpoints`. Each completed period is written atomically as
`period_<period_index>.npz`. The write uses a temporary file, flushes and
syncs it, and then replaces the final path, so an interrupted write is not
treated as a completed period.

The checkpoint identity covers the schema version, formulation version, sorted
requested period indices, sorted candidate bus IDs, resolved solver backend,
solver-thread count, and a source identity digest. The source identity includes
the normalized period inputs and source metadata, including artifact/scenario
identity. A checkpoint is reusable only when its period index, identity,
completion marker, array shapes, statuses, solve times, and optimal values all
validate. Worker count is recorded in output metadata but is not part of the
mathematical checkpoint identity.

To resume safely, preserve the output and checkpoint directories, keep the same
artifact/source and formulation, and rerun the same period, bus, backend, and
solver-thread request. Valid completed-period checkpoints are loaded and only
pending periods are solved; outputs are republished after each completed period.
An identity mismatch is rejected rather than silently reused. Malformed or
incomplete checkpoint files are ignored and recomputed. Do not copy checkpoints
between artifacts, horizons, bus selections, or backend/thread configurations.

## 9. IEEE-14 example

Run the bundled one-period IEEE-14 smoke case with HiGHS:

```bash
ercot-game-hosting-capacity \
  --ieee14 \
  --horizon 1 \
  --solver-backend highs \
  --workers 1 \
  --output-dir results/hosting_capacity_ieee14
```

This case is a correctness and output-shape smoke test, not a production-scale
performance benchmark.

## 10. ACTIVSg2000 24-hour smoke test

After the prepared ACTIVSg2000 block has passed its validation contract, solve
the first 24 periods of the 2028 January--June block:

```bash
ercot-game-hosting-capacity \
  --artifact-dir data/processed/2028/jan_jun \
  --horizon 24 \
  --period-count 24 \
  --solver-backend highs \
  --workers 1 \
  --output-dir results/hosting_capacity_2028_jan_jun_24h
```

Use `--write-long-form` when a row-oriented CSV is needed for inspection. The
artifact directory must be a validated prepared block; a missing, invalid, or
unverified manifest is rejected before the matrix run.

## 11. Full-horizon command and warning

The following is the planned 2028 January--June full-horizon command:

```bash
ercot-game-hosting-capacity \
  --artifact-dir data/processed/2028/jan_jun \
  --horizon 4368 \
  --solver-backend auto \
  --workers 4 \
  --output-dir results/hosting_capacity_2028_jan_jun
```

**Warning:** with the default all-bus selection, this requests approximately
8.7 million point solves: 2,000 candidate buses × 4,368 periods = 8,736,000
independent `H_{s,t}` values. Run the IEEE-14 and 24-hour stages first, verify
the output and checkpoint behavior, and launch the full horizon only after
reviewing measured resource use. The command is resumable, but resumability
does not make the workload small.

## 12. Benchmark interpretation

Use `tools/benchmarks/benchmark_hosting_capacity.py` with a caller-selected
output directory for staged measurements. Report measured wall time and peak
RSS, together with backend/version, worker and solver-thread counts, mean/P50/P95
point solve time, and completed/failed point counts. A failed, interrupted, or
dependency-limited stage is an explicit result, not a timing estimate.

The IEEE-14 case measures plumbing and solver correctness on one tiny period; do
not extrapolate its wall time or memory to ACTIVSg2000 production runs. Interpret
production performance only from measurements using the target prepared
artifact, requested horizon, candidate-bus count, backend, and parallelism.
