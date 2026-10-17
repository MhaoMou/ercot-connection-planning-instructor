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
    -> controlled half-year scenario block (six-hour periods)
    -> canonical seven-column site CSV
    -> joint DCOPF and grid-operated storage
    -> fair projection when required
    -> score and privacy-safe classroom outputs
```

The canonical CSV is one row per site. The evaluator expands each constant site
capacity over the selected half-year horizon of six-hour periods. Students do
not submit time-series profiles, generator IDs, storage IDs, prices, dispatch schedules, or storage
state of charge.

## Network and horizon parameters

| Parameter | Value | Units | Origin | Rationale / reference |
|---|---:|---|---|---|
| Network | ACTIVSg2000 | synthetic Texas 2,000-bus model | DATASET-SPECIFIED | Public source model; see [ACTIVSg2000](https://github.com/caseformat/ACTIVSg2000). |
| Buses | 2,000 | buses | DATASET-SPECIFIED | Student `bus_id` values must come from the selected block. |
| Branches | 3,206 | branches | DATASET-SPECIFIED | Fixed topology used by the dispatch adapter. |
| Generator records | 544 | records | DATASET-SPECIFIED | Active status, PMIN, PMAX, and marginal-cost fields come from the catalog. |
| Time step | 6 | hours | INSTRUCTOR-CHOSEN | Each game period is the mean of six consecutive hourly source records; four periods per day. IT value, energy purchases, and generator operating cost multiply MW by 6 h. |
| January-June, 2027 or 2029 | 724 | six-hour periods | INSTRUCTOR-CHOSEN | Non-leap half-year calendar (4,344 source hours). |
| January-June, 2028 | 728 | six-hour periods | INSTRUCTOR-CHOSEN | Leap-year half-year calendar (4,368 source hours). |
| July-December, 2027-2029 | 736 | six-hour periods | INSTRUCTOR-CHOSEN | Second half of each released year (4,416 source hours). |
| Scenario package | `data/processed/activsg2000_scenarios_v2_6h/` | path | INSTRUCTOR-CHOSEN | Canonical six-hour package; scoring rules are `data/teaching/dispatch_scoring_rules_{jan_jun,jul_dec}_6h_v1.json`. |
| Existing-generator availability | Static status, PMIN, PMAX | MW and status | DATASET-SPECIFIED | ACTIVSg2000 has no technology labels or hourly availability fields; no synthetic renewable derating is applied. |
| Dispatch solver | HiGHS through the DCOPF adapter | solver | INSTRUCTOR-CHOSEN | Transparent, repeatable linear dispatch; storage-bearing horizons use the prepared storage-aware path. |

The three years are instructional scenario labels, not historical ERCOT years.
The selected block must have a passed validation manifest before production
use. The six block manifests of the six-hour package record
`validation_status: passed`.

## Classroom schedule and state boundaries

The game has three weekly matches and six half-year submissions:

| Week | Submission 1 | Submission 2 | Treatment |
|---|---|---|---|
| Week 1 | `2027_jan_jun` | `2027_jul_dec` | Practice week: feedback only; neither submission counts. |
| Week 2 | `2028_jan_jun` | `2028_jul_dec` | Scored weekly match; Submission 1 and Submission 2 both count. |
| Week 3 | `2029_jan_jun` | `2029_jul_dec` | Scored weekly match; Submission 1 and Submission 2 both count. |

Within a week, Submission 1 is the first-stage plan. Its sites and evaluator-
assigned assets are carried into Submission 2. Stored energy is not carried:
under the daily storage cycle every device starts and ends each day at 50%
state of charge, so each submission starts from the same SOC. Capital
cost (connection-site cost, generator capital, storage capital) is charged
once, in the submission where the asset is first built: Submission 1 pays for
everything it builds, and Submission 2 pays only for newly added sites and
assets. Assets carried over from Submission 1 are not charged again. At the
beginning of the next week, the asset ledger resets; the previous
week's assets do not receive credit or carry-over treatment.

Submission 1 has no energy-purchase cost. Submission 2 uses the LMP from its
selected joint dispatch for each team's grid purchases. Week 1 is practice: it
still produces feedback artifacts, but neither of its submissions contributes
to the final ranking. In Weeks 2 and 3 both submissions are scored, and the
final ranking is the sum of four scores: Week 2 Submission 1 + Week 2
Submission 2 + Week 3 Submission 1 + Week 3 Submission 2.

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

The checker is [`scripts/check_submission.py`](../scripts/check_submission.py):

```bash
PYTHONPATH=src python scripts/check_submission.py \
  --file team-blue.csv
```

Use `--artifact-dir` when the checker should validate `bus_id` against a
released scenario catalog. `--year`, `--round`, and `--horizon` are optional
report context; they never create a per-period row requirement. The evaluator
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
| IT-service value | 6,500 | USD/MWh served load | SOURCE-CALIBRATED, INSTRUCTOR-CHOSEN | AI-intensive instructional opportunity value, rounded from a recent compute-value estimate with a blended value near $6,350/MWh. Cloud prices remain contextual, not a direct tariff conversion. See [Compute Heat Rate](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6322318) and the [IEA Energy and AI executive summary](https://www.iea.org/reports/energy-and-ai/executive-summary). |
| Energy purchase cost, Submission 1 | 0 | USD | INSTRUCTOR-CHOSEN | Submission 1 settlement excludes grid-energy cost (it is still scored in Weeks 2 and 3). |
| Energy purchase cost, Submission 2 | Joint-dispatch LMP times grid purchase (MW) times 6 h | USD/MWh | DERIVED | The LMP is selected from the joint dispatch at the team's bus; it is not a student-chosen independent price. |
| Team-generator marginal curve | 25% at 80, 50% at 150, 25% at 250 | USD/MWh and capacity share | SOURCE-CALIBRATED, INSTRUCTOR-CHOSEN | Convex curve for generic colocated natural-gas reciprocating/simple-cycle generation with diesel-standby/scarcity premium; exact curve remains instructional. See [EIA 2026 LCOE page](https://www.eia.gov/outlooks/aeo/electricity_generation/), [EIA AEO2025 technology-cost report](https://www.eia.gov/analysis/studies/powerplants/capitalcost/pdf/capital_cost_AEO2025.pdf), and [California Energy Commission data-center overview](https://www.energy.ca.gov/programs-and-topics/topics/data-centers). |
| Team-generator capital | 1,500,000 | USD/MW | SOURCE-CALIBRATED, INSTRUCTOR-CHOSEN | Rounded near EIA's 2024 internal-combustion-engine installed cost of $1,522/kW; charged once, in the submission where the generator MW is first built. See [EIA generator costs](https://www.eia.gov/electricity/generatorcosts/). |
| Connection-site cost | 25,000,000 | USD/MW | SOURCE-CALIBRATED, INSTRUCTOR-CHOSEN | AI-data-center facility-capital proxy, not a universal interconnection tariff. JLL reports $10.7M/MW shell/core and up to $25M/MW AI fit-out; Morgan Stanley reports approximately $21–31M/MW including GPUs/servers. See [JLL outlook](https://www.jll.com/en-us/insights/market-outlook/global-data-centers) and [Morgan Stanley data-center research](https://www.morganstanley.com/content/dam/msdotcom/en/assets/pdfs/Research_Bridging-Data-Center-Gap.pdf). |
| Storage power capital | 400,000 | USD/MW | SOURCE-CALIBRATED, INSTRUCTOR-CHOSEN | Rounded component of the 2028 moderate four-hour storage projection. See [NREL 2023 storage projections](https://www.nrel.gov/docs/fy23osti/85332.pdf) and [NREL 2024 utility-scale storage ATB](https://atb.nrel.gov/electricity/2024/2023/utility-scale_battery_storage). |
| Storage energy capital | 250,000 | USD/MWh | SOURCE-CALIBRATED, INSTRUCTOR-CHOSEN | Rounded component of the same 2028 moderate four-hour projection. |
| Four-hour storage equivalent | 1,400,000 | USD/MW | DERIVED | `400,000 * P + 250,000 * (4P)` for power `P`; storage operating cost is zero. |
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

IT-service value, energy purchase cost, and team-generator operating cost are
energy terms: each sums a per-period MW quantity times its USD/MWh rate times
the 6-hour period length. Capital terms are charged once per newly built asset.

Existing public generators retain their dataset marginal costs. Team-owned
generators and storage are separate private assets and cannot serve another
team.

## Storage and dispatch rules

Students submit storage capacities only. The grid operator, not the student,
chooses charging and discharging in the centralized dispatch. For each asset:

```text
dt = 6 hours (one game period)
-storage_power_mw <= p[t] <= storage_power_mw     (p > 0 discharges, p < 0 charges)
SOC[t] = SOC[t-1] - dt * p[t]
0 <= SOC[t] <= storage_energy_mwh
dt * p[t] <= SOC[t-1]                               (discharge cannot exceed stored energy)
SOC at the start of every day = 0.5 * storage_energy_mwh
SOC at the end of every day   = 0.5 * storage_energy_mwh   (exact)
```

The storage boundary mode is `daily_exact_cycle`: a day is four consecutive
six-hour periods, every day starts half full and must end exactly half full,
and no stored energy carries between days or between submissions. The
transition uses 100% efficiency. The prepared HiGHS model represents each
storage asset with this one signed net-power variable per period, so the
dispatch is a linear program with no binary operating-mode variable. With ideal
efficiency and no storage operating cost, simultaneous charge and discharge has
no value, so this is equivalent to a two-variable charge/discharge form. Discharge can reduce only the owning team's load. It cannot be
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

The round coordinator carries accepted team-owned assets (and records the final
SOC, which the daily cycle fixes at 50%) from Submission 1 to Submission 2
within a week, charges capital only in the
submission where an asset is first built (Submission 2 is charged only for
newly added sites/assets), and resets the ledger at each new week. The state file is written only
after a usable dispatch and valid final SOC have been obtained.

The raw instructor output contains the full audit record, submitted plans,
dispatch details, and diagnostics. The classroom writer separately produces a
shared `classroom-safe-v1` summary with aggregate counts, aggregate periods,
and aggregate projection metadata. Each team receives an isolated hashed-
directory `classroom-private-v1` summary for its own score and diagnostics.
Do not publish raw CSVs, exact competitor sites, asset capacities, or full
cross-team dispatch JSON.

## Validation and benchmark limitations

- The canonical six-hour package (`activsg2000_scenarios_v2_6h`) records
  `validation_status: passed` for all six blocks, covering 4,384 six-hour
  states. The retired hourly package (`activsg2000_scenarios_v1`) and its
  hourly rule files are historical, are not used by the current game, and are
  not published in this repository.
- The student preview (`ercot-game-preview`, default `--preview-stride 6`)
  samples every sixth six-hour period (every 36 hours) and leaves storage idle.
  It can help students iterate, but instructor evaluation uses the selected
  full half-year block and release manifest.

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
