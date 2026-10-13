# RL Gym Phase 6–7 Research Execution Design

## Goal

Complete the currently approved research-readiness roadmap around the stable
Phase 1–5 RL release, while isolating hourly-operation research as a separate
architecture gate.

## Phase 6 scope

Phase 6 contains four sequential workstreams:

1. a privacy-preserving multi-agent replay format;
2. a reproducible experiment runner over released half-year blocks and team
   counts;
3. baseline comparison reports;
4. a minimal training scaffold using the existing planning-step environment.

None of these workstreams changes DCOPF, scoring, projection, network data, or
the classroom CSV contract. They consume the existing environment APIs.

## Multi-agent replay

The replay format records sorted team IDs, per-team observations/actions/rewards,
termination flags, sanitized public info, joint aggregate metadata, and the
release manifest. It must reject tampered team order, missing teams, changed
manifest identity, and malformed terminal traces before replaying actions. It
must never serialize another team's private plan or evaluator internals.

## Experiment runner and reports

The runner executes deterministic policies against configurable scenario
year/block, team count, seed, and episode count. It records policy names,
release-manifest digests, timing, terminal rewards, termination status, and
sanitized aggregate counts in JSON. Reports compare policies and scenarios but
do not claim official classroom scores unless the authoritative evaluator is
explicitly supplied.

## Training scaffold

Provide a small entry point that can run a policy loop and optionally plug in a
Gymnasium-compatible learner. The default path must remain dependency-light and
solver-free. It should save configuration and manifest metadata with outputs,
not checkpoints containing private team data by default.

## Phase 7 boundary

Hourly operation is intentionally not implemented as part of Phase 6. Before
building it, the project must establish an explicit hourly action/observation
contract, reward accounting, computational budget, and parity tests against the
existing evaluator. Phase 7 may proceed only after that design gate.
