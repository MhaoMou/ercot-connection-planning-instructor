# ERCOT Data-Center Dispatch Game

Instructor-facing implementation of a competitive connection-planning game
based on the public synthetic Texas ACTIVSg2000 2,000-bus system. The active
workflow uses a validated three-year ACTIVSg2000 scenario package rather than the retired prototype
prototype or its abstract teaching network.

## Current production pipeline

```text
Validated ACTIVSg2000 scenario package (2,000 buses; 3 years)
        -> student long-form CSV submissions
        -> CSV/catalog validation
        -> joint DCOPF dispatch
        -> fair continuous projection if required
        -> team score and ranking
```

The Python evaluator is authoritative. Students submit CSV files; instructors
run the batch evaluator and return scores plus the intended privacy-safe
feedback artifacts. Student-owned generators and storage serve only their own
team. The score includes IT value, energy purchases, site cost, generator
capital/operating cost, and storage capital/operating cost.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/ercot_game/` | Canonical CSV reader, validator, batch evaluator, and CLI |
| `tools/activsg2000/` | Scenario construction, hourly validation, DCOPF, storage scheduling, and joint dispatch |
| `data/processed/activsg2000_scenarios_v1/` | Validated six-block ACTIVSg2000 scenario package |
| `data/teaching/` | Half-year scoring rules |
| `scripts/` | CSV checks, fixture generation, simulation, and summary utilities |
| `tests/` | Active CSV, batch, DCOPF, storage, and ACTIVSg2000 adapter tests |
| `archive/legacy/` | Recoverable retired browser/practice implementation and historical tests |
| `docs/` | Current instructor, student, calibration, and data documentation |

## Setup

Python 3.11+ and `highspy` are required:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Validate a student CSV

```bash
ercot-game-check-csv \
  --file path/to/team.csv \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2017/jan_jun \
  --year 2017 --round jan_jun
```

The active header is:

```text
team_id,submission_id,period,bus_id,load_mw,generator_id,generator_capacity_mw,storage_id,storage_power_mw,storage_energy_mwh
```

Each submitted bus must cover every period in the selected half-year. Generator
IDs and marginal costs come from `generator_limits.csv`; students submit only
the generator capacity. Storage uses MW power capacity and MWh energy capacity.

## Evaluate a half-year round

For a 2017 January--June round:

```bash
ercot-game-batch run \
  --submissions-dir path/to/submissions \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2017/jan_jun \
  --rules data/teaching/dispatch_scoring_rules_jan_jun_v1.json \
  --year 2017 --round jan_jun \
  --workers 8 \
  --projection-iterations 10 \
  --output-dir work/batch_results_jan_jun
```

Use `--year 2017 --round jul_dec` for July--December. The evaluator derives
the correct half-year length and source-row offset from the calendar. For
2016, January--June is 4,368 hours and July--December is 4,416 hours; for
2017 and 2018, January--June is 4,344 hours and July--December is 4,416
hours. The three-year package uses deterministic bus-level load and
generator-specific renewable availability recipes over fixed ACTIVSg2000
topology; see `docs/ACTIVSG2000_SCENARIO_PACKAGE.md`.

If the joint plan is infeasible, the evaluator applies the documented fair
continuous common service projection before deterministic fallback isolation.
Malformed submissions are rejected independently so valid teams can still be
scored. The output directory is the instructor-side source of truth.

See [`docs/ERCOT_INSTRUCTOR_BATCH_EVALUATION.md`](docs/ERCOT_INSTRUCTOR_BATCH_EVALUATION.md)
for the complete CSV, dispatch, projection, scoring, and privacy contract.

## Tests

```bash
PYTHONPATH=src pytest -q
```

The active test suite covers the production ACTIVSg2000 CSV/DCOPF/scoring path. The
previous browser/practice product is preserved only under `archive/legacy/` and
is not packaged, launched, or used as the current game implementation.
