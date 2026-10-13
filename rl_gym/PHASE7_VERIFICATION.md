# Phase 7 Hourly Verification

**Scope:** Task 5 deterministic, solver-free benchmark evidence for the
single-agent hourly environment.

**Status:** The hourly contract and public-record behavior are evidenced by
tests. Computational-budget acceptance remains open because this report does
not define an approved machine-specific budget threshold. Solver-backed
evaluator parity is **DEPENDENCY-LIMITED**; this report makes no score claim.

## Contract under test

The benchmark uses the three-field action contract:

```text
{
  "generator_output_mw": finite scalar,
  "storage_charge_mw": finite non-negative scalar,
  "storage_discharge_mw": finite non-negative scalar
}
```

There is no hourly storage-energy or curtailment action. The exogenous boundary
is solver-free and contains only `scenario_year/scenario_block`. The benchmark
does not run a solver, DCOPF, dispatch, projection, or scoring path.

A one-time optional evaluator callback may be supplied by an application and
receives the frozen plan and release manifest at the terminal boundary. The
benchmark uses no callback, so its evaluator call count is deterministically
zero. Callback behavior is covered separately by the hourly tests.

## Deterministic benchmark method

`tests/test_rl_gym_hourly.py::test_phase7_deterministic_benchmark_evidence_covers_all_released_combinations`
runs one idle-action episode for each released year/block combination. Each
case uses a fresh frozen fixture plan and the deterministic seed derivation:

```text
seed = 70000 + (year - 2027) * 2
       + block_offset(jan_jun=0, jul_dec=1)
```

The test measures reset latency, mean per-transition step latency, full
episode latency including public-record serialization, and the UTF-8 byte size
of the default public record. Latencies are measurements, not fixed timing
claims; exact horizons, transition counts, evaluator counts, seeds, identity,
and output schema are asserted. All records are serialized with
`json.dumps(..., allow_nan=False)`.

## Evidence captured

The following values were captured from the deterministic benchmark helper on
the verification host. Latency values are seconds. `output_size_bytes` is the
size of the JSON-safe `public_sanitized` episode record.

| Year | Block | Horizon hours | Transitions | Evaluator calls | Seed | Reset s | Mean step s | Episode s | Output bytes | Environment | Release manifest digest |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 2027 | `jan_jun` | 4344 | 4344 | 0 | 70000 | 0.000040 | 0.000021 | 0.397302 | 4,738,946 | `phase7-hourly-v1` | `05e7fb302bee09496e2a8e3721f955e7f33161658c043082bcc934682a0d3500` |
| 2027 | `jul_dec` | 4416 | 4416 | 0 | 70001 | 0.000032 | 0.000020 | 0.407174 | 4,817,498 | `phase7-hourly-v1` | `048fa11c9d2babebfd45c5674621112ef0c4b6c2bb242545d61208cba98e6dbe` |
| 2028 | `jan_jun` | 4368 | 4368 | 0 | 70002 | 0.000035 | 0.000021 | 0.387673 | 4,765,130 | `phase7-hourly-v1` | `f457f3bf0381ba95bb470e9228951d7dd85b222ee545d98f5fa215d49be24c47` |
| 2028 | `jul_dec` | 4416 | 4416 | 0 | 70003 | 0.000032 | 0.000020 | 0.393829 | 4,817,498 | `phase7-hourly-v1` | `4a06aef5a11741321b693385a58bf84b60ff75828b24127058f2636bc0eab843` |
| 2029 | `jan_jun` | 4344 | 4344 | 0 | 70004 | 0.000031 | 0.000024 | 0.397870 | 4,738,946 | `phase7-hourly-v1` | `b0c92e39e49d8f2209d9f7a0981d172f6ac9eb897012a36dea61ec7c61eef12a` |
| 2029 | `jul_dec` | 4416 | 4416 | 0 | 70005 | 0.000032 | 0.000020 | 0.391861 | 4,817,498 | `phase7-hourly-v1` | `e5aeea9d320281afb6b7bdb03e3c0487ce8eb2cb222aacd9310753ad629d2a48` |

## Public-report privacy boundary

The benchmark report is an aggregate `public_sanitized` report. It contains
only release/environment identity, scenario metadata, exact transition and
evaluator counts, seed derivation, latency fields, and output size. It contains
no private actions, plans, asset IDs, storage schedules, evaluator objects or
outputs, solver objects, team scores, or raw diagnostics. The test serializes
the aggregate report with `allow_nan=False` and rejects those private keys.

The underlying public episode record is bounded and JSON-safe. It intentionally
does not include an action trace; explicit `include_private_actions=True`
creates a separate `private_record` for trusted local inspection and is outside
this benchmark report.

## Acceptance status

- [x] Exact released horizon and one-transition-per-hour counts are evidenced
      for all six year/block combinations.
- [x] Solver-free public records report environment/release identity, seed,
      evaluator call count, latency, and output size without private fields.
- [x] Default no-callback benchmark has deterministic `evaluator_call_count=0`
      and no official score claim.
- [ ] Computational budget: measurements are captured, but no approved
      machine/profile threshold or budget pass is claimed here.
- [ ] Solver-backed evaluator parity: **DEPENDENCY-LIMITED**; no available
      solver-backed fixture was used and no score claim is made.
