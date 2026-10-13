# RL Environment

## Implemented release

Current status: Phase 1–6 APIs are implemented, with the solver-free Phase 7
hourly surface available while its remaining gate evidence is still open.
Phase 6 as the current research-readiness phase remains the active release
context. This includes the Phase 1 adapter,
the Phase 2 single-agent sequential planning environment, the Phase 3
single-agent baselines/replay/benchmark utilities, the Phase 4 competitive
parallel wrapper, the Phase 5 research-release surface, and the Phase 6
planning/replay/experiment/reporting/training APIs. The current Phase 7 gate
controls any future hourly-operation work. Phase 5 remains the historical,
versioned release-foundation context for the current Phase 6 surface.

The [Phase 6 research-readiness contract](RELEASE.md) is the authoritative,
standalone reference for the current research surface. It preserves the Phase 5
release-foundation history, including version metadata, supported 2027--2029
`jan_jun` and `jul_dec` blocks, manifest/hash semantics, replay compatibility,
example commands,
privacy, evaluator/solver boundaries, and known limitations. This README is a
concise API entry point; the full release contract lives in `RELEASE.md`.

Phase 6 planning/replay/experiment/reporting/training APIs are implemented.
The solver-free hourly operation surface is available while its exact gate is documented in
[`PHASE7_HOURLY_GATE.md`](PHASE7_HOURLY_GATE.md).

## Phase 6 implemented APIs and commands

Phase 6 adds reproducible research plumbing around the planning environments.
These surfaces are public JSON/API contracts, not a second evaluator:

| Surface | Public API | Command/example | Boundary |
|---|---|---|---|
| Parallel replay | `run_parallel_episode`, `save_parallel_replay`, `load_parallel_replay`, `replay_parallel_episode` | `examples/rl_gym/competitive_parallel.py` | Default files are `public_sanitized`: observations/infos are filtered, plan-bearing fields and per-team actions are redacted, and the artifact is intentionally not replayable. `include_private_actions=True` is an explicit instructor/private opt-in and produces `private_replayable` output. |
| Experiment runner | `ExperimentConfig`, `run_experiments` | `PYTHONPATH=src:. python examples/rl_gym/run_experiments.py --policy greedy --years 2029 --blocks jan_jun --episodes 1 --seed 17` | Records contain public timing, reward, status, counts, and release identity only. The CLI exposes `--evaluator-mode none`; terminal evaluation is Python API-only because a callback must be injected. |
| Reporting CLI | `aggregate_records`, `render_json`, `render_markdown` | `PYTHONPATH=src:. python examples/rl_gym/compare_baselines.py --format markdown --input work/rl-experiments.json` | Accepts public experiment records, rejects private/evaluator-bearing fields, and computes summaries without scoring or solver logic. |
| Training scaffold | `TrainingConfig`, `train_policy` | `PYTHONPATH=src:. python examples/rl_gym/train_policy.py --episodes 3 --seed 17 --year 2029 --block jan_jun --output work/rl-training.json` | Dependency-light deterministic policy loop. A learner callback receives only public observation/info and is never serialized; this is not a third-party RL algorithm or checkpoint format. |

Parallel replay has two deliberately different uses. Public artifacts support
sharing aggregate outcomes and release identity without revealing strategy;
private artifacts support a trusted instructor's deterministic replay audit.
Neither mode grants an agent another team's plan, asset IDs, dispatch, storage
schedule, or resource-level decisions.

The evaluator boundary is equally explicit. `run_experiments` may receive an
application-owned terminal evaluator callback through Python, but the callback
is not stored in configuration or output. The command-line runner is solver-free
and only permits `--evaluator-mode none`. `train_policy` and reporting remain
environment/reporting scaffolds: official scores, network dispatch, projection,
and scoring continue to come from the existing production evaluator through the
Phase 1 adapter. No Phase 6 command changes evaluator, dispatch, network, or
scoring mathematics.

The Phase 7 gate must be satisfied before claiming full hourly release
readiness; planning-step traces and policy-loop training do not satisfy that
gate by themselves.

## Phase 7 hourly operation

`HourlyOperationEnv` is the solver-free hourly interface around a frozen
`RLPlanState`. It performs deterministic local transitions and does not alter
the evaluator, dispatch, projection, or scoring implementations. The released
half-year horizons are:

| Year | January--June | July--December |
|---|---:|---:|
| 2027 | 4,344 | 4,416 |
| 2028 | 4,368 | 4,416 |
| 2029 | 4,344 | 4,416 |

The default local reward is zero; it is not an official classroom score.

The public package exports the environment and its version marker:

```python
from rl_gym import HourlyOperationEnv, HOURLY_ENVIRONMENT_VERSION, RLPlanState
from ercot_game.models import SitePlan

plan = RLPlanState.for_half_year(
    team_id="team-a",
    sites=(SitePlan("site-a", "bus-1", 100.0, 50.0, 10.0, 40.0),),
    year=2028,
    round_name="jan_jun",  # 4,368 hours; jul_dec is exactly 4,416 hours
)
env = HourlyOperationEnv(plan_state=plan)
observation, info = env.reset(seed=17)
idle_action = {
    "generator_output_mw": 0.0,
    "storage_charge_mw": 0.0,
    "storage_discharge_mw": 0.0,
}
for step_index in range(10):
    observation, reward, terminated, truncated, info = env.step(idle_action)
    assert not terminated and not truncated
    assert info["transition_count"] == step_index + 1
assert HOURLY_ENVIRONMENT_VERSION == "phase7-hourly-v1"
```

The action has exactly three fields: `generator_output_mw`,
`storage_charge_mw`, and `storage_discharge_mw`. Values are finite bounded
requests for the current hour. There is no `storage_energy_mwh` or curtailment
action; the hourly contract has no curtailment action, and storage energy
capacity comes from the frozen plan. An optional
application-owned `evaluator_callback(plan_state, release_manifest)` may be
provided as a one-time evaluator callback at the terminal boundary; it is
called at most once per episode. Without it, the
terminal status is `not_evaluated` and the local reward remains zero.

`env.episode_record()` returns a bounded JSON-safe `public_sanitized` record
with actions and private evaluator details removed. Explicitly passing
`include_private_actions=True` returns a `private_record` containing the
normalized action trace for trusted local inspection. The hourly
`private_record` is not replayable by an hourly loader; it is intentionally
distinct from the Phase 6 `private_replayable` parallel-replay artifact.
The public/private boundary excludes plan-bearing fields, asset IDs,
storage schedules, evaluator objects/output, solver objects, raw diagnostics,
and team scores from public records.

### Experiment evaluator boundary

`ExperimentConfig.evaluator_mode` supports `"none"` and `"terminal"`.
`run_experiments(..., evaluator_callback=...)` keeps the callback at the
application boundary: terminal mode requires that callback, while none mode
rejects one. The experiment CLI is intentionally solver-free and exposes only
`--evaluator-mode none`; terminal evaluation is Python API-only because the CLI
has no callback-injection mechanism.

Construction validates `scenario_year` as a non-bool integer through
`half_year_calendar`; supported years are 2027--2029. Empty-plan finish is a
deterministic invalid terminal outcome and never invokes the evaluator. A
non-empty plan reaching `max_planning_steps` follows the same terminal adapter
evaluation path as an explicit finish action.

`ConnectionPlanningEnv` is the released single-agent planning environment. Its
declared `action_space` is a standard structured `Dict` with discrete
`bus_index`, `finish`, and `revise` fields and bounded scalar capacity fields.
Each non-finish action adds one public bus or revises the existing site at that
bus. Storage energy must equal four times storage power, and generator capacity
cannot exceed requested connection capacity. `action_space.sample()` produces a
structurally valid non-finish action for standard tooling. The legacy seven-value
vector
`[bus_index, requested_capacity_mw, generator_capacity_mw, storage_power_mw,
storage_energy_mwh, finish, revise]` remains accepted by `step` for compatibility.

Observations are fixed-shape dictionaries containing numeric public metadata,
an indexed public bus catalog, and padded summaries of the agent's own plan.
They contain no other-team or dispatch state. Invalid actions are deterministic
penalized no-ops by default; `invalid_action_policy="raise"` is available for
strict callers. A finish action terminates the episode. The planning-step
limit terminates, while an optional `max_episode_steps` limit truncates.

The default terminal result is score `0` with
`dispatch_status="not_evaluated"`. When `evaluator_callback` is supplied,
the environment serializes the plan and delegates through the Phase 1
`evaluate_plan_state` adapter; it does not duplicate evaluator or scoring
mathematics. `reward_mode="terminal_score"` charges the terminal score once,
while `reward_mode="dense_surrogate"` emits a documented local planning signal
and does not add the terminal score again.

Gymnasium is an optional dependency: install `ercot-game[rl]` for the standard
spaces and environment base class. A small documented compatibility fallback
keeps the API and tests usable when Gymnasium is absent.

## Phase 3 public APIs

`RandomPolicy(seed=...)`, `GreedyPolicy()`, and `RuleBasedPolicy()` are
callables with the common `policy(observation, info)` interface. They emit the
structured action mapping accepted by `ConnectionPlanningEnv`; all generated
actions satisfy the configured capacity, generator, and storage-duration
constraints. Random policy choices are seeded and avoid duplicate add-site
actions while another public bus is available. Greedy and rule-based policies
use deterministic catalog order.

Episodes can be recorded and verified without serializing private evaluator or
multi-agent state. The manifest records environment/scenario identity,
`evaluator_version`, optional rule/hash metadata, and the seed; replay validates
that identity against the target environment before consuming the action trace.

```python
from rl_gym import GreedyPolicy, load_replay, replay_episode, run_episode, save_replay

trace = run_episode(env, GreedyPolicy(), seed=7)
save_replay("episode.json", trace)
replay_episode(env_factory(), load_replay("episode.json"))
```

`benchmark_environment(env_factory, policy, episodes=..., seed=...)` returns a
JSON-serializable report with reset, step, episode, optional evaluator-step
latencies, and action count. It measures the environment only; it does not
benchmark solvers.

## Phase 1 adapter contract

Phase 1 provides only the plan-state boundary needed before an environment is
introduced. `rl_gym.RLPlanState` stores a team ID, canonical
`ercot_game.models.SitePlan` entries, and exact released half-year metadata
from `ercot_game.calendars`. `RLPlanState.to_team_plan()` materializes the
existing evaluator-facing `TeamPlan`: one constant connection profile per
hour, plus deterministic team-owned IDs of the form
`generator:{team_id}:{site_id}` and `storage:{team_id}:{site_id}`.

`RLPlanState.write_csv(path)` writes a canonical seven-column CSV at a
caller-controlled persistent path. `evaluate_plan_state(submission_path=...)`
then delegates to `ercot_game.batch_evaluation.evaluate_csv_batch`. This is
the real evaluator path: artifact loading, dispatch, projection, and score/cost
calculation remain in the production evaluator, and returned
`SubmissionRecord.path` values remain usable after evaluation. The helper's
optional evaluator argument exists only as a test seam for lightweight
monkeypatched fixtures.

Solver-backed parity still requires the repository's optional `highspy`
dependency and prepared scenario artifacts. When `highspy` is unavailable,
Phase 1 can verify CSV/plan structure and evaluator delegation with a
monkeypatched evaluator, but it cannot claim a real DCOPF score comparison.

The adapter delegates expansion to `tools.activsg2000.plan_adapter`, parsing to
`ercot_game.csv_submission`, dispatch to the prepared DCOPF path, and scoring
to `ercot_game.batch_evaluation`. It does not duplicate any dispatch or score
math. The compatibility scalar stored on generated `GeneratorPlan` records is
`TEAM_GENERATOR_COMPATIBILITY_COST_PER_MWH`; prepared dispatch and scoring
continue to expand each generator with the production
`TEAM_GENERATOR_COST_CURVE` three-segment curve. `compare_csv_parity()`
compares the actual adapter-produced plan with a CSV-derived plan and can also
compare evaluator-provided score/cost fields when both `TeamScore` records are
passed.

The Phase 1 adapter itself intentionally does not implement Gymnasium `reset`,
`step`, action spaces, observation spaces, rewards, or environment timing; those
responsibilities now belong to the Phase 2 environment described above.

This document summarizes the implemented Phase 1--6 wrapper around the current
competitive data-center connection-planning game. Phase 6 is the current
research-readiness surface; see `RELEASE.md` for its contract and explicit
future-work boundary. Phase 5 is retained below as historical release-
foundation context.

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
- the current student CSV has exactly seven columns and no generator-ID or
  storage-ID fields; the evaluator assigns deterministic private asset IDs
  internally from each team/site pair;
- every team-built generator uses the common convex curve: 25% at $60/MWh,
  50% at $100/MWh, and 25% at $160/MWh;
- team-built generators and storage serve only their owning team;
- one episode corresponds to one released half-year;
- the time step is one hour;
- January--June and July--December are separate episode configurations;
- the existing evaluator remains authoritative for final classroom scores.

The active scoring coefficients are IT value of $1500/MWh; site cost of
$20m/MW; generator capital cost of $1.3m/MW; storage capital costs of
$350k/MW plus $200k/MWh (a four-hour total of $1.15m/MW); and no storage
operating cost.

## 2. Scope and non-goals

### In scope

1. A deterministic single-agent environment for developing one team's plan.
2. Gymnasium-compatible `reset`, `step`, `action_space`, and `observation_space`
   behavior.
3. Scenario-versioned experiments using the same half-year artifacts supplied to
   students.
4. Evaluation reports that can be compared directly with instructor-side CSV
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
  README.md                    # environment contract and roadmap
  __init__.py                  # public exports
  adapter.py                   # Phase 1 plan/evaluator bridge
  env.py                       # Phase 2 single-agent environment
tests/
  test_rl_gym_adapter.py
  test_rl_gym_env.py
```

### Domain layer

The existing `src/ercot_game` and `tools/activsg2000` packages remain the
source of truth. The RL package imports them rather than copying their logic.

### Environment layer

The environment owns episode timing, action validation, observation assembly,
termination, truncation, seeding, and reward presentation. It does not own a
second copy of the evaluator.

### Experiment layer

Training scripts, checkpoints, and research outputs should live
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
- deterministic planning-policy experiments;
- comparing learned policies with hand-designed baselines.

### 4.2 Competitive multi-team environment

`ParallelConnectionPlanningEnv` represents several independent teams. Each
agent observes the public scenario and its own plan, but never another team's
private asset IDs, loads, storage schedule, or resource-level decisions.

At a joint-evaluation boundary, the environment submits all valid plans to the
existing joint DCOPF evaluator. If the joint request is infeasible, the existing
fair continuous projection is applied. The environment returns each team its own
score and only the aggregate information explicitly permitted by the experiment
configuration.

This variant is implemented with single-team evaluator parity established.

## 5. Episode and time design

An episode is one complete half-year block, not six abstract periods.

| Configuration | Hours | CSV period labels |
|---|---:|---:|
| January--June 2027/2029 | 4,344 | 1--4,344 |
| January--June 2028 | 4,368 | 1--4,368 |
| July--December 2027/2028/2029 | 4,416 | 1--4,416 |

Phase 2 supports planning-step mode only. Each step adds or revises one site;
the underlying hourly scenario is evaluated only at terminal completion.
Hourly-operation mode, in which an agent acts every hour, is deferred to a
later phase.

The environment must never silently compress a half-year into six periods. Any
aggregation used for training must be named in the observation and recorded in
the episode metadata.

## 6. Action-space design

The action-schema examples in this section and the later API sketch are legacy
RL/internal design examples, not the current student CSV contract. An RL
wrapper may generate in-memory IDs for its own serialized plan state, but those
IDs must not be added to the seven-column student CSV.

The recommended initial action is a structured, bounded action rather than a
raw 2,000-bus by 4,000-hour dense tensor.

### Phase 2 action fields and legacy vector order

```text
[bus_index, requested_capacity_mw, generator_capacity_mw,
 storage_power_mw, storage_energy_mwh, finish, revise]
```

The public `Dict` action space uses these same names as mapping keys. The
seven-value ordering above is retained only for callers using the legacy vector
input accepted by `step`.

There are no hourly load-profile tensors, generator-ID fields, storage-ID
fields, or separate asset-discovery actions. The wrapper generates stable
internal site IDs, and the Phase 1 adapter generates evaluator-side asset IDs.

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

Scenario-year and half-year-block validity are checked before an episode
begins. Manifest, rules, and evaluator hashes are optional metadata in the
current Phase 2 environment; future scenario-management work may add strict
artifact hash validation.

`ConnectionPlanningEnv` accepts optional `scenario_manifest_hash`,
`rules_version`, `rules_hash`, and `evaluator_version` constructor fields and
copies the supplied values into `info` on reset and step. Omitted values remain
`None`; the environment never invents replay hashes. Evaluator submissions use
unique persistent filenames under `submission_dir`, so concurrent environment
instances do not overwrite one another and evaluator-returned path provenance is
preserved.

## 11. API sketch

The first public API should follow Gymnasium conventions without forcing the
student CSV workflow into a step-by-step API:

```python
from rl_gym import ConnectionPlanningEnv

env = ConnectionPlanningEnv(
    team_id="team-a",
    bus_catalog=("bus-a", "bus-b"),
    scenario_year=2029,
    scenario_block="jan_jun",
    max_sites=4,
    max_planning_steps=4,
)

observation, info = env.reset(seed=7)
observation, reward, terminated, truncated, info = env.step(action)
```

The current `info` dictionary includes:

- `environment_version`;
- `scenario_year` and `scenario_block`;
- optional replay identity metadata;
- `step` and the current serialized `plan`;
- `valid_action`;
- `dispatch_status`;
- `score` when terminal evaluation is performed;
- `invalid_action_code` and `diagnostic` for rejected actions;
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

### Phase 2 -- Single-agent Gymnasium environment (implemented)

- implement `reset` and `step`;
- implement structured action and observation spaces;
- add deterministic invalid-action handling;
- support one complete half-year episode;
- add seeded replay tests.

**Exit condition:** `check_env`-style API tests pass and deterministic replay is
bitwise or tolerance-equivalent for numerical outputs.

### Phase 3 -- Baselines, replay, and performance reporting (implemented)

- implement random, greedy, and rule-based policies;
- record JSON-serializable episode manifests, observations, actions, rewards,
  termination flags, and terminal score metadata;
- replay traces with numeric tolerance and tamper detection;
- benchmark environment reset, step, episode, and optional evaluation latency.

**Exit condition:** baseline policies run on every released half-year block and
produce auditable reports.

### Phase 4 -- Competitive multi-agent wrapper (implemented)

- `ParallelConnectionPlanningEnv` provides a dependency-light parallel API with
  deterministic sorted team IDs and private per-team child environments;
- missing and invalid actions are isolated to their team; unknown action keys
  are ignored but counted in aggregate metadata;
- valid nonempty plans are written to unique persistent submission paths and
  evaluated once as a joint set at the terminal boundary;
- aggregate metadata exposes only counts and booleans, including sanitized
  projection status/counts rather than team-keyed diagnostics;
- tests cover API behavior, privacy, deterministic reset/step, evaluator call
  count, valid/empty teams, order invariance, and invalid-team isolation.

**Exit condition:** reordering teams does not change the joint result, and privacy
tests detect no cross-team plan leakage.

`run_parallel_episode`, `save_parallel_replay`, `load_parallel_replay`, and
`replay_parallel_episode` support deterministic parallel episode traces. The
default `save_parallel_replay(path, trace)` output is a public, sanitized
artifact: it retains manifest identity, team IDs, sanitized observations and
infos, rewards, terminal flags, aggregate metadata, and a privacy marker, but
redacts per-team actions and plan-bearing fields. Public artifacts cannot
reconstruct or replay private team strategies. Instructor/private experiments
may opt in explicitly with `include_private_actions=True`; those artifacts are
marked `private_replayable` and can be loaded and replayed.

### Phase 5 -- Release foundation (historical context)

- version the environment, plan schema, scenario blocks, and release metadata;
- provide deterministic canonical JSON manifests and SHA-256 identity hashes;
- document replay compatibility, privacy, evaluator/solver boundaries, and
  known limitations;
- provide runnable baseline/replay examples and focused release checks.

**Historical release boundary:** an independent user could install the package,
run a baseline, replay a compatible single-agent episode, and inspect the
release identity. Phase 6 now carries the active research-readiness surface;
hourly operation remains future work under the Phase 7 gate.

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

The next extensions are hourly operation and full training scripts/checkpoints.
Parallel replay is implemented with separate public-sanitized and explicitly
private-replayable artifact modes documented in `RELEASE.md`.
