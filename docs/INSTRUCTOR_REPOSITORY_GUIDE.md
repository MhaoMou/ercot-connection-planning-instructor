# Instructor Repository Guide

This repository contains the current instructor-side implementation of the
ACTIVSg2000-based data-center connection-planning game. Its active
responsibility is to validate student CSV files, run joint DCOPF dispatch on a
validated scenario block, and produce auditable team scores.

## Production boundary

```text
Validated ACTIVSg2000 scenario block (~2,000 buses)
        -> canonical long-form student CSV
        -> catalog and horizon validation
        -> joint hourly DCOPF
        -> fair continuous projection when needed
        -> score components, ranking, and audit outputs
```

The retired browser/practice implementation is recoverable under
`archive/legacy/`; it is not packaged, launched, or part of the current game.

## Important paths

- `src/ercot_game/`: canonical CSV and batch-evaluation code.
- `tools/activsg2000/`: scenario construction, validation, DCOPF, and dispatch adapter.
- `data/processed/activsg2000_scenarios_v1/`: validated three-year scenario package.
- `data/teaching/`: January--June and July--December scoring rules.
- `scripts/check_submission.py`: student-file validation entry point.
- `scripts/create_meaningful_test_submissions.py`: complete-horizon fixtures.
- `tests/`: active production tests.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Validate and evaluate

```bash
ercot-game-check-csv \
  --file path/to/team.csv \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2017/jan_jun \
  --year 2017 --round jan_jun

ercot-game-batch run \
  --submissions-dir path/to/submissions \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2017/jan_jun \
  --rules data/teaching/dispatch_scoring_rules_jan_jun_v1.json \
  --year 2017 --round jan_jun \
  --workers 8 \
  --projection-iterations 10 \
  --output-dir work/batch_results_jan_jun
```

Use `data/processed/activsg2000_scenarios_v1/2017/jul_dec` with the
July--December rules for the second half-year. The scenario package manifest
records the six blocks and their validation state.

The CSV uses source `bus_id` values directly. Generator operating costs come
from `generator_limits.csv`; storage is specified in MW/MWh. Each team’s
generator and storage serve only that team. The score includes IT value,
energy cost, site cost, generator capital/operating cost, and storage
capital/operating cost.

## Data and privacy

Keep student CSVs, full evaluation JSON, dispatch diagnostics, and audit
bundles instructor-private. Do not publish cross-team plans. Raw downloads and
large local outputs remain outside version control according to `.gitignore`.

Run the active suite with:

```bash
PYTHONPATH=src pytest -q
```
