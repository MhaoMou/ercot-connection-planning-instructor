# RL Gym examples

These examples exercise the released Phase 5 RL APIs without adding a second
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

## Scope limitations

These are smoke-testable baseline examples, not training pipelines. They do
not claim official scores, persist private plans in their summaries, implement
hourly operation, or expose cross-team private state. The release manifest
identifies the scenario and artifact, while the existing RL environment owns
action validation, episode timing, and no-evaluator behavior.
