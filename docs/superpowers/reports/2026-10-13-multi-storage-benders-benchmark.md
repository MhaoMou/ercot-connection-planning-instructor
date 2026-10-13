# Multi-Storage Benders Benchmark Report

This report records measured evidence for the exact instructor-side storage
solver. It intentionally distinguishes small parity validation from the
full-half-year production benchmark.

## Solver route

- Route: exact continuous multi-storage Benders decomposition.
- Reference: monolithic coupled horizon LP on small fixtures.
- Storage model: signed net power, ideal charging/discharging, team-owned
  storage, initial SOC in `[0.5B, B]`, terminal SOC at least `0.5B`.
- Approximation: none for an `optimal` result.
- Timeout behavior: `DCOPFUnresolved`; no official score is assigned.

## Current measured results

| Benchmark | Horizon | Storage assets | Status | Wall time | Peak RSS | Notes |
|---|---:|---:|---|---:|---:|---|
| Two-storage parity fixture | 4 h | 2 | PASS | measured remotely | not collected | Benders objective/final SOC matched monolithic LP |
| Adapter and batch regression | fixture-dependent | fixture-dependent | PASS | 3.43 s | not collected | 103 tests passed remotely |
| Full instructor half-year (pre final-pass optimization) | 4,368 h | 2 | UNRESOLVED | 1,398.5 s | 13,889 MB | Benders reached the optimum but the redundant detailed-result pass hit the deadline; the pass was removed afterward |
| Full instructor half-year (post final-pass optimization) | 4,368 h | 2 | NOT VERIFIED | — | — | Remote rerun pending after the final-pass fix |
| Full instructor half-year | 4,416 h | pending | NOT RUN | — | — | Run only after the isolated route is reviewed |

## Verification commands

The focused remote checks were run in the isolated validation checkout with:

```text
pytest tests/test_dcopf_multi_storage_tmp.py -k multi_storage_benders -q
pytest tests/test_plan_adapter_storage_tmp.py tests/test_batch_evaluation_tmp.py -q
```

The observed results were `3 passed` for the multi-storage tests, `25 passed`
for the adapter-storage tests, and `103 passed` for the adapter/batch subset.
The full isolated checkout suite was not a valid release regression because it
did not contain the repository's documentation and manifest fixtures; it
reported 339 passes and 18 fixture-copy failures.

## Acceptance rule

The full benchmark may be marked `PASS` only when it returns `optimal` or a
documented `infeasible` result within the configured wall-time limit, with
finite diagnostics and no hidden fallback to the monolithic horizon model.
An unresolved result is evidence about the remaining bottleneck, not a score.
