# ERCOT Data-Center Dispatch Game

Instructor-facing implementation of a competitive connection-planning game
based on the public synthetic Texas ACTIVSg2000 2,000-bus system. The active
workflow uses a validated three-year ACTIVSg2000 scenario package rather than the retired
prototype or its abstract teaching network.

## Current production pipeline

```text
Validated ACTIVSg2000 scenario package (2,000 buses; 3 years)
        -> student canonical site-level CSV submissions
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
  --file path/to/team.csv
```

The active canonical header is exactly seven columns, with one data row per
site:

```text
team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh
team-blue,site-north,1001,40,20,10,40
team-blue,site-south,1002,15,0,0,0
```

Canonical submissions contain no hourly `period`/`load_mw` rows and no
`submission_id`, `generator_id`, or `storage_id` columns. `bus_id` values come
from the selected scenario block; capacities use MW/MWh. The released
year/round and half-year horizon describe evaluator context, not CSV row
coverage. `--artifact-dir`, `--horizon`, `--year`, and `--round` remain optional
checker context when an instructor wants catalog or historical report details.

For Python callers, `CANONICAL_CSV_COLUMNS` is the named active constant.
`CSV_COLUMNS` and `BUS_CSV_COLUMNS` remain deprecated compatibility aliases to
that same tuple. The retired ten-column hourly names are available only for
explicit historical/legacy parsing and are not current submission examples.

## Evaluate a half-year round

For a 2027 January--June round:

```bash
ercot-game-batch run \
  --submissions-dir path/to/submissions \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2027/jan_jun \
  --rules data/teaching/dispatch_scoring_rules_jan_jun_v1.json \
  --year 2027 --round jan_jun \
  --workers 8 \
  --projection-iterations 10 \
  --output-dir work/batch_results_jan_jun
```

Use `--year 2027 --round jul_dec` for July--December. The evaluator derives
the correct half-year length and source-row offset from the calendar. For
2028, January--June is 4,368 hours; for 2027 and 2029, January--June is
4,344 hours; every July--December block is 4,416 hours. The three-year package uses deterministic bus-level load over fixed
ACTIVSg2000 topology. Generator status, PMIN, PMAX, and marginal costs come
from the dataset; because ACTIVSg2000 does not provide technology labels or
hourly availability data, the active release uses static generator limits.
See `docs/ACTIVSG2000_SCENARIO_PACKAGE.md`.

If the joint plan is infeasible, the evaluator applies the documented fair
continuous common service projection before deterministic fallback isolation.
Malformed submissions are rejected independently so valid teams can still be
scored. The output directory is the instructor-side source of truth.

See [`docs/ERCOT_INSTRUCTOR_BATCH_EVALUATION.md`](docs/ERCOT_INSTRUCTOR_BATCH_EVALUATION.md)
for the complete CSV, dispatch, projection, scoring, and privacy contract.

## Tests

```bash
PYTHONPATH=.:src pytest -q
```

The active test suite covers the production ACTIVSg2000 CSV/DCOPF/scoring path. The
previous browser/practice product is preserved only under `archive/legacy/` and
is not packaged, launched, or used as the current game implementation.
