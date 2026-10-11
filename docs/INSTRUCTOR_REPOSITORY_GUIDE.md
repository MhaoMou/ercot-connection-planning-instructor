# Instructor Repository Guide

This repository contains the current instructor-side implementation of the
ACTIVSg2000-based data-center connection-planning game. Its active
responsibility is to validate student CSV files, run joint DCOPF dispatch on a
release-approved scenario block, and produce auditable team scores.

Full six-block/hour DCOPF validation is currently pending and unverified because
the optional `highspy` dependency is unavailable in this environment. The
checked-in manifests are fail-closed and must not be described as passed or
validated until a HiGHS-backed validation run succeeds.

The scenario package is canonical and source-controlled, but release validation
is pending. It is not validated for release until the complete HiGHS-backed
validation finishes successfully.

## Production boundary

```text
Canonical ACTIVSg2000 scenario block; release validation pending (~2,000 buses)
        -> canonical seven-column site-level student CSV
        -> catalog validation (optional checker context)
        -> joint hourly DCOPF
        -> fair continuous projection when needed
        -> score components, ranking, and audit outputs
```

The retired browser/practice implementation is recoverable under
`archive/legacy/`; it is not packaged, launched, or part of the current game.

## Important paths

- `src/ercot_game/`: canonical CSV and batch-evaluation code.
- `tools/activsg2000/`: scenario construction, validation, DCOPF, and dispatch adapter.
- `data/processed/activsg2000_scenarios_v1/`: canonical, source-controlled
  three-year scenario package; release validation pending.
- `data/teaching/`: January--June and July--December scoring rules.
- `scripts/check_submission.py`: student-file validation entry point.
- `scripts/create_meaningful_test_submissions.py`: **legacy/historical complete-horizon
  compatibility fixtures only**; it emits the retired ten-column hourly schema and
  is not an example for current submissions. Use the canonical one-row-per-site
  seven-column examples above for active tests and documentation.
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
  --file path/to/team.csv

ercot-game-batch run \
  --submissions-dir path/to/submissions \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2027/jan_jun \
  --rules data/teaching/dispatch_scoring_rules_jan_jun_v1.json \
  --year 2027 --round jan_jun \
  --submission-number 2 \
  --workers 8 \
  --projection-iterations 10 \
  --output-dir work/batch_results_jan_jun
```

The current CSV header is:

```text
team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh
```

Each data row is one site. There are no `period`, hourly `load_mw`,
`submission_id`, `generator_id`, or `storage_id` columns, and no horizon rows to
generate. The seven-column CSV has no asset-ID columns; the evaluator assigns
deterministic private generator and storage IDs internally from each team/site
pair, and those IDs are not student inputs.
`--artifact-dir`, `--horizon`, `--year`, and `--round` remain optional
historical/catalog context for the checker. The retired hourly schema is kept
only for explicitly marked compatibility fixtures.

## Fast student preview

Students can use the `ercot-game-preview` command in Colab to iterate on a
full-half-year plan without waiting for the exact storage-coupled LP:

```bash
PYTHONPATH=src python -m ercot_game.student_preview \
  --submissions-dir path/to/submissions \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2028/jan_jun \
  --rules data/teaching/dispatch_scoring_rules_jan_jun_v1.json \
  --horizon 4368 \
  --workers 2 \
  --output preview.json
```

The preview keeps the official 4,368/4,416-hour index space but samples one
representative hour every six hours by default and scales the sampled totals
to the represented hours. It validates submitted storage and includes storage
construction cost, but uses an idle-storage dispatch policy for speed.
Consequently it is a conservative screening estimate and must not be used for
ranking or final scores. Use `--preview-stride 1` only for a slower diagnostic
run. The instructor batch evaluator remains authoritative and performs the
exact joint storage dispatch LP.

Use `data/processed/activsg2000_scenarios_v1/2027/jul_dec` with the
July--December rules for the second half-year. The scenario package manifest
records the six blocks and their validation state.

The CSV uses source `bus_id` values directly. Site capacities are specified in
MW/MWh, and each team's submitted resources serve only that team. The score
includes IT value, site cost, generator capital/operating cost, and storage
capital cost. Use `--submission-number 1` for practice; it has no
energy-purchase cost. Use `--submission-number 2` for scored evaluation; it
settles energy purchases at the selected joint dispatch LMP. The CLI defaults explicitly to Submission 2, and
`--submission-number` is not a CSV field. Submission 1 is practice; Submission
2 is scored.

## Data and privacy

Keep student CSVs, full evaluation JSON, dispatch diagnostics, and audit
bundles instructor-private. Do not publish cross-team plans. Raw downloads and
large local outputs remain outside version control according to `.gitignore`.

Run the active suite with:

```bash
PYTHONPATH=.:src pytest -q
```
