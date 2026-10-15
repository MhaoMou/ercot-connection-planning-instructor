# Phase 7 Hourly Verification

**Scope:** Task 5 deterministic, solver-free benchmark evidence for the
single-agent hourly environment.

**Status:** The hourly contract and public-record behavior are evidenced by
tests. Native Gymnasium compatibility and a small solver-backed production
evaluator parity fixture are now verified remotely. Full half-year evaluator
latency, complete scenario-package validation, and computational-budget
acceptance remain open; this report does not claim that a 4,368/4,416-hour
storage-enabled run meets a release-time threshold.

## Machine profile and invocation

Evidence capture timestamp: **2026-10-12 EDT** (**2026-10-13T02:19:30Z**).
The profile contains no hostnames, credentials, or other secrets.

| Item | Value |
|---|---|
| OS | macOS 27.0, Darwin arm64 |
| CPU/platform | Apple Silicon arm64; 8 logical CPUs; `macOS-27.0-arm64-arm-64bit-Mach-O` |
| Python | CPython 3.13.9 (`/opt/anaconda3/bin/python`) |
| NumPy | 2.3.5 |
| pytest | 8.4.2 |
| Gymnasium | unavailable; fallback spaces were exercised |
| HiGHS Python bindings (`highspy`) | unavailable; no solver-backed path was run |

The local profile above is retained for reproducibility of the solver-free
benchmark. Additional remote evidence was captured on Electrify on
**2026-10-13** using CPython 3.10.12, Gymnasium 1.2.3, HiGHS Python bindings,
and the repository's copied ACTIVSg2000 artifacts. The remote run used an
isolated validation directory and did not modify the working repository.

The local-only solver status remains **DEPENDENCY-LIMITED**: Gymnasium and
`highspy` are unavailable in that profile, so the local benchmark makes **no score claim**.
The remote evidence below closes that limitation only for the
scoped smoke fixture, not for the complete release package.

Exact benchmark invocation:

```text
PYTHONPATH=src:. /opt/anaconda3/bin/python -m pytest -p no:cacheprovider \
  tests/test_rl_gym_hourly.py::test_phase7_deterministic_benchmark_evidence_covers_all_released_combinations -q
```

focused benchmark result: `1 passed in 2.95s`

The complete verification invocation was:

```text
PYTHONPATH=src:. /opt/anaconda3/bin/python -m pytest -p no:cacheprovider \
  tests/test_documentation_consistency.py tests/test_rl_gym_*.py -q
```

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
`json.dumps(..., allow_nan=False)`. The deterministic output bound is
`output_size_limit_bytes = 64,000 + 2,048 * horizon_hours`; this is a
horizon-tied test bound, not a machine timing threshold.

The aggregate public benchmark record has exactly these fields:

```text
privacy, environment_version, release_identity, scenario_year,
scenario_block, horizon_hours, transition_count, evaluator_call_count, seed,
seed_derivation, reset_latency_seconds, step_latency_seconds,
episode_latency_seconds, output_size_bytes, output_size_limit_bytes
```

`release_identity` has exactly `release_version`,
`release_environment_version`, `release_plan_schema_version`,
`release_scenario_year`, `release_scenario_block`, and
`release_manifest_digest`.

## Evidence captured

The following values were captured from the deterministic benchmark helper on
the verification host at the timestamp above. Latency values are seconds.
`output_size_bytes` is the size of the JSON-safe `public_sanitized` episode
record, and `Output limit bytes` is the deterministic horizon-tied bound.

| Year | Block | Horizon hours | Transitions | Evaluator calls | Seed | Reset s | Mean step s | Episode s | Output bytes | Output limit bytes | Environment | Release manifest digest |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 2027 | `jan_jun` | 4344 | 4344 | 0 | 70000 | 0.000040 | 0.000022 | 0.455974 | 4,738,946 | 8,960,512 | `phase7-hourly-v1` | `05e7fb302bee09496a2e8a3721f955e7f33161658c043082bcc934682a0d3500` |
| 2027 | `jul_dec` | 4416 | 4416 | 0 | 70001 | 0.000028 | 0.000020 | 0.456985 | 4,817,498 | 9,107,968 | `phase7-hourly-v1` | `048fa11c9d2babebfd45c5674621112ef0c4b6c2bb242545d61208cba98e6dbe` |
| 2028 | `jan_jun` | 4368 | 4368 | 0 | 70002 | 0.000027 | 0.000020 | 0.440178 | 4,765,130 | 9,009,664 | `phase7-hourly-v1` | `f457f3bf0381ba95bb470e9228951d7dd85b222ee545d98f5fa215d49be24c47` |
| 2028 | `jul_dec` | 4416 | 4416 | 0 | 70003 | 0.000030 | 0.000020 | 0.445709 | 4,817,498 | 9,107,968 | `phase7-hourly-v1` | `4a06aef5a11741321b693385a58bf84b60ff75828b24127058f2636bc0eab843` |
| 2029 | `jan_jun` | 4344 | 4344 | 0 | 70004 | 0.000027 | 0.000024 | 0.454260 | 4,738,946 | 8,960,512 | `phase7-hourly-v1` | `b0c92e39e49d8f2209d9f7a0981d172f6ac9eb897012a36dea61ec7c61eef12a` |
| 2029 | `jul_dec` | 4416 | 4416 | 0 | 70005 | 0.000028 | 0.000020 | 0.443501 | 4,817,498 | 9,107,968 | `phase7-hourly-v1` | `e5aeea9d320281afb6b7bdb03e3c0487ce8eb2cb222aacd9310753ad629d2a48` |

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

## Evaluator latency and parity status

The native Gymnasium checks pass on Electrify:

```text
77 passed, 7 warnings in 8.85s
gymnasium.utils.env_checker.check_env: CHECK_ENV_PASS
```

The warnings are Gymnasium's non-fatal Box-casting warnings. The focused
tests exclude the documentation fixture suite, which requires repository
artifacts not copied into the isolated validation directory.

The default deterministic benchmark still intentionally uses zero evaluator callbacks,
so its evaluator-latency field remains **OPEN/NOT MEASURED**; its
reset, step, and episode measurements are solver-free measurements only.

The repository now includes `ProductionEvaluatorConfig` and
`make_production_evaluator`. This callback is the supported production bridge:
it writes the canonical seven-column plan CSV and calls the existing batch
evaluator with the configured scenario artifacts and rules. It does not expose
solver objects or duplicate scoring logic. Unit tests verify configuration,
scenario matching, serialization, and delegation. Full solver-backed parity and
complete half-year latency remain open because the available local profile does
not include `highspy`.

A two-hour storage-enabled production smoke fixture also passed. It used one
canonical CSV submission, the prepared 2028 January--June ACTIVSg2000 block,
remote HiGHS, and the actual `evaluate_csv_batch` path. The batch evaluator
returned a feasible score. Direct prepared-DCOPF output and the batch result
matched exactly for both hourly objective values and final storage state of
charge:

```text
direct objectives: [826127.0262006355, 823687.6599435874]
batch objectives:  [826127.0262006355, 823687.6599435874]
direct final SOC:   2.0 MWh
batch final SOC:    2.0 MWh
```

This is a **small-fixture parity PASS**, not a full half-year parity claim.
The remaining **Solver-backed evaluator parity** gate is still open for the
complete released package.
The copied block was validated for the first two hours with HiGHS for this
smoke test; the release package itself remains blocked until all released
hours have passed validation.

The new `make_production_evaluator` callback was then checked against a direct
call to that same evaluator on Electrify. For the same one-site plan
(`10 MW` connection, `2 MW` private generator, `1 MW` / `4 MWh` storage), the
callback and direct paths agreed on every returned score field over diagnostic
periods `0` and `1`: feasibility, served load, IT value, energy cost,
generator operating and capital cost, storage capital cost, site cost, and
total score. Both paths used remote HiGHS and the copied 2028 January--June
artifact. The callback took `2.421 s`; the direct comparison took `0.812 s`.
This is a **callback-to-direct parity PASS for the bounded storage fixture**;
it is not a full-half-year parity or release-package validation result. The
diagnostic copy was explicitly marked as validated only for this two-hour
check and must not be treated as a released scenario artifact.

The explicit reusable coupled-LP option was also compared with the exact
Benders route on the same two-hour storage fixture. The production callback
was configured with `storage_solver="monolithic"`; its score matched a direct
monolithic evaluation, and the monolithic and Benders evaluations matched on
feasibility, served load, IT value, energy cost, generator operating and
capital cost, storage capital cost, site cost, and total score. Measured
remote runtimes were `0.637 s` for monolithic and `0.747 s` for Benders on
this two-hour case. This closes only the **bounded solver-selection parity**
check. It does not authorize changing the production default or claim that
the coupled LP is faster at the full 4,368-hour horizon.

Measured remote production-evaluator latency for the same storage-enabled
fixture was approximately **0.67 s for 2 hours** and **73.55 s for 24 hours**.
These measurements are useful scaling evidence, but they do not establish a
4,368/4,416-hour runtime or a student-facing time limit. Full-half-year
evaluator latency is therefore **OPEN**.

## Acceptance status

- [x] Exact released horizon and one-transition-per-hour counts are evidenced
      for all six year/block combinations.
- [x] Solver-free public records report environment/release identity, seed,
      evaluator call count, latency, and output size without private fields.
- [x] Default no-callback benchmark has deterministic `evaluator_call_count=0`
      and no official score claim.
- [x] Native Gymnasium availability and `check_env` are verified remotely.
- [x] Small storage-enabled production evaluator smoke test and direct/batch
      objective/SOC parity are verified remotely.
- [ ] Evaluator latency for a complete 4,368/4,416-hour run: **OPEN**; only
      2-hour and 24-hour storage-enabled measurements are available.
- [ ] Computational budget: measurements are captured, but no approved
      machine/profile threshold or budget pass is claimed here.
- [ ] Solver-backed evaluator parity: **OPEN** pending complete released
      scenario-package validation and a half-year production fixture; the
      small two-hour fixture above is only a scoped smoke result.
