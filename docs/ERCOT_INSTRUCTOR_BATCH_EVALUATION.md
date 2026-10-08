# ERCOT Instructor Batch Evaluation

`ercot-game-batch` evaluates a directory of canonical site-level student CSV submissions
against one validated ACTIVSg2000 scenario block. It writes the ranked leaderboard
and audit artifacts to one output directory; that directory is the source of
truth for the ranking and audit record.

The production data path is the public synthetic Texas ACTIVSg2000 system.
Student files use source `bus_id` values from the selected block. The active
seven-column CSV has no asset-ID columns, including no generator- or storage-ID
columns. For positive
site-level capacities, the evaluator assigns deterministic private asset IDs
internally from the team and site IDs; students do not submit or invent those
labels. This guide describes the only supported production evaluation path.

The ACTIVSg2000 topology and generator catalog are fixed. The three-year
scenario package uses deterministic bus-level load and the static generator
status, PMIN, and PMAX values supplied by ACTIVSg2000. These are controlled
instructional scenarios, not historical ERCOT replays or official ERCOT
operating forecasts.

| Released year | January--June | July--December |
|---|---:|---:|
| 2027 | 4,344 h | 4,416 h |
| 2028 | 4,368 h | 4,416 h |
| 2029 | 4,344 h | 4,416 h |

The supported execution mode is from a source checkout. The Python runtime
packages, scoring rules, and prepared dispatch CSVs remain repository-level
resources and must be supplied by path.

## Run a batch

For a 2027 January--June release (4,344 one-hour periods):

```bash
ercot-game-batch run \
  --submissions-dir work/half_year_submissions \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2027/jan_jun \
  --rules data/teaching/dispatch_scoring_rules_jan_jun_v1.json \
  --year 2027 --round jan_jun \
  --workers 8 \
  --projection-iterations 10 \
  --output-dir work/batch_results_jan_jun
```

For July--December, use the matching year artifact and round:

```bash
ercot-game-batch run \
  --submissions-dir work/half_year_submissions \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2027/jul_dec \
  --rules data/teaching/dispatch_scoring_rules_jul_dec_v1.json \
  --year 2027 --round jul_dec \
  --workers 8 \
  --projection-iterations 10 \
  --output-dir work/batch_results_jul_dec
```

The command requires a submission directory, prepared year-specific dispatch
artifact, scoring-rules JSON, and output directory. The batch evaluator's
`--year`/`--round` or `--horizon` options remain evaluator context; they do not
describe rows that must appear in a student CSV. Student files identify
ACTIVSg2000 source buses directly with `bus_id`, one site per data row.

`--period-indices`, when supplied, selects local scoring periods and is reserved
for explicitly labeled smoke tests or debugging. `--source-period-offset`
maps local periods to source-artifact rows. The output records both local
`period_indices` and `source_period_indices`, so the calendar alignment is
auditable.

`--workers` evaluates independent hourly DCOPF solves concurrently while
preserving requested period order. `--projection-iterations` is a lower bound
on the bisection precision used by fair continuous projection; the evaluator
also evaluates the zero-service endpoint and uses a fixed tolerance, so the
old `1/1024` service cutoff does not apply.

## CSV and resource contract

Use the active canonical site-level header:

```text
team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh
team-blue,site-north,1001,40,20,10,40
team-blue,site-south,1002,15,0,0,0
```

Each data row is one site. The CSV contains no `period`, hourly `load_mw`,
`submission_id`, `generator_id`, or `storage_id` columns, and no hourly horizon
rows. Site capacities are specified in MW/MWh; the evaluator combines the
site-level plan with the selected hourly scenario and economic artifacts. The
evaluator derives deterministic private generator and storage IDs internally;
the asset-ID fields in the retired hourly schema are not valid current inputs.

The retired ten-column hourly schema is historical compatibility input only and
must not be used as a current student submission. In Python, use
`CANONICAL_CSV_COLUMNS`; `CSV_COLUMNS` and `BUS_CSV_COLUMNS` remain deprecated
aliases to the same tuple.

The dispatch model uses fixed submitted load and does not model curtailment.
Student generators and storage are team-owned local resources and cannot serve
another team. In prepared-artifact evaluation, storage is dispatched in one
centralized HiGHS binary MIP: each asset has power and energy limits, a binary
operating mode prevents simultaneous charge/discharge, and initial and
terminal state of charge are at least `0.5 B`. Charge and discharge are MW,
SOC is MWh, and each transition uses `time_step_hours` (one hour in the
released scenario blocks). The legacy no-artifact compatibility path is a
separate deterministic continuous peak-shaving schedule.

For the current classroom release, source generators use the status, `p_min_mw`,
and nameplate `p_max_mw` values from the ACTIVSg2000 generator catalog in every
hour. The source dataset does not provide hourly technology-specific
availability, so the release does not apply synthetic renewable deratings.

## Evaluation behavior

Submissions are discovered deterministically from `.csv` files. A malformed
submission is rejected in isolation and receives the configured infeasible
score; other valid teams remain eligible for evaluation. If the joint dispatch
is infeasible, the evaluator first applies one common fair continuous service
fraction to all valid teams. If no projected dispatch is available, it uses
deterministic leave-one-team-out and individual fallback behavior where
possible. The applied competition scope and service fraction are recorded in
the audit output.

The score includes IT value, energy purchase cost, generator operating cost,
generator capital cost, site cost, storage capital cost (MW and MWh
components), and storage operating cost. The evaluator's scoring-rules JSON is
the authoritative source for the numerical coefficients.

## Output artifacts

The output directory contains `leaderboard.csv`, `submission_manifest.csv`,
`evaluation.json`, `evaluation_sha256.txt`, per-team files under
`diagnostics/`, and per-period dispatch files under `dispatch/`. The JSON
records the rules, scenario and evaluator versions, local/source period
indices, input hashes, failure-isolation events, team scores, and output
hashes. Use the leaderboard for ranking and the manifest, diagnostics,
dispatch files, and evaluation JSON for audit and troubleshooting.
