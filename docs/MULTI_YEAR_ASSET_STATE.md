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
existing `evaluate_csv_batch()` API remains unchanged; this coordinator is an
opt-in multi-round wrapper.

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
