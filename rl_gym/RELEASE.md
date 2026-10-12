# RL Gym Phase 5 research release

This document is the authoritative contract for the implemented RL research-
release surface. It describes what can be reproduced with the checked-in
`rl_gym` package; it does not promise a hosted training service or a new
evaluator.

## Release identity

The release exposes three version values from `rl_gym.release`:

```python
from rl_gym import ENVIRONMENT_VERSION, PLAN_SCHEMA_VERSION, RELEASE_VERSION

assert RELEASE_VERSION == "phase5-release-v1"
assert ENVIRONMENT_VERSION == "phase5-environment-v1"
assert PLAN_SCHEMA_VERSION == "phase5-plan-schema-v1"
```

Use these values in experiment reports and replay artifacts. A release
manifest is an immutable, JSON-safe identity record containing:

- `release_version`, `environment_version`, and `plan_schema_version`;
- `scenario_year` and `scenario_block`;
- JSON metadata for `rules` and experiment `config`.

`make_release_manifest(...)` validates the identity and scenario fields.
`ReleaseManifest.to_dict()` returns ordinary detached JSON data, while the
manifest itself freezes nested metadata so later caller mutations cannot alter
the release identity.

## Supported scenarios and blocks

The release supports the prepared half-year blocks for years **2027, 2028,
and 2029**:

```text
scenario_block="jan_jun"
scenario_block="jul_dec"
```

Each episode represents one complete half-year scenario. The block-specific
calendar determines the number of hourly records: January–June has 4,344
hours in 2027/2029 and 4,368 hours in 2028; July–December has 4,416 hours.
The planning environment does not expose one action per hour: it builds a
plan, then evaluates that plan through the normal terminal evaluator path.

The supported environment blocks are:

- `ConnectionPlanningEnv`: deterministic single-agent sequential planning;
- `ParallelConnectionPlanningEnv`: competitive parallel planning with one
  public scenario view and one private plan per team.

The release also includes the Phase 3 policies, JSON episode recording, and
benchmark helpers. The parallel wrapper intentionally does not claim a
parallel replay format.

## Manifest and hash semantics

`canonical_json(value)` serializes JSON-compatible values as compact UTF-8 JSON
with sorted mapping keys, no insignificant whitespace, and JSON-compatible
finite numbers only. `stable_hash(value)` is the lowercase SHA-256 digest of
that canonical JSON byte sequence.

Therefore, mapping insertion order does not affect a digest, but a semantic
change to rules, configuration, scenario identity, or version metadata does.
The release manifest digest is:

```python
from rl_gym import make_release_manifest

manifest = make_release_manifest(
    scenario_year=2029,
    scenario_block="jan_jun",
    rules={"version": "rules-v1"},
    config={"seed": 7},
)
digest = manifest.digest()
```

Hashes are identity metadata, not a substitute for validating or loading the
underlying scenario artifacts. Optional scenario, rules, and evaluator hashes
are preserved when supplied by the environment or experiment.

## Replay compatibility

Single-agent traces can be recorded, saved, loaded, and replayed. The
checked-in examples exercise the released APIs with runnable commands:

```bash
PYTHONPATH=src:. python examples/rl_gym/single_agent_baseline.py \
  --year 2029 --block jan_jun --seed 17 \
  --replay-out work/rl-replays/single-agent.json

PYTHONPATH=src:. python examples/rl_gym/competitive_parallel.py \
  --year 2028 --block jul_dec --seed 23
```

Replay checks the release manifest digest when present, plus the environment
identity fields, scenario/rules/evaluator metadata, initial observation and
info, every action/result, termination state, and terminal score within the
configured numeric tolerance. A changed release manifest, stale identity
metadata, tampered action, or changed environment fails with a replay
mismatch rather than silently producing a new result.

Replay files contain public episode data and release metadata only. They are
not a permission mechanism for recovering private evaluator state. The
current helpers replay single-agent episodes; replaying a parallel mapping
requires an experiment-specific wrapper.

## Example commands

Install the optional Gymnasium dependency when standard Gym spaces are
desired:

```bash
pip install -e '.[rl]'
```

Run the deterministic planning examples and save the single-agent public trace:

```bash
PYTHONPATH=src:. python examples/rl_gym/single_agent_baseline.py \
  --year 2029 --block jan_jun --seed 17 \
  --replay-out work/rl-replays/single-agent.json

PYTHONPATH=src:. python examples/rl_gym/competitive_parallel.py \
  --year 2028 --block jul_dec --seed 23
```

Run the release/documentation checks:

```bash
python -m pytest tests/test_rl_gym_release.py -q
```

Use `benchmark_environment(env_factory, policy, episodes=..., seed=...)` for
JSON-serializable environment timing and action-count reports. It measures
environment and optional evaluator-step latencies; it does not benchmark a
solver.

## Privacy boundary

Observations expose public scenario metadata, the indexed public bus catalog,
and summaries of the current agent's own plan. They do not expose another
team's private plan, asset IDs, dispatch decisions, storage schedule, or
resource-level decisions. Public logs and replay artifacts must follow the
same boundary. Seeds, hashes, and deterministic traces do not make
instructor-only namespaces public.

The parallel environment applies the same rule: each team receives its own
private plan view and only the aggregate information explicitly allowed by
the experiment configuration. The RL package does not create a second
privacy policy for training or logging.

## Evaluator and solver boundary

The existing evaluator remains authoritative for final classroom scores. The
RL adapter serializes the plan and delegates to the production CSV, dispatch,
projection, and scoring path. The RL layer does not duplicate network,
storage, projection, dispatch, or score mathematics, and an RL reward is not a
replacement for the official score.

`highspy` and prepared scenario artifacts are optional requirements for
solver-backed parity. Without them, tests can still validate plan/CSV shape
and evaluator delegation with a monkeypatched evaluator, but they cannot
claim a real DCOPF score comparison. The benchmark helper likewise reports
environment/evaluator timing and does not measure solver performance.

## Known limitations and future work

- The release is planning-step based; **hourly operation** is future work.
- It provides reusable policies and replay/benchmark utilities, but **full
  training** scripts, checkpoints, and a hosted training service are future
  work; full training is not part of this release.
- Parallel episodes do not yet have a built-in multi-agent replay format.
- Solver-backed parity depends on optional `highspy` and prepared artifacts.
- Hashes identify supplied metadata; they do not package or verify missing
  external artifacts by themselves.
- The compatibility fallback for missing Gymnasium is intentionally small and
  is not a replacement for the full Gymnasium distribution.

These limitations do not defer the implemented Phase 5 release surface: the
versioned manifest, supported scenario blocks, replay identity checks, privacy
boundary, and evaluator/solver boundary described above are available now.
