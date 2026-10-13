# RL Gym Phase 7 Hourly Operation Design

**Date:** 2026-10-12
**Status:** Approved design; implementation pending written-spec review
**Scope:** Add an isolated hourly-operation RL interface around an already validated site-level plan.

## Goal

Add a deterministic, privacy-preserving hourly-operation environment for a frozen team plan while preserving the existing planning-step environment, evaluator, dispatch mathematics, projection, scoring, replay formats, and Phase 6 APIs.

## Non-negotiable boundaries

- `ConnectionPlanningEnv` remains the supported site-level planning environment.
- The existing instructor-side evaluator remains authoritative for dispatch, projection, costs, and final score.
- Phase 7 does not add a second DCOPF implementation, alter network data, change storage mathematics, add curtailment, or revise scoring coefficients.
- Hourly operation is a separate versioned contract. Planning actions are never silently interpreted as hourly actions.
- Public observations and artifacts cannot expose another team’s plan, asset IDs, dispatch, storage schedule, evaluator callback, solver object, or raw score diagnostics.

## Architecture

### Frozen-plan hourly wrapper

Create `HourlyOperationEnv` in the new focused module `rl_gym/hourly_env.py`. The constructor receives a validated `RLPlanState` or canonical CSV-derived plan, release identity, and optional application-owned terminal evaluator callback. It materializes no private cross-team state and does not mutate the supplied plan.

Each call to `step(action)` advances exactly one hour in the selected released half-year calendar. The environment exposes a normal Gymnasium-style `(observation, reward, terminated, truncated, info)` result and remains usable with the project’s fallback spaces when Gymnasium is not installed.

The hourly environment is intentionally not a replacement for the planning environment. A future competitive hourly wrapper may compose several hourly environments, but that is outside this first Phase 7 increment.

### Action space

The action is a JSON-safe structured mapping:

```text
{
  "generator_output_mw": finite scalar,
  "storage_charge_mw": finite non-negative scalar,
  "storage_discharge_mw": finite non-negative scalar
}
```

The values are bounded operating requests for the current team and current hour. They are not new connection requests and cannot edit the plan. The bounds are derived from that team’s frozen site assets and the released scenario. No curtailment field is included.

The environment rejects non-finite values, unknown fields, missing fields, negative charge/discharge values, generator output above owned capacity, and storage charge/discharge above owned power capacity. Simultaneous charging and discharging is rejected unless the frozen release explicitly permits it; the initial Phase 7 release disallows it.

### Observation space

The observation is a fixed JSON-safe mapping containing:

- release identity and scenario year/block;
- zero-based hour index and total horizon length;
- released public exogenous data required by the environment;
- the team’s own connection, generator, and storage capacity summary;
- the team’s own storage state of charge and current operating limits;
- terminal/truncation metadata at the final transition.

Observation schemas, dtypes, bounds, and missing-value behavior are fixed and tested for every supported year/block combination. Other teams’ private plans and operating decisions are never included.

### State transitions

The first implementation provides deterministic local transitions for the RL contract. Generator output is checked against owned capacity. Storage state evolves with the declared no-loss convention:

```text
storage_next_mwh = storage_current_mwh
                    + storage_charge_mw
                    - storage_discharge_mw
```

The state remains in `[0, B]`; discharging cannot exceed the current state; and the final state must satisfy the released terminal requirement, currently `storage_next_mwh >= 0.5 B` when the team owns storage. The transition layer does not claim to perform grid dispatch or official feasibility evaluation.

### Rewards and terminal evaluation

The default reward mode is a labeled zero local reward suitable for interface and policy-loop testing. It intentionally avoids silently duplicating production economics. If a future dense signal is added, its units and formula must be stored in release metadata and separately reported.

At the declared terminal boundary, an optional application-owned callback may evaluate the frozen canonical plan through the existing Phase 1 adapter. The terminal score is emitted exactly once. The result reports separate fields:

- `local_return`;
- `terminal_score`;
- `episode_return`;
- `terminal_evaluation_status`.

No terminal score is added again on later calls, and the environment cannot claim an official score when no evaluator callback or solver-backed artifact is available.

### Computational budget

The environment uses exactly the selected half-year horizon: 4,368 hours for January–June and 4,416 hours for July–December, as defined by the existing calendar. It records transition count, evaluator call count, reset/step/episode latency, release digest, environment version, and seed. It must not silently shorten the horizon or drop transitions.

Hourly operation itself is solver-free in this first increment. Solver-backed evaluation is confined to the optional terminal evaluator callback and is called at most once per episode. This makes the RL interface testable without creating an implicit 4,000-hour DCOPF loop.

### Privacy and replay

Hourly public records contain only schema-safe observations, local rewards, terminal status, aggregate metadata, and release identity. Private actions are retained only in an explicitly requested instructor/private artifact. Default public artifacts are not replayable and cannot reconstruct the team’s operating strategy.

### Evaluator parity

Parity tests compare canonical plan serialization and, when a solver-backed fixture is available, the terminal score/cost fields returned by the existing evaluator within a declared tolerance. The hourly wrapper does not reproduce evaluator equations. If required solver dependencies or artifacts are unavailable, the verification report labels parity as dependency-limited and makes no score claim.

## Error handling

- Invalid actions use the existing environment policy pattern: deterministic penalty-and-terminal behavior by default, with an optional strict raise mode for tests.
- Stepping after termination raises a clear lifecycle error.
- Missing terminal evaluator callbacks produce a documented `not_evaluated` status rather than fabricated scores.
- Evaluator exceptions are converted into a deterministic public status without exposing private diagnostics.
- Horizon mismatches, invalid plan ownership, and release-manifest mismatches fail at construction time.

## Testing and acceptance

The implementation must add focused tests for:

1. action/observation schema, JSON safety, bounds, and all supported year/block horizons;
2. generator and storage state transitions, including charge/discharge and terminal-state constraints;
3. deterministic seeded traces and exact transition counts;
4. one-time terminal evaluation and separation of local versus official return;
5. invalid actions, lifecycle errors, evaluator failures, and horizon mismatches;
6. public privacy filtering and private-artifact opt-in;
7. planning-environment regression, with the existing Phase 6 test suite remaining green;
8. parity against an available evaluator fixture, or an explicit dependency-limited report.

Acceptance requires the Phase 7 gate’s criteria to be evidenced by tests or a checked-in verification report. `git diff --check` must pass, and the change set must not modify evaluator, dispatch, network, projection, or scoring implementation files.

## Explicit non-goals

- No hourly DCOPF implementation in the RL layer.
- No replacement of the instructor evaluator.
- No new curtailment mechanism.
- No multi-team hourly competitive wrapper in this increment.
- No third-party RL algorithm, hosted training service, checkpoint format, or automatic hyperparameter search.
- No changes to the existing planning action/observation/reward contract.
