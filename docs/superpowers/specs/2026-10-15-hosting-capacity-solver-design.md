# Hosting Capacity Solver Design

**Status:** Approved design; implementation not started.

**Goal:** Compute the exact maximum additional demand that can be connected at every candidate bus in every operating period of a supplied power-network dataset while allowing existing generators to redispatch optimally.

## Scope and interpretation

For operating period \(t\) and candidate bus \(s\), the solver computes

\[
H_{s,t}=\max h_{s,t},
\]

where \(h_{s,t}\geq 0\) is additional demand connected at bus \(s\). Existing network topology, base demand, generator availability and limits, branch limits, and fixed injections come from the selected period. Existing generators, voltage angles, and branch flows are reoptimized through the DCOPF.

This is a one-bus-at-a-time hosting-capacity definition. The result \(H_{s,t}\) does not claim that all buses can simultaneously accept their individual capacities. A separate joint-injection problem would have a different meaning and is out of scope for the first release.

The first release excludes student-built generators, student-built storage, and multi-period storage state coupling. It is a network-capacity diagnostic based on the supplied base system. Storage support can be added later as a separate model variant without changing the default definition.

## Mathematical model

For each pair \((s,t)\), solve the Appendix A DCOPF with one additional load variable. For each bus \(i\), the nodal balance contains

\[
P^{\mathrm{gen}}_{i,t}-P^{\mathrm{load}}_{i,t}
-h_{s,t}\mathbf{1}_{i=s}
+P^{\mathrm{fixed}}_{i,t}
=P^{\mathrm{netflow}}_{i,t}.
\]

The model includes:

- generator bounds, including status and period-specific availability;
- DC branch-flow equations;
- positive and negative branch thermal limits;
- the reference-angle constraint;
- the nonnegative candidate-load variable \(h_{s,t}\);
- the objective to maximize \(h_{s,t}\).

The implementation must use the project’s existing power-flow sign convention and per-unit/base-MVA conversion. It must not introduce a second, inconsistent DCOPF formulation.

If the base period is infeasible, the result is “infeasible_base_case”. If the objective is unbounded, the result is “unbounded”; the implementation must not silently replace it with an arbitrary cap. Numerical failures and invalid inputs receive separate statuses.

## Architecture

### Normalized period input

The solver consumes a normalized period object independent of the source dataset:

- bus records and reference-bus information;
- generator records and period-specific limits;
- branch records and period-specific limits;
- base bus load;
- fixed injections such as DC-line injections;
- period index and source metadata.

An input provider returns one normalized object for each requested period. This keeps the solver reusable for both static IEEE-14 cases and time-varying ACTIVSg2000 scenario artifacts.

### Dataset adapters

The first adapters are:

1. **IEEE-14 adapter:** Reads the project’s normalized test-case representation and exposes one period, or multiple periods when an explicit profile is supplied.
2. **ACTIVSg2000 adapter:** Reads the validated prepared scenario artifact, including bus loads, generator limits, branch limits, DC-line data, and scenario metadata.

The solver must validate bus uniqueness, branch endpoints, generator endpoints, reference-bus existence, finite numeric values, and period-length consistency before starting a large run.

### Single-point solver

The core API is conceptually:

~~~python
solve_hosting_capacity_at_bus(
    period,
    candidate_bus_id,
    *,
    solver_backend="auto",
    solver_threads=1,
) -> HostingCapacityPoint
~~~

HostingCapacityPoint contains the period index, bus ID, capacity in MW, solver status, objective value, solve time, and optional binding-resource diagnostics.

The backend uses the existing solver abstraction and supports auto, highs, and gurobi. auto selects the best available supported backend without changing the model.

### Reusable period model

The driver must not rebuild the complete network LP for every candidate bus. For each period it should:

1. construct the DCOPF matrix once;
2. add candidate-demand variables associated with buses;
3. activate one candidate bus at a time by bounds/objective;
4. maximize that bus’s candidate-demand variable;
5. reuse the model and warm-start information when supported by the backend.

The reusable implementation must remain mathematically equivalent to rebuilding an independent LP for each bus. A fresh-model comparison is required in tests.

### Matrix driver

The matrix-level API is conceptually:

~~~python
compute_hosting_capacity_matrix(
    period_provider,
    *,
    bus_ids=None,
    period_indices=None,
    solver_backend="auto",
    solver_threads=1,
    workers=1,
    checkpoint_dir=None,
    output_dir=None,
) -> HostingCapacityMatrix
~~~

Periods are independent. Initial parallelism should therefore use processes across periods, with one solver model owned by each worker. Solver objects must not be shared across processes. Nested solver parallelism is disabled by default and must be explicitly configured.

## Persistence and outputs

The primary result is a dense capacity matrix indexed by period and bus. The output package contains:

~~~text
hosting_capacity_mw.npy
hosting_capacity_status.npy
hosting_capacity_metadata.json
hosting_capacity_summary.csv
~~~

An optional interoperability export is a compressed long-form file:

~~~text
hosting_capacity_long.csv.gz
~~~

with columns:

~~~text
period_index,bus_id,hosting_capacity_mw,status,solve_time_s
~~~

Metadata records the scenario/artifact version, source hashes, bus and period counts, solver backend and version, solver settings, tolerances, completed and failed counts, and whether the output is complete.

The driver checkpoints completed period blocks atomically. A restarted run validates the input identity and solver configuration before reusing checkpoints. It must reject checkpoints from a different artifact, bus ordering, horizon, or solver formulation.

## Validation strategy

### Unit and analytical tests

Use small cases that isolate:

- an uncongested system;
- one bottleneck branch;
- a generator-capacity bottleneck;
- parallel transmission paths;
- nonzero generator minimum output;
- a disconnected bus;
- an infeasible base period;
- an unbounded candidate injection.

### Reusable-model equivalence

For selected IEEE-14 and ACTIVSg2000 period/bus pairs, compare the reusable model with a freshly built LP. Capacity and feasibility status must agree within explicitly defined numerical tolerances.

### Boundary feasibility

For selected results \(H_{s,t}\), verify that:

1. the model is feasible at \(H_{s,t}\);
2. the model becomes infeasible or exceeds the solver optimality tolerance at \(H_{s,t}+\epsilon\);
3. the reported status and capacity are consistent with the solver status.

### Dataset checks

Before a production run, verify that the scenario block is validated, all candidate buses exist, all period profiles have the requested length, and no invalid numeric values enter the model.

## Performance evaluation

Benchmark in this order:

1. IEEE-14, one period, all buses;
2. ACTIVSg2000, one period, all buses;
3. ACTIVSg2000, 24 periods, all buses;
4. ACTIVSg2000, 168 periods, all buses;
5. ACTIVSg2000, the full 4,368-hour horizon.

Each benchmark records total wall time, model-build time, average and percentile bus-solve time, peak memory, worker count, solver backend, and completed/failed point counts. The full ACTIVSg2000 workload is expected to contain approximately 8.7 million bus-period LP solves, so checkpointing and resumability are release requirements rather than optional polish.

Only measured bottlenecks justify further acceleration. Possible later optimizations include PTDF-based screening, active-set reuse, parametric-LP methods, bus grouping, and backend-specific warm starts. Any approximation must be explicit and must not replace the exact default solver.

## CLI and documentation

The implementation should expose a command equivalent to:

~~~bash
python -m ercot_game.hosting_capacity \
  --artifact-dir data/processed/.../2028/jan_jun \
  --horizon 4368 \
  --solver-backend auto \
  --workers 4 \
  --output-dir results/hosting_capacity_2028_jan_jun
~~~

Documentation must explain the one-bus-at-a-time interpretation, optimized generator redispatch, dataset inputs, status values, output indexing, exactness guarantees, and the distinction between individual and simultaneous hosting capacity.

## Acceptance criteria

The feature is ready for implementation review when:

- the single-point LP matches Appendix A and the existing DCOPF sign convention;
- IEEE-14 analytical tests pass;
- reusable and fresh-model results agree on selected cases;
- ACTIVSg2000 period inputs load from validated artifacts;
- a partial matrix run can resume from a verified checkpoint;
- outputs contain complete metadata and deterministic bus/period ordering;
- HiGHS works without optional dependencies;
- Gurobi is supported when licensed;
- the full-horizon benchmark reports measured runtime and memory rather than an unverified estimate.

