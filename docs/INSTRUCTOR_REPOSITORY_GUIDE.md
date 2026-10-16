# Instructor Repository Guide

This repository contains the current instructor-side implementation of the
ACTIVSg2000-based data-center connection-planning game. Its active
responsibility is to validate student CSV files, run joint DCOPF dispatch on a
release-approved scenario block, and produce auditable team scores.

The canonical six-hour scenario package has passed the complete HiGHS-backed
release validation. The literal-hour package remains available as a
derivation/reference input and is not the default student-game release.

## Production boundary

```text
ACTIVSg2000 hourly source -> validated six-hour scenario block (~2,000 buses)
        -> canonical seven-column site-level student CSV
        -> catalog validation (optional checker context)
        -> daily-cycle joint DCOPF
        -> fair continuous projection when needed
        -> score components, ranking, and audit outputs
```

The retired browser/practice implementation is recoverable under
`archive/legacy/`; it is not packaged, launched, or part of the current game.

## Important paths

- `src/ercot_game/`: canonical CSV and batch-evaluation code.
- `tools/activsg2000/`: scenario construction, validation, DCOPF, and dispatch adapter.
- `data/processed/activsg2000_scenarios_v2_6h/`: canonical, source-controlled
  three-year six-hour scenario package; all six blocks are release-validated.
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
  --artifact-dir data/processed/activsg2000_scenarios_v2_6h/2027/jan_jun \
  --rules data/teaching/dispatch_scoring_rules_jan_jun_6h_v1.json \
  --year 2027 --round jan_jun \
  --submission-number 2 \
  --horizon 724 \
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
  --artifact-dir data/processed/activsg2000_scenarios_v2_6h/2028/jan_jun \
  --rules data/teaching/dispatch_scoring_rules_jan_jun_6h_v1.json \
  --horizon 728 \
  --workers 2 \
  --output preview.json
```

The preview is a conservative screening tool. For the canonical release, use
the 724/728/736 game-period horizon. Its default stride samples every six
game periods (36 real hours); use `--preview-stride 1` to inspect every
six-hour game period. It scales the sampled totals
to the represented hours. It validates submitted storage and includes storage
construction cost, but uses an idle-storage dispatch policy for speed.
Consequently it is a conservative screening estimate and must not be used for
ranking or final scores. The instructor batch evaluator remains authoritative and performs the
exact joint storage dispatch LP.

Use `data/processed/activsg2000_scenarios_v2_6h/2027/jul_dec` with the
six-hour July--December rules for the second half-year. The scenario package
manifest records the six blocks and their validation state.

The CSV uses source `bus_id` values directly. Site capacities are specified in
MW/MWh, and each team's submitted resources serve only that team. The score
includes IT value, site cost, generator capital/operating cost, and storage
capital cost. Use `--submission-number 1` for Submission 1; it has no
energy-purchase cost. Use `--submission-number 2` for Submission 2; it
settles energy purchases at the selected joint dispatch LMP. The CLI defaults explicitly to Submission 2, and
`--submission-number` is not a CSV field. Week 1 (2027) is practice/feedback
only; in Weeks 2 and 3 both submissions are scored, and the final ranking is
Week 2 S1 + Week 2 S2 + Week 3 S1 + Week 3 S2. Capital cost is charged once,
in the submission where an asset is first built; the `classroom` command
carries Submission 1 assets into Submission 2 without a second charge.

## Classroom grid map

After each evaluated submission, release a privacy-safe interactive map with
the score breakdown and service fractions. Each bus is colored by the total
connection capacity of all valid teams at that bus (no team or site IDs appear
in the file), and each line by its peak loading over the evaluated hours.

```bash
ercot-game-grid-map \
  --artifact-dir data/processed/activsg2000_scenarios_v2_6h/2028/jan_jun \
  --submissions-dir <round submissions directory> \
  --dispatch-dir <evaluator output directory>/dispatch \
  --title "Week 2, Submission 1 (2028 January-June)" \
  --output <release directory>/grid_map.svg
```

For Submission 2, repeat `--submissions-dir` with the Submission 1 directory so
carried-over sites are included. The bus positions come from
`data/teaching/activsg2000_layout_v1.json`, a deterministic schematic layout
(the scenario package has no coordinates), so every round's map looks the same.
A bus with a single team still reveals that team's total at that bus, but not
which team it is.

## Data and privacy

Keep student CSVs, full evaluation JSON, dispatch diagnostics, and audit
bundles instructor-private. Do not publish cross-team plans. Raw downloads and
large local outputs remain outside version control according to `.gitignore`.

Run the active suite with:

```bash
PYTHONPATH=.:src pytest -q
```
