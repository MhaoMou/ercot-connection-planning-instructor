# RL Environment Wrapping Plan

This document describes how to wrap the current competitive data-center
connection-planning game as a Gymnasium-compatible reinforcement-learning
environment. It is an implementation roadmap, not the RL environment itself.

The central rule is to reuse the validated instructor-side evaluator rather than
create a second set of network, storage, projection, or scoring mathematics.

## 1. Current production foundation

The repository already provides the main domain components needed by an RL
wrapper:

```text
ACTIVSg2000 scenario block
        -> hourly bus/load and generator data
        -> prepared DCOPF
        -> student-plan parser
        -> joint dispatch
        -> fair continuous projection when needed
        -> hourly costs, IT value, and final score
```

The RL layer should call these components through a narrow adapter. It should
not duplicate:

- public bus validation;
- DCOPF construction or solution;
- storage state constraints;
- team-owned generator limits;
- fair continuous projection;
- score accounting;
- scenario and rule-file hashing.

The current classroom convention is:

- a team may select any public bus listed in the scenario block;
- team-built `generator_id` and `storage_id` values are self-defined labels;
- every team-built generator uses the common convex curve: 25% at $12/MWh,
  50% at $16/MWh, and 25% at $24/MWh;
- team-built generators and storage serve only their owning team;
- one episode corresponds to one released half-year;
- the time step is one hour;
- January--June and July--December are separate episode configurations;
- the existing evaluator remains authoritative for final classroom scores.

## 2. Scope and non-goals

### In scope

1. A deterministic single-agent environment for developing one team's plan.
2. A multi-agent or batched interface for studying strategic interaction among
   several teams.
3. Gymnasium-compatible `reset`, `step`, `action_space`, and `observation_space`
   behavior.
4. Scenario-versioned experiments using the same half-year artifacts supplied to
   students.
5. Evaluation reports that can be compared directly with instructor-side CSV
   evaluation.

### Explicitly out of scope for the first release

- changing the ACTIVSg2000 topology;
- introducing a new market-clearing model;
- changing the DCOPF mathematics;
- adding curtailment or integer recourse;
- allowing team assets to serve other teams;
- exposing other teams' private plans to an agent;
- treating the RL reward as a replacement for the official classroom score;
- implementing a full online student web service inside the Gym package.

## 3. Recommended architecture

Use three layers with clear boundaries.

```text
rl_gym/
  README.md                    # this roadmap
  src/ercot_game_rl/
    env.py                     # public Gymnasium environment
    spaces.py                  # action/observation schemas
    state.py                   # episode state and serialization
    adapter.py                 # bridge to existing evaluator
    rewards.py                 # reward views derived from evaluator outputs
    scenarios.py               # scenario manifests and deterministic seeds
  tests/
    test_env_api.py
    test_action_validation.py
    test_observation_privacy.py
    test_evaluator_parity.py
```

### Domain layer

The existing `src/ercot_game` and `tools/activsg2000` packages remain the
source of truth. The RL package imports them rather than copying their logic.

### Environment layer

The environment owns episode timing, action validation, observation assembly,
termination, truncation, seeding, and reward presentation. It does not own a
second copy of the evaluator.

### Experiment layer

Training scripts, baselines, checkpoints, and research outputs should live
outside the core environment package. Every experiment must record the scenario
block, rule-file hash, environment version, seed, and agent configuration.

## 4. Environment variants

### 4.1 Planning environment: recommended first implementation

The first environment should be a single-team sequential planning environment.
It is easier to test and makes the action/observation contract precise.

At each step, the agent chooses or revises one bounded planning decision. The
environment maintains a partial plan and returns a local planning reward. At the
end of the episode, the complete plan is passed through the authoritative batch
evaluator for the final score.

This variant is useful for:

- action-space debugging;
- policy learning for site and resource selection;
- training load-profile heuristics;
- comparing learned policies with hand-designed baselines.

### 4.2 Competitive multi-team environment

The second variant represents several independent teams. Each agent observes the
public scenario and its own plan, but never another team's private asset IDs,
loads, storage schedule, or resource-level decisions.

At a joint-evaluation boundary, the environment submits all valid plans to the
existing joint DCOPF evaluator. If the joint request is infeasible, the existing
fair continuous projection is applied. The environment returns each team its own
score and only the aggregate information explicitly permitted by the experiment
configuration.

This variant should be implemented only after single-team evaluator parity is
established.

## 5. Episode and time design

An episode is one complete half-year block, not six abstract periods.

| Configuration | Hours | CSV period labels |
|---|---:|---:|
| January--June 2016 | 4,368 | 1--4,368 |
| July--December 2016 | 4,416 | 1--4,416 |
| January--June 2017/2018 | 4,344 | 1--4,344 |
| July--December 2017/2018 | 4,416 | 1--4,416 |

The first implementation should support two modes:

1. **Planning-step mode:** several decisions are grouped into one environment
   step, while the underlying scenario clock advances according to an explicit
   configuration.
2. **Hourly-operation mode:** the agent acts every hour and receives dispatch
   feedback. This is more expensive and should be introduced only after the
   planning-step mode is validated.

The environment must never silently compress a half-year into six periods. Any
aggregation used for training must be named in the observation and recorded in
the episode metadata.

## 6. Action-space design

The recommended initial action is a structured, bounded action rather than a
raw 2,000-bus by 4,000-hour dense tensor.

### Planning action components

```text
site_selection       public bus index or no-op
load_capacity_mw     bounded continuous value
load_profile         bounded hourly/profile parameterization
generator_present    binary or categorical decision
generator_capacity   bounded continuous value
generator_id         deterministic label generated by the wrapper
storage_present      binary or categorical decision
storage_power_mw     bounded continuous value
storage_energy_mwh   bounded continuous value
storage_id           deterministic label generated by the wrapper
commit/revise         discrete action controlling when a site is finalized
```

The wrapper may generate IDs such as `agent0-gen-01` and `agent0-storage-01`.
Agents do not need to discover IDs in a public generator catalog. The IDs must
remain stable when a plan is serialized or compared with a CSV submission.

### Action validation

Invalid actions should be handled explicitly:

- reject non-finite values;
- clip only when the environment configuration explicitly requests clipping;
- reject unknown public bus IDs;
- reject negative capacities;
- reject storage power or energy without a storage asset;
- reject inconsistent revisions of an existing asset;
- return an `invalid_action` flag and a diagnostic code;
- do not silently convert an invalid action into a different plan.

The default research environment should terminate with a clear error only for
programmer/API misuse. Ordinary agent mistakes should produce a deterministic
penalty or no-op according to the configured invalid-action policy.

## 7. Observation-space design

Observations should distinguish public information, the agent's private state,
and derived feedback.

### Public observation

- scenario and block identifiers;
- current period or planning-step index;
- public bus features allowed by the experiment;
- public branch/interface features allowed by the experiment;
- base load and generator summaries for the current hour;
- public LMP information only if the experiment intentionally permits it;
- remaining planning budget, if a budget is part of the experiment.

### Private observation

- the agent's own selected sites;
- its own hourly load profile or profile parameters;
- its own generator/storage capacities;
- its own storage state of charge;
- its own previous dispatch and score components;
- validity and diagnostic flags for its current plan.

### Aggregate observation

Aggregate quantities must be opt-in. Examples include total submitted load,
number of valid teams, projected service fraction, or public congestion
indicators. They must not reveal another team's site-by-site plan.

The observation schema should include a version string and a mask for unavailable
or intentionally hidden fields. A fixed-shape numeric representation is preferred
for standard RL libraries, with a separate `info` dictionary for human-readable
diagnostics.

## 8. Evaluation and reward contract

The authoritative terminal reward is derived from the existing evaluator:

```text
IT value
- grid energy purchase cost
- team-generator operating cost
- team-generator capital cost
- storage capital cost
- storage operating cost
- site cost
= team score
```

For a joint infeasible plan, the evaluator applies the documented fair
continuous projection before computing the cleared score. The environment must
return projection metadata without exposing private plans of other teams.

The environment should support three reward views:

1. `terminal_score`: exact final team score; recommended for parity tests.
2. `dense_surrogate`: optional incremental signal for training, clearly marked
   as a surrogate and not a classroom score.
3. `cost_breakdown`: an `info`-only diagnostic containing value, energy cost,
   generator cost, storage cost, and site cost.

Dense rewards must not accidentally double-count terminal costs. Capital costs
should be charged exactly once, at asset commitment or episode termination, and
the choice must be documented in the environment version.

## 9. Privacy and information-leakage rules

The competitive environment must enforce the same privacy goal as the classroom
game:

- an agent sees its own plan in full;
- an agent sees public network/scenario data;
- an agent may receive permitted aggregate outcomes;
- an agent does not see another team's bus-level load, resource IDs, capacities,
  storage state, or dispatch;
- logs intended for training must be filtered through the same privacy policy;
- debugging dumps containing all plans must be marked instructor-only.

Evaluation artifacts should have separate public, per-agent-private, and
instructor-only namespaces. A seed or deterministic replay file must not be
treated as permission to expose private observations.

## 10. Determinism, seeding, and scenario identity

Every environment reset must record:

```text
environment_version
scenario_year
scenario_block
scenario_manifest_hash
rules_version
rules_hash
seed
agent/team identifiers
```

With the same scenario, rules, seed, action sequence, and evaluator version, the
environment should produce the same observations, dispatch results, and reward.
If parallel DCOPF workers are used, output ordering must remain deterministic.

Scenario files must be loaded from validated manifests. The environment should
fail before an episode begins if the scenario, economic-profile, or rule-file
hash does not match the requested configuration.

## 11. API sketch

The first public API should follow Gymnasium conventions without forcing the
student CSV workflow into a step-by-step API:

```python
env = ConnectionPlanningEnv(
    scenario_dir=".../2016/jan_jun",
    rules_file=".../rules.json",
    mode="planning",
    seed=7,
)

observation, info = env.reset(seed=7)
observation, reward, terminated, truncated, info = env.step(action)
```

The `info` dictionary should include structured diagnostics such as:

- `period_index`;
- `valid_action`;
- `plan_hash`;
- `dispatch_status`;
- `service_fraction`;
- `score_components`;
- `terminated_reason`.

Human-readable error messages belong in diagnostics and instructor reports, not
in the numeric observation itself.

## 12. Phased implementation roadmap

### Phase 1 -- Adapter and parity harness

- define a serializable internal plan state;
- convert that state to the existing `TeamPlan`;
- call the current evaluator;
- compare scores and cost components against known CSV submissions;
- verify arbitrary team-owned generator/storage IDs;
- verify the common three-segment generator curve.

**Exit condition:** an adapter-generated plan and an equivalent CSV produce the
same dispatch status and score components.

### Phase 2 -- Single-agent Gymnasium environment

- implement `reset` and `step`;
- implement structured action and observation spaces;
- add deterministic invalid-action handling;
- support one complete half-year episode;
- add seeded replay tests.

**Exit condition:** `check_env`-style API tests pass and deterministic replay is
bitwise or tolerance-equivalent for numerical outputs.

### Phase 3 -- Baselines and training harness

- implement random, greedy, and rule-based policies;
- record episode manifests and score breakdowns;
- benchmark environment construction and step latency;
- add checkpoint and replay serialization.

**Exit condition:** baseline policies run on every released half-year block and
produce auditable reports.

### Phase 4 -- Competitive multi-agent wrapper

- define per-agent private observations;
- define public aggregate observations;
- synchronize joint evaluation boundaries;
- expose fair-projection metadata without plan leakage;
- test team-order invariance and invalid-team isolation.

**Exit condition:** reordering teams does not change the joint result, and privacy
tests detect no cross-team plan leakage.

### Phase 5 -- Research release

- document environment versioning and limitations;
- publish reproducible scenario/rule references;
- add example training scripts;
- report performance and solver-time measurements;
- provide a paper-ready experiment manifest format.

**Exit condition:** an independent user can install the package, run a baseline,
replay an episode, and reproduce its score breakdown.

## 13. Required test matrix

At minimum, tests should cover:

| Area | Required checks |
|---|---|
| API | `reset`, `step`, termination, truncation, spaces, serialization |
| Parsing | arbitrary team asset IDs, public bus validation, duplicate consistency |
| Dispatch | DCOPF parity, generator segment ordering, storage constraints |
| Scoring | terminal score equals instructor evaluator, capital costs charged once |
| Projection | feasible plans unchanged; infeasible plans use common continuous fraction |
| Privacy | no private cross-team fields in observations or public logs |
| Determinism | same seed/action trace gives same result |
| Robustness | invalid actions, solver failure, missing artifact, stale hash |
| Performance | step timing at representative bus/site/resource counts |
| Documentation | example environment can be run from a clean checkout |

## 14. Important design decisions to preserve

1. The RL wrapper is a consumer of the production evaluator, not a replacement
   evaluator.
2. A half-year remains thousands of one-hour periods; any temporal abstraction
   must be explicit and reversible.
3. Team-owned generator/storage IDs are labels, not public asset catalog keys.
4. The common team-generator curve is shared across teams and sites.
5. Public network information and private team strategy are separate data
   channels.
6. Fair continuous projection is applied at joint evaluation boundaries, not
   hidden inside arbitrary per-agent reward shaping.
7. Final classroom scores come from the instructor-side batch evaluator.

## 15. Recommended next implementation step

Implement Phase 1 only: the plan-state adapter and parity harness. Do not begin
RL training or multi-agent environment work until the adapter can reproduce
known CSV results on short test horizons and at least one full half-year test
case. This keeps solver correctness, scoring correctness, and environment
correctness independently auditable.
