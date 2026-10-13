# Exact Multi-Storage Benders Evaluation Design

**Date:** 2026-10-13  
**Status:** Approved design; implementation pending  
**Scope:** Instructor-side ACTIVSg2000 dispatch and scoring

## Goal

Replace the memory-intensive monolithic storage-coupled half-year solve used by
the instructor evaluator with an exact continuous Benders decomposition that
supports multiple team-owned storage assets without changing the optimal
dispatch, feasibility result, or score mathematics.

## Existing problem

The production path currently evaluates storage-bearing submissions through a
single coupled horizon model containing network variables for every bus,
generator, branch, storage asset, and hour. The current storage formulation is
continuous: storage uses signed net-power variables and state-of-charge
variables, so the mathematical model is an LP under the approved assumptions
of ideal efficiency, no storage operating cost, and no value from simultaneous
charging and discharging.

The formulation is mathematically appropriate but its explicit model matrix is
too large for a 4,368- or 4,416-hour instructor evaluation. The recent remote
benchmark exceeded 24 GB resident memory before producing a result.

## Design principles

1. Official scores must use an exact optimization method.
2. A timeout or unresolved solver status must never be converted into an
   approximate official score.
3. The existing coupled horizon solver remains the small-fixture correctness
   reference.
4. The public CSV contract, storage ownership rules, terminal SOC rule, and
   scoring formulas remain unchanged.
5. The implementation must be deterministic for fixed inputs, solver settings,
   and tolerances.

## Recommended architecture

```text
CSV collection and validation
            |
            v
cheap structural / aggregate screen
            |
            +-- no storage --> reusable hourly DCOPF path
            |
            +-- storage -----> exact multi-storage Benders path
                                  |
                                  +-- master: storage and recourse variables
                                  +-- hourly reusable DCOPF subproblems
                                  +-- dual-based Benders cuts
                                  +-- exact optimality certificate
```

### No-storage route

Plans without storage continue to use the existing reusable single-period
DCOPF path. Independent hourly subproblems may run in deterministic chunks
with bounded worker count. This route does not introduce intertemporal state
variables.

### Storage route

Plans with one or more storage assets use generalized Benders decomposition.
All assets across all teams are included in one master problem, while each
hourly network recourse problem contains the complete 2,000-bus network and
all team loads and generators.

The master contains, for every storage asset `s` and hour `t`:

- signed net storage power `p[s,t]`;
- state of charge `e[s,t]`;
- one recourse-cost variable `theta[t]` for each hour;
- storage power bounds;
- SOC bounds;
- SOC transition equations;
- initial SOC constraints;
- terminal SOC lower bounds;
- any owner-load bounds needed to prevent storage from serving another team.

## Exactness

For a fixed master storage schedule, each hourly recourse problem is an LP.
The Benders master accumulates valid lower bounds on each hourly recourse value.
When the master objective and recourse objective meet within configured
tolerances and all recourse problems are feasible, the result is the same
optimum as the monolithic coupled LP.

The solver must return one of:

- `optimal`: exact result within configured feasibility and optimality
  tolerances;
- `infeasible`: the submitted joint plan cannot be dispatched under the
  evaluator's feasibility rules;
- `unresolved`: iteration limit, wall-time limit, numerical issue, or another
  solver termination condition prevented an optimality certificate.

Only `optimal` and `infeasible` may enter official scoring. `unresolved` must
be surfaced to the instructor as an evaluation failure requiring retry or
diagnosis.

## Parallelization and reuse

Hourly recourse subproblems are independent conditional on the master
schedule. The implementation may use a bounded thread pool, with one reusable
fixed-topology DCOPF model per worker. The initial configuration should use
one solver thread per worker to avoid oversubscription.

The implementation must preserve deterministic result ordering by sorting or
re-indexing recourse results by hour. Parallel execution may change runtime,
but not the returned mathematical solution within solver tolerances.

The reusable model must update hourly load and storage-injection right-hand
sides instead of rebuilding the network matrix. The implementation must not
retain one full 2,000-bus model per hour.

## Bounds and cuts

The master starts with physically valid storage-power and SOC bounds. Optional
per-hour feasibility bounds may be tightened only when they are proven valid
by the corresponding recourse problem. Any stronger bound must be derived
from storage capacity, SOC, local owner load, generator capacity, or a valid
network feasibility result; heuristic bounds cannot be used in the official
route.

The initial implementation should support:

- optimality cuts from recourse objective duals;
- feasibility cuts when an hourly recourse problem is infeasible;
- cut deduplication using a deterministic normalized representation;
- configurable feasibility and optimality tolerances;
- maximum Benders iterations;
- wall-clock deadline checks between recourse batches and master solves.

## Routing and compatibility

The production batch evaluator will select the route as follows:

1. Validate and materialize the submitted plans exactly as today.
2. If no storage assets are present, use the existing no-storage route.
3. If storage assets are present and the generalized Benders route is enabled,
   use Benders for the joint dispatch.
4. Retain an explicit reference/diagnostic option that invokes the monolithic
   coupled LP on small horizons or test fixtures.
5. Never silently fall back from an unresolved Benders solve to a different
   approximate solver for official scoring.

The existing one-storage Benders solver may be refactored into shared helpers,
but its exact behavior and tests must remain valid. The generalized route must
not change the CSV schema or require students to submit hourly storage
actions.

## Diagnostics

Every storage-aware production evaluation should record, at minimum:

- solver route;
- solver backend and version;
- horizon length;
- number of storage assets;
- number of Benders iterations;
- number of recourse solves;
- number of feasibility and optimality cuts;
- master objective;
- final recourse objective;
- absolute and relative optimality gap;
- maximum feasibility residual;
- wall time;
- peak resident memory when available;
- termination status and unresolved reason.

These diagnostics are instructor-facing and must not expose one team's private
strategy to another team.

## Error handling

- Malformed submissions remain isolated by the existing batch evaluator.
- Invalid storage dimensions remain validation errors.
- A recourse infeasibility must follow the existing joint-plan handling and
  fair continuous projection policy; Benders must not invent a new fairness
  policy.
- A numerical or time-limit termination must be marked `unresolved`, not
  treated as feasible or infeasible.
- The reference monolithic solver remains available for small parity tests and
  debugging, but not as the default full-horizon storage route.

## Testing strategy

Tests must be written before implementation changes and cover:

1. Multi-storage Benders reaches the same objective and final SOC as the
   monolithic LP on small fixed networks.
2. Two teams' storage assets remain team-owned and cannot discharge into the
   other team's load cap.
3. Initial and terminal SOC constraints match the existing rules.
4. Parallel recourse and serial recourse return equivalent results.
5. Valid bound tightening does not change the optimum.
6. Cut deduplication does not change convergence.
7. Wall-time and iteration limits return `unresolved`.
8. Batch routing selects the no-storage or Benders route correctly.
9. Official scoring rejects unresolved dispatch results rather than scoring
   them.
10. Existing no-storage, one-storage, projection, and scoring regression tests
    remain green.

## Non-goals

This design does not:

- change the game scoring function;
- change the student CSV format;
- change storage ownership or terminal SOC rules;
- introduce rolling-horizon approximation for official scoring;
- change the network reduction or dataset;
- require students to solve the official instructor evaluator locally;
- replace the exact result with sampled-hour or preview results.

## Acceptance criteria

Implementation is accepted when:

1. Small multi-storage cases match the monolithic LP within documented
   tolerances.
2. The instructor batch evaluator routes full-horizon storage plans through
   the generalized Benders path.
3. The route returns a bounded, explicit `unresolved` status on time limit.
4. The full 4,368-hour and 4,416-hour remote benchmarks complete with valid
   solver status, or produce reproducible evidence that the remaining blocker
   is elsewhere in the solver/model and not hidden by an indefinite run.
5. Existing regression tests remain passing.

