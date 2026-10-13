# Phase 7 Hourly-Operation Gate

## Purpose and status

Phase 6 provides planning environments, deterministic replay, experiment
records, reporting, and a dependency-light policy loop. It does not expose
hourly operation. Phase 7 may begin only after this gate is accepted. The gate
protects the existing public/private boundary and requires hourly behavior to
remain comparable with the instructor-side evaluator.

This document is a readiness contract, not an implementation of hourly
operation. It does not authorize changes to evaluator, dispatch, network, or
scoring code.

## 1. Action contract

At each operating hour `t`, an agent receives one structured action for its own
team. The action must be JSON-safe and bounded by the environment's declared
space. The minimum contract is:

```text
{
  "generator_output_mw": finite scalar,
  "storage_charge_mw": finite non-negative scalar,
  "storage_discharge_mw": finite non-negative scalar,
  "storage_energy_mwh": finite scalar,
  "curtailment_mw": finite non-negative scalar
}
```

The environment must reject or deterministically penalize out-of-range values,
simultaneous charge/discharge when disallowed, and state transitions that
violate the declared storage limits. The contract must state whether a value is
an instruction or a bounded request; it must not silently reinterpret a
planning action as an hourly action. The action applies only to the current
hour and cannot directly edit another team's plan or asset state.

## 2. Observation contract

The observation at hour `t` must include, at minimum:

- public scenario identity and hour index;
- public bus/load and other released exogenous information;
- the agent's own preceding asset/state summary needed for a Markov decision;
- the agent's own storage state and available operating limits;
- termination/truncation metadata when the hour is the final transition.

It must not include another team's private plan, asset IDs, dispatch decisions,
storage schedule, resource-level decisions, evaluator callback, solver object,
or raw score internals. Aggregate values are allowed only when explicitly
declared in the experiment configuration and must not be team-keyed diagnostics.
Observation keys, dtypes, bounds, and missing-value behavior must be fixed and
tested for every supported half-year block.

## 3. Reward accounting

Reward accounting must distinguish local transition feedback from the official
terminal result:

1. Per-hour reward is either a documented local signal or zero. If a dense
   surrogate is used, its terms and units are fixed in the release metadata.
2. The authoritative evaluator may contribute a terminal score only at the
   declared evaluation boundary, and that score is charged exactly once.
3. The episode return must state whether it is a local-learning return, an
   evaluator-backed score, or both as separate fields. It must never add the
   terminal score twice or present a surrogate as the classroom score.
4. Invalid actions, truncation, evaluator errors, and infeasible joint plans
   must have deterministic status/reward semantics and must remain visible in
   public reports without leaking private evaluator details.

The production evaluator remains the authority for final score, cost,
projection, and dispatch accounting. Hourly reward code may shape learning
feedback, but it may not duplicate or replace those calculations.

## 4. Computational budget

Every hourly environment and experiment configuration must declare and enforce
the following budget:

| Budget item | Gate requirement |
|---|---|
| Episode horizon | Exactly the released half-year calendar for the selected year/block; no implicit shortening. |
| Environment transitions | At most one transition per released operating hour, plus a documented terminal transition if needed. |
| Evaluator calls | Zero for no-evaluator mode; otherwise at most one authoritative evaluation per declared evaluation boundary. |
| Wall-clock budget | A reproducible benchmark records reset, step, episode, and evaluator latency on a documented machine/profile. |
| Memory/output | Public traces and reports remain JSON-safe and bounded; private artifacts require explicit opt-in and are never emitted by default. |
| Reproducibility | Fixed seed derivation, release manifest digest, environment version, and action/observation schema are recorded. |

The acceptance report must include measured counts and timings. A policy that
exceeds a declared limit fails the gate rather than silently truncating or
dropping transitions.

## 5. Evaluator parity

Hourly operation must use the existing Phase 1 adapter and production
evaluator path for any authoritative comparison. Parity evidence must cover:

- canonical plan/state serialization and the released scenario identity;
- generator and storage ownership boundaries;
- storage constraints and terminal state handling;
- dispatch, projection, cost, and score fields returned by the production path;
- deterministic agreement within a declared numeric tolerance for a fixed
  seed/action trace.

The RL layer must not add network equations, DCOPF construction, storage
physics that disagree with the evaluator, projection mathematics, or scoring
coefficients. If the optional solver dependency or prepared artifacts are
unavailable, the report must label parity as dependency-limited rather than
claiming a score match.

## 6. Acceptance criteria

Phase 7 hourly operation is accepted only when all criteria below are evidenced
by tests or a checked-in verification report:

- [ ] Action and observation spaces are fixed, JSON-safe, bounded, and tested
      on all released year/block combinations.
- [ ] Public observations and public replay/report artifacts contain no
      cross-team private strategy or evaluator-bearing fields.
- [ ] Same release manifest, seed, and action trace reproduce the same hourly
      observations, rewards, statuses, and terminal result within tolerance.
- [ ] Reward accounting demonstrates one-time terminal score handling and
      separately labels any dense/local signal.
- [ ] Computational budgets report exact horizon, transition count, evaluator
      call count, latency, and output size; no budget is exceeded.
- [ ] Production evaluator parity passes for an available solver-backed fixture,
      or the result is explicitly marked dependency-limited with no score claim.
- [ ] CLI/API documentation and documentation tests describe the contract,
      privacy boundary, evaluator boundary, and non-goals.
- [ ] `git diff --check` is clean and the change set contains no evaluator,
      dispatch, network, or scoring modifications.

## 7. Non-goals

Passing this gate does not mean that the project will:

- change the ACTIVSg2000 topology, network model, DCOPF, projection, or scoring
  coefficients;
- expose other teams' plans, asset IDs, dispatch, storage schedules, or raw
  evaluator diagnostics to an agent;
- replace the instructor-side evaluator with an RL-native score;
- add a hosted training service, third-party RL algorithm, checkpoint format, or
  automatic hyperparameter search;
- make public replay artifacts replayable by embedding private actions;
- convert planning actions into hourly actions without a separately versioned
  contract and parity evidence;
- claim solver-backed parity when required dependencies or artifacts are absent.

Until every acceptance criterion is satisfied, hourly operation remains future
work and Phase 6 planning/replay/training behavior remains the supported scope.
