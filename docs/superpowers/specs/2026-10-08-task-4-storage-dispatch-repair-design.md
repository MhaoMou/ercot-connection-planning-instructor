# Task 4 Storage Dispatch Repair Design

## Goal

Make the centralized multi-period storage DCOPF enforce the published storage
contract at the solver boundary: persisted initial and terminal state of charge
must be in `[0.5B, B]`, an asset cannot charge and discharge simultaneously,
and discharge can serve only its owning team's load at the same bus.

## Scope and invariants

- Keep ideal storage efficiency (`eta = 1`), existing power and energy bounds,
  discharge limited by the SOC at the start of the period, and existing result
  and persisted-final-SOC exposure.
- Preserve the existing omitted-initial-SOC behavior: the adapter and ledger
  continue to resolve omitted initial SOC to half-full. Explicit invalid
  persisted values are rejected at the solver boundary as well as at plan and
  ledger validation boundaries.
- Do not change scoring, economic costs, projection, fairness, manual content,
  or the deterministic non-artifact compatibility scheduler.

## Formulation

The existing implementation uses HiGHS as a linear-programming DCOPF path. It
will keep that path and replace each internal pair `(charge, discharge)` with a
single signed continuous storage-power variable `net_storage_mw`:

- `net_storage_mw > 0` means discharge;
- `net_storage_mw < 0` means charge;
- `-P <= net_storage_mw <= P`;
- `soc[t] = soc[t-1] - net_storage_mw[t] * Δt` (or the persisted initial SOC
  for the first period);
- `net_storage_mw[t] <= soc[t-1] / Δt` (or initial SOC divided by Δt) enforces
  discharge availability;
- the final SOC variable has lower bound `0.5B`, while every SOC remains at
  most `B` and at least `0`.

This is a safe continuous representation because the positive and negative
parts of one scalar cannot both be positive. The public result still exposes
the existing nonnegative charge and discharge maps by splitting the solved
signed value, so downstream contracts remain compatible. A binary mode is not
needed and would turn this large network LP into a MIP.

For team dedication, group storage assets by `(team_id, bus_id)` and add one
row per group and period:

`sum(discharge[team, bus, t]) <= owner_load[team, bus, t]`.

The batch adapter already provides the required per-team/per-bus base-load
maps. A storage asset at a bus with no same-team load therefore cannot
discharge, even if another team has load at that bus or the owning team has
load at another bus.

## Tests

- Solver-independent tests cover invalid initial SOC, invalid dimensions,
  owner-load shape/value validation, and the grouping semantics used to build
  per-team/per-bus caps.
- HiGHS-backed tests cover terminal SOC, persisted initial SOC, no simultaneous
  charge/discharge, same-team same-bus caps, and cross-team/cross-bus loads.
  The whole solver-backed module is skipped only when `highspy` is unavailable.
- Existing focused and full suites are run, with the pre-existing manual build
  artifacts left untouched.
