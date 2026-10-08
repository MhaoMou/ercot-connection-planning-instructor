# Task 6 Fair Projection Repair Design

## Goal

Repair the continuous joint-load projection so it maximizes total served load
over team service fractions, applies a deterministic anti-manipulation cap, and
selects a proportional-fair solution among total-load-optimal solutions. The
repair must preserve valid-team isolation, the existing dispatch feasibility
oracle, alpha-as-a-team-service-fraction semantics, and fixed-load dispatch
without integer curtailment.

## Projection contract

For each valid team, the projection creates one fraction `alpha_i` in `[0, 1]`
and scales every connection capacity in that team's capped plan by `alpha_i`.
The only feasibility decision is the existing `_dispatch_plans` oracle. A
candidate is feasible when that oracle returns a dispatch and infeasible when it
raises one of the existing infeasibility/unresolved/value errors.

Before optimization, each connection is capped at the fixed site cap. The
resulting team request is then capped at the smaller of the fixed absolute team
cap and the smallest positive post-site-cap request among eligible teams. Thus
the cap is fixed throughout one projection, deterministic from valid requests,
and an oversized request cannot exceed a small request merely by declaring a
larger capacity. Zero-request teams remain in the valid-plan isolation boundary
but are not eligible for the positive-service fairness objective.

## Exact-to-tolerance optimization

The fixed-load feasibility region is treated as coordinatewise downward closed:
if a vector of fractions is feasible, every componentwise smaller vector is
feasible. This is the monotonicity contract of the projection's fixed-load
oracle. The optimizer uses a deterministic branch-and-bound over axis-aligned
boxes in `[0, 1]^n`:

1. The lower and upper corners of each box are checked through the existing
   oracle. An infeasible lower corner certifies the whole box infeasible; a
   feasible upper corner certifies the whole box feasible and supplies the
   box's exact best linear objective value.
2. Otherwise, `dot(requested_load_mwh, upper_corner)` is a valid upper bound.
   Boxes whose upper bound cannot improve the incumbent by the configured
   total-load tolerance are discarded.
3. The remaining box with the greatest upper bound is split at its widest
   deterministic dimension, with team ID as the tie-break. The process stops
   only when every unexplored box is within tolerance of the best feasible
   total load.

The first pass therefore returns a feasible vector whose total projected load
is within the explicit tolerance of the global maximum for the oracle's
downward-closed region. It does not use greedy coordinate ordering, random
sampling, or integer curtailment. Its worst-case cost is exponential in the
number of teams and can be high for a pathological oracle; this is preferable
to silently returning an uncertified candidate.

For exactly two teams, the same monotone/convex oracle contract reduces the
search to a one-dimensional upper-boundary function: for each first fraction,
the largest feasible second fraction is found by bisection, and deterministic
ternary refinement maximizes the resulting concave total-load function. The
proportional-fair pass uses the same boundary parameterization and log utility.
This avoids unnecessary high-dimensional box refinement while retaining the
same tolerance guarantee for the two-team linear/convex constraints used by
the no-storage dispatch model. Two-team storage cases use the general
branch-and-bound path because their binary operating-mode oracle is not convex.

The second pass retains only boxes that can reach the total-load-optimal band
and applies the same certified search to the strictly increasing utility
`sum(log(requested_load_mwh_i * alpha_i))`. A positive common-service seed is
found by deterministic bisection. If no positive vector exists in the
total-optimal band, the diagnostic utility remains `-inf` rather than being
converted to zero; this distinguishes undefined proportional fairness from a
zero-valued finite utility.

## Diagnostics and compatibility

Requested and projected load diagnostics are energy values: every MW-period
term is multiplied by `time_step_hours`. The optimizer's ordering is unchanged
by this positive common scale, but the reported MWh and tolerance are correct.
The existing `FairProjectionResult` tuple compatibility, `service_fraction`
dispatch field, team isolation, and fallback behavior remain unchanged.

## Tests

Task 6 tests will cover:

- a coupled three-team oracle with pairwise `alpha_i + alpha_j <= 1`, proving
  total-load optimum `1.5` and proportional-fair fractions `(0.5, 0.5, 0.5)`;
- a coupled non-greedy total-load optimum;
- positive service for every eligible team when the total-optimal face permits
  it, plus preservation of `-inf` when it does not;
- 10,000-versus-1 MW anti-manipulation requests;
- `time_step_hours` conversion to MWh;
- valid-team isolation and unchanged no-integer-curtailment semantics.
