# RL Gym examples

These examples exercise the released Phase 6 RL APIs without adding a second
evaluator or a second scoring implementation. They use the public bus catalog
from `data/processed/activsg2000_scenarios_v1/<year>/<block>/` and construct a
validated `ReleaseManifest` for that release.

## Installation

From the repository root, use Python 3.11 or newer:

```bash
python -m pip install -e '.[rl]'
```

The examples can also be run from a checkout with the repository root on
`PYTHONPATH`.

## Commands

Supported blocks are `jan_jun` and `jul_dec`. The checked-in scenario package
currently provides years 2027, 2028, and 2029.

```bash
PYTHONPATH=src:. python examples/rl_gym/single_agent_baseline.py \
  --year 2029 --block jan_jun --seed 17 \
  --replay-out work/rl-replays/single-agent.json

PYTHONPATH=src:. python examples/rl_gym/competitive_parallel.py \
  --year 2028 --block jul_dec --seed 23
```

Each command prints exactly one compact JSON summary. The single-agent command
uses `GreedyPolicy` and can save the existing replay format. The parallel
command runs deterministic private `team-a` and `team-b` policies and reports
only per-team reward/status values and aggregate counts; it never prints plans
or raw evaluator results.

## Deterministic policy-loop training

Phase 6 adds a dependency-light training loop around
`ConnectionPlanningEnv`. It is a deterministic episode runner, not a
third-party RL algorithm or checkpoint format. `GreedyPolicy` is used by
default, and `--episodes`, `--seed`, `--year`, `--block`, and `--output` are
configurable:

```bash
PYTHONPATH=src:. python examples/rl_gym/train_policy.py \
  --episodes 3 --seed 17 --year 2029 --block jan_jun \
  --output work/rl-training.json
```

The command prints one compact JSON result and optionally writes the same
result to a file. The result contains validated configuration, episode
reward/step summaries, aggregate totals, and the release manifest. It does
not serialize private environment state, a learner callback, or multi-team
state.

Python experiments may provide an in-memory learner callback in place of the
built-in policy. The callback receives only the current public observation and
info mapping and returns one public environment action:

```python
from rl_gym import train_policy

result = train_policy(environment_factory, episodes=4, seed=17,
                      year=2029, block="jan_jun",
                      learner_callback=learner_callback)
```

The callback is intentionally not serialized into `result`; use the returned
JSON metadata for reproducible experiment records.

## Evaluator injection

No evaluator is configured by default. Therefore terminal rewards are the
environment's no-evaluation result and the status is `not_evaluated`. For an
experiment that needs authoritative scoring, construct the environment in
Python and inject the existing evaluator callback through
`ConnectionPlanningEnv(evaluator_callback=...)`, or inject the existing joint
evaluator through `ParallelConnectionPlanningEnv(joint_evaluator=...)` with
the required artifact/rules arguments. Keep evaluator ownership in the
production `ercot_game` APIs; do not duplicate scoring or dispatch math in an
example policy.

The `examples/rl_gym/run_experiments.py` CLI intentionally exposes only
`--evaluator-mode none`. Terminal evaluation requires an application-level
callback, so `evaluator_mode="terminal"` is Python API-only:

```python
from rl_gym import ExperimentConfig, run_experiments

config = ExperimentConfig(
    policy_name="greedy",
    scenario_years=(2029,),
    blocks=("jan_jun",),
    team_count=1,
    episode_count=1,
    seed=17,
    evaluator_mode="terminal",
)
records = run_experiments(config, evaluator_callback=your_evaluator_callback)
```

The API requires that callback for terminal mode and rejects a callback when
the mode is `none`. Passing `--evaluator-mode terminal` to the CLI is rejected
by its argument parser rather than attempting an evaluation without an
injection mechanism.

## Scope limitations

These examples do not claim official scores, persist private plans in their
summaries, implement hourly operation, or expose cross-team private state.
The policy-loop trainer is intentionally dependency-light and does not add a
third-party RL algorithm, checkpoint format, or hosted training service. The
release manifest identifies the scenario and artifact, while the existing RL
environment owns action validation, episode timing, and no-evaluator behavior.
