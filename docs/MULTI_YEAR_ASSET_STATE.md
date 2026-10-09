# Multi-year team-asset state

This document describes the active persistent-state contract implemented by the
round coordinator. It is not a proposal or a pre-integration stage note.

## Three weekly matches

The released classroom sequence is six half-year submissions grouped into three
two-submission matches:

| Week | Submission 1 | Submission 2 | State treatment |
|---|---|---|---|
| Week 1 | `2027_jan_jun` | `2027_jul_dec` | State persists within the week; feedback-only and unranked. |
| Week 2 | `2028_jan_jun` | `2028_jul_dec` | State persists within the week; independent scored match. |
| Week 3 | `2029_jan_jun` | `2029_jul_dec` | State persists within the week; independent scored match. |

The coordinator requires the six rounds in order. It rejects a second submission
when the first submission for that week has not committed successfully. It
resets the asset ledger at the start of Weeks 2 and 3. Week 1 produces feedback
for both submissions but contributes no score to the final ranking. Submission
2 of Weeks 2 and 3 supplies the weekly rankings; final rankings aggregate valid
team scores across those two scored submissions.

## Canonical plan and evaluator-assigned assets

The active public CSV has exactly one row per site:

```text
team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh
```

It contains no hourly rows, public generator IDs, storage IDs, or SOC fields.
For every positive team-owned generator or storage capacity, the evaluator
derives a deterministic private asset ID from the team and site identity.
Assets are owned by the submitting team and cannot serve another team.

The serialized ledger stores immutable team/site/kind identity, source bus,
capacity, operating-cost metadata where applicable, and the last committed
round. An asset identity conflict raises `AssetStateError`; an existing asset
cannot silently move or resize. The ledger is immutable from the caller's
perspective and is written as deterministic UTF-8 JSON.

## Transactional round boundary

`evaluate_round()`:

1. loads the prior state or starts a new state;
2. resolves the released week and submission number;
3. resets the ledger when the week changes;
4. validates the current canonical CSV files and isolates rejected files;
5. materializes the active portfolio for each accepted team;
6. passes the materialized plans and one-time capital-cost scope to the evaluator;
7. records final storage SOC only after a usable dispatch; and
8. atomically saves the advanced state.

A plan-loader failure, asset conflict, evaluator exception, unusable dispatch, or
missing/malformed final SOC leaves the prior state file unchanged. Accepted
teams can still be evaluated when another submitted file is malformed.

The evaluator-facing materialized portfolio includes assets from Submission 1
even when Submission 2 omits them. A persisted asset whose site is omitted is
represented by a deterministic zero-load placeholder at its immutable bus, so
the asset remains owned and auditable without inventing new demand. An exact
re-declaration is accepted; a changed bus, site identity, or immutable capacity
specification is rejected.

## Capital-cost accounting

Within one weekly match:

- generator capital is charged once for each newly constructed evaluator asset;
- storage capital is charged once for each newly constructed evaluator asset;
- connection-site cost is charged once for newly declared connection capacity;
- operational generator cost uses the complete materialized portfolio, including
  assets carried from Submission 1;
- storage operating cost is not charged.

At the next weekly boundary, the ledger is reset and the next match starts with
new construction accounting. Direct one-round `evaluate_csv_batch()` callers
without a capital scope retain their original all-assets-charged behavior; the
classroom coordinator supplies the scope needed for weekly accounting.

## Persistent storage state of charge

Storage construction fields remain immutable. The mutable
`state_of_charge_mwh` field records the final SOC of the latest committed
dispatch. A newly built storage asset starts half full. The public CSV never
contains SOC.

The prepared storage model is grid-operated, ideal-efficiency, and
team-dedicated. It enforces power limits, energy limits, one-hour SOC
transitions, binary no-simultaneous-charge/discharge mode, initial half-full
SOC, and terminal SOC at least half full. Discharge is capped to the owning
team's load.

After a usable evaluation, the coordinator reads the final dispatch period's
`storage_soc_mwh_by_team` mapping. Every dispatched storage-bearing team must
provide a finite SOC for every materialized storage asset. A storage asset in a
failure-isolated team that was not dispatched retains its advanced-state SOC.
The updated SOC is then written to the next state snapshot and is available
only to the next submission in the same week. Week reset discards it.

## Fair projection and state safety

Projection diagnostics are validated before state commit. Every diagnostic map
must contain only accepted materialized team IDs; rejected or unknown teams
cause the transaction to fail. Per-team service fractions, projected loads,
request caps, and proportional-fair utility are audit data, not student input.

Classroom output is split from raw audit output. The shared safe output contains
aggregate counts, aggregate periods, and aggregate projection metadata. Each
team's own scalar result is written under a hashed private directory. Exact
competitor sites, asset capacities, raw dispatch periods, and full failure
diagnostics remain instructor-private.

## Historical and dependency notes

The old long-form hourly CSV and submitted asset-label fixtures are retained
only where tests explicitly exercise compatibility parsing. They are historical
or legacy inputs and are not part of this state contract or student workflow.

The active scenario package currently remains
`unverified_dependency` because `highspy` is unavailable in the checkout.
Complete HiGHS-backed validation is required before classroom release. The
Colab horizon benchmark is a separate measurement package and requires
`highspy`, `psutil`, prepared artifacts, and a compatible runtime; it does
not change the state contract or evaluator math. Without a generated,
completed full-horizon result, it cannot support a performance claim or horizon
reduction.
