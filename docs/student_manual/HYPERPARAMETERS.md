# Hyperparameters and configuration

**Audience:** students and instructors using the active ACTIVSg2000 teaching game.

This is the compact provenance table for the active implementation. The Python
evaluator, the selected scenario block, and its scoring-rules JSON are
authoritative for a released run. The values below describe the checked-in
contract; an instructor must announce any separately versioned round override.

Origin categories:

- **DATASET-SPECIFIED** - copied from, or directly determined by, the public
  ACTIVSg2000 source package.
- **DERIVED** - computed by the evaluator from the selected network, joint
  dispatch, or classroom state.
- **SOURCE-CALIBRATED** - informed by the cited engineering, construction, or
  cloud-pricing reference and then mapped into the game.
- **INSTRUCTOR-CHOSEN** - selected for the learning objective, reproducibility,
  computational tractability, or student interface.

These labels describe the game configuration. None of the values is an official
ERCOT tariff, forecast, or operating requirement.

## Active model boundary

The active pipeline is:

```text
ACTIVSg2000 network and catalogs
    -> controlled half-year scenario block
    -> canonical seven-column site CSV
    -> joint DCOPF and grid-operated storage
    -> fair projection when required
    -> score and privacy-safe classroom outputs
```

The canonical CSV is one row per site. The evaluator expands each constant site
capacity over the selected half-year horizon. Students do not submit hourly
profiles, generator IDs, storage IDs, prices, dispatch schedules, or storage
state of charge.

## Network and horizon parameters

| Parameter | Value | Units | Origin | Rationale / reference |
|---|---:|---|---|---|
| Network | ACTIVSg2000 | synthetic Texas 2,000-bus model | DATASET-SPECIFIED | Public source model; see [ACTIVSg2000](https://github.com/caseformat/ACTIVSg2000). |
| Buses | 2,000 | buses | DATASET-SPECIFIED | Student `bus_id` values must come from the selected block. |
| Branches | 3,206 | branches | DATASET-SPECIFIED | Fixed topology used by the dispatch adapter. |
| Generator records | 544 | records | DATASET-SPECIFIED | Active status, PMIN, PMAX, and marginal-cost fields come from the catalog. |
| Time step | 1 | hour | INSTRUCTOR-CHOSEN | Literal hourly transition and settlement interval. |
| January-June, 2027 or 2029 | 4,344 | hours | INSTRUCTOR-CHOSEN | Non-leap half-year calendar. |
| January-June, 2028 | 4,368 | hours | INSTRUCTOR-CHOSEN | Leap-year half-year calendar. |
| July-December, 2027-2029 | 4,416 | hours | INSTRUCTOR-CHOSEN | Second half of each released year. |
| Existing-generator availability | Static status, PMIN, PMAX | MW and status | DATASET-SPECIFIED | ACTIVSg2000 has no technology labels or hourly availability fields; no synthetic renewable derating is applied. |
| Dispatch solver | HiGHS through the DCOPF adapter | solver | INSTRUCTOR-CHOSEN | Transparent, repeatable linear dispatch; storage-bearing horizons use the prepared storage-aware path. |

The three years are instructional scenario labels, not historical ERCOT years.
The selected block must have a passed validation manifest before production
use. In this checkout the manifests are `unverified_dependency` because
`highspy` is unavailable; this is a dependency limitation, not a passed or
failed DCOPF validation.

## Classroom schedule and state boundaries

The game has three weekly matches and six half-year submissions:

| Week | Submission 1 | Submission 2 | Treatment |
|---|---|---|---|
| Week 1 | `2027_jan_jun` | `2027_jul_dec` | Feedback-only practice week; excluded from final ranking. |
| Week 2 | `2028_jan_jun` | `2028_jul_dec` | Scored weekly match; Submission 2 is the scored result. |
| Week 3 | `2029_jan_jun` | `2029_jul_dec` | Scored weekly match; Submission 2 is the scored result. |

Within a week, Submission 1 is the first-stage plan. Its sites, evaluator-
assigned assets, and final storage SOC are carried into Submission 2. Generator
and storage capital cost is charged once when an asset is first built in that
weekly match. At the beginning of the next week, the asset ledger and SOC reset;
the previous week's assets do not receive credit or carry-over treatment.

Submission 1 is practice: it has no energy-purchase cost. Submission 2 uses
the LMP from its selected joint dispatch for each team's grid purchases. Week 1
still produces feedback artifacts, but neither of its submissions contributes
to the final ranking. The final ranking aggregates the scored Submission 2
results from Weeks 2 and 3.

## Canonical student CSV

The active header is exactly:

```text
team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh
```

Every data row is one site, so a multi-site submission looks like:

```text
team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh
team-blue,site-north,1001,40,20,10,40
team-blue,site-south,1002,15,0,0,0
```

The checker is [`scripts/check_submission.py`](../../scripts/check_submission.py):

```bash
PYTHONPATH=src python scripts/check_submission.py \
  --file team-blue.csv
```

Use `--artifact-dir` when the checker should validate `bus_id` against a
released scenario catalog. `--year`, `--round`, and `--horizon` are optional
report context; they never create an hourly-row requirement. The evaluator
remains authoritative.

| CSV field | Units | Origin | Rule |
|---|---|---|---|
| `team_id` | identifier | INSTRUCTOR-CHOSEN | One file contains one team. |
| `site_id` | identifier | INSTRUCTOR-CHOSEN | Unique within the file; defines the site identity used for asset IDs. |
| `bus_id` | catalog identifier | DATASET-SPECIFIED | Must exist in the selected block's `bus.csv`. |
| `connection_capacity_mw` | MW | INSTRUCTOR-CHOSEN | Finite and nonnegative constant site capacity. |
| `generator_capacity_mw` | MW | INSTRUCTOR-CHOSEN | Finite, nonnegative, and no greater than connection capacity. |
| `storage_power_mw` | MW | INSTRUCTOR-CHOSEN | Charge/discharge power capacity; zero only with zero energy capacity. |
| `storage_energy_mwh` | MWh | INSTRUCTOR-CHOSEN | Must equal `4 * storage_power_mw` when storage is present. |

The evaluator assigns deterministic private generator and storage asset IDs from
the team/site identity. Those IDs are not student inputs and are not public
generator-catalog IDs. Historical/legacy fixtures may contain the retired
hourly schema and submitted asset labels, but they are compatibility inputs
only and must not be copied into an active submission.

## Active economic parameters

| Parameter | Value | Units | Origin | Rationale / reference |
|---|---:|---|---|---|
| IT-service value | 1,500 | USD/MWh served load | INSTRUCTOR-CHOSEN, SOURCE-CALIBRATED | Explicit AI-intensive opportunity-value profile; cloud compute pricing is a contextual reference, not a direct tariff conversion. See [AWS EC2](https://aws.amazon.com/ec2/pricing/), [Azure VM pricing](https://azure.microsoft.com/en-us/pricing/details/virtual-machines/), and [Google Cloud Compute pricing](https://cloud.google.com/products/compute/pricing). |
| Energy purchase cost, Submission 1 | 0 | USD | INSTRUCTOR-CHOSEN | Practice settlement excludes grid-energy cost. |
| Energy purchase cost, Submission 2 | Joint-dispatch LMP times grid purchase | USD/MWh | DERIVED | The LMP is selected from the joint dispatch at the team's bus; it is not a student-chosen independent price. |
| Team-generator marginal curve | 25% at 60, 50% at 100, 25% at 160 | USD/MWh and capacity share | INSTRUCTOR-CHOSEN, SOURCE-CALIBRATED | Common convex teaching curve; EIA heat-rate and generator-cost data inform the calibration. See [EIA generator costs](https://www.eia.gov/electricity/generatorcosts/) and [EIA heat-rate data](https://www.eia.gov/todayinenergy/detail.php?id=52158). |
| Team-generator capital | 1,300,000 | USD/MW | INSTRUCTOR-CHOSEN, SOURCE-CALIBRATED | Charged once per newly built generator MW in a weekly match; informed by EIA/NREL technology-cost methods. |
| Connection-site cost | 20,000,000 | USD/MW | INSTRUCTOR-CHOSEN, SOURCE-CALIBRATED | Capacity-dependent game coefficient. JLL reports data-center shell/core construction near 10.7 million USD/MW in 2025 and AI fit-out up to 25 million USD/MW; the game uses 20 million USD/MW as an instructional middle ground. See [JLL 2026 Global Data Center Outlook](https://www.jll.com/content/dam/jllcom/en/global/documents/reports/research-reports/26-research-global-data-center-outlook.pdf). |
| Storage power capital | 350,000 | USD/MW | INSTRUCTOR-CHOSEN, SOURCE-CALIBRATED | Power component of the approved four-hour storage split; informed by EIA and NREL battery benchmarks. |
| Storage energy capital | 200,000 | USD/MWh | INSTRUCTOR-CHOSEN, SOURCE-CALIBRATED | Energy component of the approved four-hour storage split; see [NREL storage ATB](https://atb.nrel.gov/electricity/2024/2023/utility-scale_battery_storage) and [EIA storage assumptions](https://www.eia.gov/analysis/studies/powerplants/capitalcost/pdf/capcost_assumption.pdf). |
| Four-hour storage equivalent | 1,150,000 | USD/MW | DERIVED | `350,000 * P + 200,000 * (4P)` for power `P`; storage operating cost is zero. |
| Storage operating cost | 0 | USD/MWh | INSTRUCTOR-CHOSEN | The grid operator schedules storage without a variable storage-cost term. |
| Invalid/infeasible fallback score | 0 | USD | INSTRUCTOR-CHOSEN | Deterministic grading value when no usable projected/fallback dispatch exists. |

The score is:

```text
IT-service value
- energy purchase cost (Submission 2 only)
- team-generator operating cost
- generator capital cost
- connection-site cost
- storage capital cost
```

Existing public generators retain their dataset marginal costs. Team-owned
generators and storage are separate private assets and cannot serve another
team.

## Storage and dispatch rules

Students submit storage capacities only. The grid operator, not the student,
chooses charging and discharging in the centralized dispatch. For each asset:

```text
0 <= charge[t] <= storage_power_mw
0 <= discharge[t] <= storage_power_mw
SOC[t+1] = SOC[t] + charge[t] - discharge[t]
0 <= SOC[t] <= storage_energy_mwh
SOC[0] = 0.5 * storage_energy_mwh
SOC[end] >= 0.5 * storage_energy_mwh
```

The transition uses one hour and 100% efficiency. The prepared HiGHS model
uses a binary operating-mode variable to prevent simultaneous charge and
discharge. Discharge can reduce only the owning team's load. It cannot be
exported to another team. A separate continuous peak-shaving path exists only
for explicitly marked no-artifact compatibility tests; it is not the active
classroom dispatch.

## Fair projection and execution parameters

Projection is evaluator policy, not a student control. Before projection, the
evaluator applies fixed anti-manipulation request caps:

| Parameter | Value | Units | Origin | Rationale / reference |
|---|---:|---|---|---|
| Per-site request cap | 100 | MW | INSTRUCTOR-CHOSEN | Prevents one oversized site from dominating projection. |
| Per-team request cap | 500 | MW | INSTRUCTOR-CHOSEN | Fixed team-level anti-manipulation cap. |
| Multi-team request ratio | 1.0 times the smallest positive team request | ratio | INSTRUCTOR-CHOSEN | Equalizes the largest request to the smallest positive request before projection. |
| Minimum positive service fraction | 1e-6 | fraction | INSTRUCTOR-CHOSEN | Keeps every positive valid request eligible for service. |
| Feasibility tolerance | 1e-6 | fraction | INSTRUCTOR-CHOSEN | Bisection and boundary-search stopping tolerance. |
| Total-load tolerance | max(1e-6, 1e-6 times optimum) | MWh | INSTRUCTOR-CHOSEN | Defines the near-optimal set for fairness tie-breaking. |
| Projection objective | total served load, then sum of log served loads | objective | INSTRUCTOR-CHOSEN | Maximizes total service first, then proportional fairness among near-optimal solutions. |
| CLI `--projection-iterations` | 10 default; internal ray search at least 32 | iterations | INSTRUCTOR-CHOSEN | Runtime lower bound with deterministic refinement. |
| Malformed submission | Reject file; continue valid teams | policy | INSTRUCTOR-CHOSEN | Failure isolation prevents one file from erasing other teams' results. |

The result records a separate service fraction and projected load for each team,
the cap policy, and the projection scope. It does not use first-come-first-
serve ordering. If joint projection is unavailable, deterministic leave-one-
team-out and individual fallback attempts are recorded where possible.

## Persistent assets and privacy-safe outputs

The round coordinator carries accepted team-owned assets and final SOC from
Submission 1 to Submission 2 within a week, charges newly built capital only
once, and resets the ledger at each new week. The state file is written only
after a usable dispatch and valid final SOC have been obtained.

The raw instructor output contains the full audit record, submitted plans,
dispatch details, and diagnostics. The classroom writer separately produces a
shared `classroom-safe-v1` summary with aggregate counts, aggregate periods,
and aggregate projection metadata. Each team receives an isolated hashed-
directory `classroom-private-v1` summary for its own score and diagnostics.
Do not publish raw CSVs, exact competitor sites, asset capacities, or full
cross-team dispatch JSON.

## Validation and benchmark limitations

- The checked-in scenario root and block manifests currently report
  `validation_status: unverified_dependency` and
  `release_status: blocked_pending_highspy_validation` because `highspy` is
  unavailable in this environment. A complete HiGHS-backed validation of all
  26,304 hourly states is required before classroom release.
- The Colab benchmark is measurement-only and does not alter evaluator math.
  It requires `highspy`, `psutil`, the prepared artifacts, and a compatible
  Colab runtime. No generated full-horizon result artifact means no performance
  conclusion; the official 4,368/4,416-hour horizons must not be shortened from
  an intuition or a partial smoke test.
- A short local screening run can help students iterate, but instructor
  evaluation uses the selected full half-year block and release manifest.

## References

1. [ACTIVSg2000 source model](https://github.com/caseformat/ACTIVSg2000).
2. [EIA generator construction costs](https://www.eia.gov/electricity/generatorcosts/).
3. [EIA natural-gas heat-rate comparison](https://www.eia.gov/todayinenergy/detail.php?id=52158).
4. [EIA battery-storage cost assumptions](https://www.eia.gov/analysis/studies/powerplants/capitalcost/pdf/capcost_assumption.pdf).
5. [NREL 2021 utility-scale battery-storage ATB](https://atb.nrel.gov/electricity/2021/utility-scale_battery_storage).
6. [NREL 2024 utility-scale battery-storage ATB](https://atb.nrel.gov/electricity/2024/2023/utility-scale_battery_storage).
7. [JLL 2026 Global Data Center Outlook](https://www.jll.com/content/dam/jllcom/en/global/documents/reports/research-reports/26-research-global-data-center-outlook.pdf).
8. [AWS EC2 pricing](https://aws.amazon.com/ec2/pricing/), [Azure VM pricing](https://azure.microsoft.com/en-us/pricing/details/virtual-machines/), and [Google Cloud Compute pricing](https://cloud.google.com/products/compute/pricing).
9. [Fairness algorithm for emergency demand response](https://doi.org/10.1016/j.ijepes.2021.107871).
