# ERCOT Instructor Batch Evaluation

`ercot-game-batch` evaluates a directory of long-form student CSV submissions
against one validated ACTIVSg2000 scenario block. It writes the ranked leaderboard
and audit artifacts to one output directory; that directory is the source of
truth for the ranking and audit record.

The production data path is the public synthetic Texas ACTIVSg2000 system.
Student files use source `bus_id` values from the selected block. Team-built
generator and storage IDs are self-defined labels. This guide describes the only supported
production evaluation path.

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
artifact, scoring-rules JSON, output directory, and either an explicit
positive horizon or `--year` plus `--round`. Student files
identify ACTIVSg2000 source buses directly with `bus_id`. Every bus appearing in a
production CSV must have one row for every period in the selected half-year.
Partial pilot files are rejected before dispatch.

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

Use the active bus-based header:

```text
team_id,submission_id,period,bus_id,load_mw,generator_id,generator_capacity_mw,storage_id,storage_power_mw,storage_energy_mwh
```

`period` is one-based in the CSV (`1..4344`, `1..4368`, or `1..4416`, depending
on the released calendar). Each submitted bus
must be present for every period, including periods with zero load. Generator
IDs must be present in `generator_limits.csv`; their marginal operating costs
come from that public-data catalog rather than from a student-supplied cost.
Storage fields are active: a storage device has a positive power capacity in
MW and energy capacity in MWh, and its specification must remain consistent
across rows.

The dispatch model uses fixed submitted load and does not model curtailment.
Student generators and storage are team-owned local resources and cannot serve
another team. Storage is scheduled continuously with power and energy limits,
no simultaneous charge/discharge, initial and terminal state of charge at least
`0.5 B`, and one-hour energy transitions.

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
