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
