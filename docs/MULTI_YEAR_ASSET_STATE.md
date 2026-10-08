# Multi-year team-asset state: Stage 1

Stage 1 defines the persistence contract for the multi-round game. It does
not change the existing single-half-year dispatch evaluator yet.

## Contract

`AssetLedger` stores one `TeamAssetState` per team. Each state records:

- team-owned generator IDs, connection IDs, source bus IDs, capacity, and
  operating-cost parameter;
- team-owned storage IDs, connection IDs, source bus IDs, power capacity, and
  energy capacity;
- the most recent round in which the team state was updated.

Asset IDs are scoped to a team. Two teams may both choose `gen-1`; they are
different assets because ownership is part of the key. Within one team, an ID
is immutable after it is first recorded: changing its site or capacity raises
`AssetStateError` instead of silently moving or resizing an existing asset.

Registering a later plan preserves previously recorded assets and adds newly
declared assets. The ledger is immutable from the caller's perspective and can
be serialized with `to_dict()` and restored with `from_dict()`.

## Deliberate non-goals

This stage does not yet:

- modify CSV submission semantics;
- automatically inject persisted assets into a later submission;
- alter DCOPF, storage state-of-charge, or scoring;
- define the instructor's round-transition policy.

Those behaviors belong to later stages and will consume this versioned state
contract. Keeping the contract separate first prevents asset persistence from
changing current single-round results accidentally.

## Stage 2: ordered game state

`GameState` now wraps the ledger with the released round sequence:

```text
2027_jan_jun -> 2027_jul_dec ->
2028_jan_jun -> 2028_jul_dec ->
2029_jan_jun -> 2029_jul_dec
```

Calling `advance(round_key, plans)` registers that round's plans and returns a
new state. The transition is rejected if a round is skipped, repeated, moved
backward, or outside the released `2027`--`2029` package. The state can be
saved and restored with `to_dict()` and `from_dict()`; later evaluator stages
will use this boundary to load the prior round's team assets before dispatch.

## Stage 3: durable state store

`GameStateStore(path)` persists a `GameState` as deterministic UTF-8 JSON.
Saving writes to a temporary file in the target file's directory, flushes it,
and atomically replaces the requested path. A failed write cleans up the
temporary file and leaves the previous target intact. Loading a missing file
raises `FileNotFoundError`; malformed JSON or an unsupported/invalid state is
reported as `GameStateStoreError`.

## Stage 4: round evaluation coordinator

`evaluate_round()` is the transactional boundary around the existing
single-half-year evaluator. It:

1. loads the prior `GameState`, or starts a new one;
2. validates and collects the current round's complete CSV plans using the
   existing strict submission path;
3. checks that the requested round is the next released round and that asset
   identities do not conflict;
4. invokes the existing batch evaluator without changing its DCOPF or scoring
   behavior; and
5. saves the advanced state only after a usable dispatch result is returned.

Plan-loader validation failures, asset conflicts, evaluator exceptions, and
unusable dispatch results do not overwrite the prior state file. Individual
rejected submissions remain isolated by the existing batch evaluator; valid
teams can still be evaluated and their accepted assets committed. The
the existing evaluator's one-round behavior remains unchanged; this
coordinator is an opt-in multi-round wrapper.

## Stage 5: active-portfolio materialization

`AssetLedger.materialize_plan(plan, horizon=...)` builds the evaluator-facing
portfolio for one team without changing the single-round evaluator. The current
submission remains the source of truth for its declared connection-site
metadata and for newly declared generator/storage specifications. The method
then restores every generator and storage asset already persisted for that
team, even when the current CSV omits those assets.

When a persisted asset's connection site is omitted from the current plan, the
method adds a deterministic placeholder connection using the asset's persisted
source bus, zero connection capacity, and an all-zero profile of the requested
horizon. This keeps the owned asset visible to the evaluator without inventing
new demand. If the site is present, its current connection metadata is retained;
the persisted source bus must still match the asset's immutable record.

The ACTIVSg2000 storage adapter treats such a zero-load placeholder as an
inactive site: storage attached to it remains idle for the requested horizon,
with zero charging, zero discharging, and state of charge fixed at its carried
state. Storage at active sites retains the normal continuous peak-shaving
schedule.

Materialization also:

- accepts an exact re-declaration of an existing asset;
- raises `AssetStateError` for an immutable-specification conflict, a missing
  current asset connection, duplicate current connection IDs, or a non-positive
  horizon; and
- returns connections, generators, and storages in deterministic ID order.

The API is pure from the caller's perspective: it does not mutate the ledger,
commit a round, run DCOPF, or compute scores. It is an input-building boundary
for a later multi-round evaluator integration. Stage 5 does not change
`evaluate_csv_batch()`, CSV parsing semantics, storage state-of-charge, capital
cost accounting, or scoring.

## Stage 6: active-portfolio evaluation and one-time capital accounting

Stage 6 connects the persistent ledger to the round evaluator without changing
the default one-round CSV behavior. `evaluate_round()` now uses the prior
round's ledger to materialize each accepted team's active portfolio before
dispatch. If a later CSV omits a previously built generator or storage asset,
that asset is restored for dispatch and operational scoring. Malformed or
rejected submissions are never materialized and never enter the ledger.

`evaluate_csv_batch()` exposes this behavior through two opt-in arguments:

- `plan_overrides` maps accepted team IDs to evaluator-facing `TeamPlan`
  objects. CSV records, input hashes, and rejection records still come from
  the submitted files; overrides affect only dispatch and operational scoring.
- `capital_cost_scope` is a `CapitalCostScope` containing the generator and
  storage IDs newly constructed in the current round for each team.

With no overrides or capital scope, existing callers retain the original
behavior: every generator and storage present in the parsed plan is charged its
capital cost. With the Stage 6 scope, only the listed newly constructed assets
are charged generator/storage capital cost. Generator and storage operational
costs are always calculated from the full materialized portfolio, including
assets carried over from earlier rounds.

The current implementation intentionally leaves `site_cost` round/submission-
scoped. `evaluate_round()` passes the number of connections declared by the
current submission through `site_cost_connection_count_by_team`, so restored
placeholder connections do not incur site cost a second time. Direct
`evaluate_csv_batch()` callers that omit this scope retain the original
behavior of charging every connection in the evaluator-facing plan. A future
connection-persistence stage must define persistent site accounting separately
rather than inferring it from the current asset IDs.

The round coordinator remains transactional: it materializes and prepares the
advanced state in memory, evaluates the joint plans, and saves the new state
only after a usable evaluation result is returned. An evaluator exception or
unusable dispatch (including a missing or `None` `periods` collection) therefore
leaves the prior state file unchanged.

Because silently evaluating raw plans would defeat persistence, `evaluate_round()`
requires a Stage 6-aware evaluator that accepts `plan_overrides`,
`capital_cost_scope`, and `site_cost_connection_count_by_team` (or accepts
arbitrary keyword arguments). A legacy evaluator with an explicit signature
that lacks any of these arguments is rejected before it is called, and the
state file is not advanced.

## Stage 7: persistent storage state of charge

Storage construction specifications remain immutable in `StorageAsset`, while
the mutable `state_of_charge_mwh` field records the end-of-round energy carried
by that asset. A newly constructed storage asset starts at one-half of its
energy capacity. Ledgers written before Stage 7, which do not contain this
field, load compatibly with the same half-full default.

`StoragePlan` has an optional internal
`initial_state_of_charge_mwh` field. It is omitted by the public CSV schema and
defaults to `None`, which preserves existing positional constructors and
one-round behavior. Materialization injects the persisted SOC into both
omitted and exactly re-declared existing storage assets; a new current-round
asset keeps the default `None` and therefore starts half full in the adapter.

The ACTIVSg2000 adapter validates an explicitly supplied initial SOC, starts
its continuous ideal-efficiency schedule from that value, and keeps inactive
zero-load placeholders idle at that value. Discharge cannot exceed the current
SOC, and a valid schedule must finish at or above one-half of the storage
energy capacity.

After a usable round evaluation, `evaluate_round()` reads the final dispatch
period's `storage_soc_mwh_by_team` mapping. For failure-isolated partial
dispatches, `BatchDispatchResult.team_ids` defines which teams were actually
dispatched: only materialized storage-bearing teams present in that set must
report every final SOC. A materialized storage-bearing team absent from the
set retains its advanced-state SOC, whether carried from an earlier round or
defaulted half full for a new asset, so valid teams can still commit. Missing
or malformed SOC for a dispatched storage team remains a transactional
failure: the state file is not saved and the prior state remains intact.
Valid final SOC values are then written to the next immutable ledger snapshot
and are available to the next released half-year round.
