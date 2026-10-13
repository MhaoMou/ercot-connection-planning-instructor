# ERCOT Instructor Batch Evaluation

`ercot-game-batch` evaluates canonical site-level student CSV submissions
against one ACTIVSg2000 scenario block. The Python evaluator and the selected
scoring-rules JSON are authoritative. This guide describes the active
instructor workflow; historical/legacy hourly fixtures are compatibility tests,
not current student inputs.

## Release boundary and limitations

The production data path is the public synthetic Texas ACTIVSg2000 system.
Student files use source `bus_id` values from the selected block. The active
scenario is controlled instructional data, not historical ERCOT telemetry, a
forecast, or an official operating model.

In this checkout, the root and block manifests report
`validation_status: unverified_dependency` and
`release_status: blocked_pending_highspy_validation` because `highspy` is
unavailable. Do not treat the checked-in manifests as passed. Install
`highspy` and complete HiGHS-backed validation of all 26,304 hourly states
before classroom use. The Colab benchmark is separate measurement-only work:
it requires `highspy`, `psutil`, prepared artifacts, and a compatible
runtime. It does not change evaluator math, and no unrun or partial benchmark
justifies shortening the official horizon.

## Classroom schedule

There are three weekly matches and six half-year submissions:

| Week | Submission 1 | Submission 2 | Ranking treatment |
|---|---|---|---|
| Week 1 | `2027_jan_jun` | `2027_jul_dec` | Feedback-only practice week; excluded from final ranking. |
| Week 2 | `2028_jan_jun` | `2028_jul_dec` | Scored match; Submission 2 is ranked. |
| Week 3 | `2029_jan_jun` | `2029_jul_dec` | Scored match; Submission 2 is ranked. |

The released horizons are:

| Released year | January-June | July-December |
|---|---:|---:|
| 2027 | 4,344 h | 4,416 h |
| 2028 | 4,368 h | 4,416 h |
| 2029 | 4,344 h | 4,416 h |

Within one week, Submission 1 is the first-stage plan. The coordinator carries
accepted sites, evaluator-assigned generator and storage assets, and final
storage SOC into Submission 2. Generator, storage, and newly built connection
capital is charged once in that weekly match. At the start of the next week,
the asset ledger and SOC reset. Final ranking aggregates valid scored
Submission 2 results from Weeks 2 and 3.

## Run a half-year batch

For a 2027 January-June release:

```bash
ercot-game-batch run \
  --submissions-dir work/half_year_submissions \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2027/jan_jun \
  --rules data/teaching/dispatch_scoring_rules_jan_jun_v1.json \
  --year 2027 --round jan_jun \
  --submission-number 2 \
  --workers 8 \
  --projection-iterations 10 \
  --output-dir work/batch_results_jan_jun
```

For July-December, use the matching year artifact and rules:

```bash
ercot-game-batch run \
  --submissions-dir work/half_year_submissions \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2027/jul_dec \
  --rules data/teaching/dispatch_scoring_rules_jul_dec_v1.json \
  --year 2027 --round jul_dec \
  --submission-number 2 \
  --workers 8 \
  --projection-iterations 10 \
  --output-dir work/batch_results_jul_dec
```

`--submission-number` is evaluator-side metadata, not a CSV field. It accepts
`1` or `2` and defaults to `2`.

- `--submission-number 1` is practice and excludes energy-purchase cost while
  retaining the other active scoring terms.
- `--submission-number 2` settles each team's grid purchases at the
  nonnegative LMP from that submission's joint dispatch.
- Week 1 remains feedback-only even though its second submission uses the
  Submission 2 settlement rule.

The `--year`, `--round`, and `--horizon` options describe evaluator
context. They do not describe rows that must appear in a student CSV.
`--period-indices` is reserved for explicitly labeled smoke tests or
debugging; official evaluation uses the full selected horizon.

## Canonical CSV and checker

The active student header is exactly seven columns:

```text
team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh
team-blue,site-north,1001,40,20,10,40
team-blue,site-south,1002,15,0,0,0
```

Every row is one site. The file has no `period`, hourly `load_mw`,
`submission_id`, `generator_id`, or `storage_id` columns and no hourly
horizon rows. `bus_id` must exist in the selected block's `bus.csv`.
Generator capacity cannot exceed connection capacity, and storage energy must
equal four times storage power.

Run the student-facing checker:

```bash
PYTHONPATH=src python scripts/check_submission.py \
  --file path/to/team.csv
```

Add `--artifact-dir` to validate catalog bus IDs. The checker is a
pre-submission convenience; batch evaluation remains authoritative.

The evaluator assigns deterministic private generator and storage asset IDs from
the team/site identity. Students do not choose public generator IDs or storage
IDs. The retired hourly format and files under explicitly labeled legacy
fixture directories remain only for compatibility and reproducibility tests;
never distribute them as active templates.

## Storage and dispatch contract

Students submit storage capacities. The grid operator schedules charge,
discharge, and SOC in the centralized prepared HiGHS model. Each storage asset
has:

- power limits in MW and energy limits in MWh;
- 100% charge/discharge efficiency and a one-hour SOC transition;
- initial SOC equal to one-half of energy capacity;
- terminal SOC at least one-half of energy capacity;
- a binary operating mode preventing simultaneous charge and discharge;
- a discharge restriction to the owning team's load.

Storage cannot be used to serve another team, and storage operating cost is not
charged. A separate continuous peak-shaving path exists only for explicitly
marked no-artifact compatibility tests.

## Economics and provenance

For a valid team, the score is:

```text
IT-service value
- energy purchase cost (Submission 2 only)
- team-generator operating cost
- generator capital cost
- connection-site cost
- storage capital cost
```

The active coefficients are:

| Parameter | Active value | Units | Origin and reference |
|---|---:|---|---|
| IT-service value | 6,500 | USD/MWh | Source-informed AI-compute opportunity value, rounded from a recent blended compute-value estimate; not a cloud tariff. |
| Team-generator curve | 80 / 150 / 250 over 25% / 50% / 25% | USD/MWh | Source-informed convex curve for colocated natural-gas engines/turbines with standby/scarcity premium. |
| Generator capital | 1,500,000 | USD/MW | Rounded near EIA's 2024 internal-combustion-engine installed cost of $1,522/kW. |
| Connection-site cost | 25,000,000 | USD/MW | AI-data-center facility-capital proxy, not a universal grid-interconnection tariff; informed by JLL and Morgan Stanley. |
| Storage power capital | 400,000 | USD/MW | Rounded component of NREL's 2028 moderate four-hour projection. |
| Storage energy capital | 250,000 | USD/MWh | Rounded component of the same NREL projection. |
| Four-hour storage equivalent | 1,400,000 | USD/MW | Derived from `400,000 * P + 250,000 * (4P)`. |
| Storage operating cost | 0 | USD/MWh | Explicit instructor-chosen zero-cost rule. |
| Invalid/infeasible fallback score | 0 | USD | Deterministic instructor policy. |

The current economic-profile artifacts contain a full-horizon constant IT-value
vector and bus/hour LMP reference data. Submission 2 settlement uses the LMP
from the selected joint dispatch, not a student-chosen price vector. Existing
public generators use dataset marginal costs; team-built generators use the
common convex curve above.

## Fair projection and failure isolation

Malformed files are rejected independently so valid teams remain eligible. If
the valid joint request is infeasible, the evaluator applies fixed caps before
projection:

- 100 MW per site;
- 500 MW per team;
- in a multi-team case, no request exceeds the smallest positive team request.

It then chooses separate `alpha_i` values by first maximizing total served load.
Within a tolerance of the optimum, it maximizes proportional-fair utility,
the sum of log served loads, while enforcing a minimum positive service
fraction of `1e-6`. Projection tolerance is `1e-6`; the CLI option
`--projection-iterations 10` is a lower bound and internal ray search uses at
least 32 iterations. The audit records per-team fractions, projected load,
request caps, objective, and scope.

No first-come-first-serve ordering is used. If projection is unavailable, the
evaluator records deterministic leave-one-team-out and individual fallback
attempts where possible. A valid team's score is not silently erased by one
oversized or malformed submission.

## Privacy-safe outputs

The raw batch output is instructor-private and may contain submitted CSV
hashes, exact plans, full dispatch periods, per-team diagnostics, asset
capacities, and failure details. The classroom writer creates separate outputs:

- shared `classroom-safe-v1` summaries contain aggregate team counts, aggregate
  period/network measures, and aggregate projection metadata;
- a team's `classroom-private-v1` file lives in a hashed directory and contains
  that team's own score and scalar diagnostics;
- exact competitor plans, raw dispatch JSON, and team-specific failure details
  are not classroom-shared.

Keep submission directories, state files, raw audit JSON, and full diagnostics
instructor-only. Release only the intended team summary and shared aggregates.

## Output artifacts

A single half-year batch output contains `leaderboard.csv`,
`submission_manifest.csv`, `evaluation.json`,
`evaluation_sha256.txt`, per-team files under `diagnostics/`, and per-period
dispatch files under `dispatch/`. A classroom run additionally writes
`week-*/submission-*/shared/summary.json`,
`shared/aggregate_periods.json`, hashed team summaries, and classroom output
hashes. Use the leaderboard and classroom-safe summaries for release; retain
the manifest, diagnostics, dispatch files, state file, and evaluation JSON for
audit.
