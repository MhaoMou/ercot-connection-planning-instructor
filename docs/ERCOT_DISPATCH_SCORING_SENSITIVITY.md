# Dispatch-scoring sensitivity report

**Status:** Historical instructor calibration draft for the retired six-period
pilot. It is retained for provenance only and is not an active release
configuration.

This document records a historical sensitivity analysis of the retired
six-period pilot rules. The pilot rules file is intentionally no longer part of
the production repository; current scoring uses the half-year rule files and
hourly ACTIVSg2000 economic-profile artifacts.

The authoritative sweep evaluates all six zero-based pilot periods. Under the
legacy pilot horizon, each representative period is weighted as `8760 / 6 =
1460` hours. This report does not describe the released half-year rules.

## Summary

- Sweep rows: **246**.
- Feasible score configurations: **243**.
- Distinct feasible ranking orders: **2**.
- Feasible score range observed: **$2,539,894.00–$36,639,576.00**.
- Stress scenarios detected as infeasible: `stress-infeasible`.

## Interpretation

This sweep is a calibration diagnostic, not a claim that the tested plan set is representative of student behavior. It confirms that the current provisional rules preserve a mixture of feasible and infeasible plans, produce more than one feasible ranking outcome, and keep capital/site costs on an annualized teaching-horizon scale. Scores are annual-scale totals because all six representative periods are included.

The test scenarios are intentionally small and synthetic. The next calibration check should use pilot CSV submissions from actual student teams before the rules are frozen.

## Scenario results

| Scenario | Generator cost ($/MWh) | Feasible rows | Infeasible rows | Ranking orders | Score range ($) |
|---|---:|---:|---:|---:|---:|
| generator-choice | 50, 100, 150 | 81 | 0 | 2 | 9,309,576.00–36,639,576.00 |
| multi-site | 50, 100, 150 | 81 | 0 | 1 | 10,259,576.00–36,639,576.00 |
| small-diverse-sites | 50, 100, 150 | 81 | 0 | 1 | 2,539,894.00–9,159,894.00 |
| stress-infeasible | 50, 100, 150 | 0 | 3 | 0 | — |

## Current provisional anchors

- `time_step_hours = 1460`: six representative periods cover one 8,760-hour year.
- `generator_capital_cost_per_mw = $85,000/MW-year`: rounded annualized combustion-turbine anchor based on EIA's $841/kW installation-cost reference, a 20-year life, and an 8% discount rate.
- `site_cost_per_site = $50,000/site`: instructional site-level proxy, not a universal data-center interconnection tariff.

These values are documented as historical pilot values. They must not be used
for current half-year evaluation; use the versioned half-year rules files with
`time_step_hours = 1` instead.

## Instructor decisions still required

1. Select a candidate-generator technology and replace the generic variable-cost placeholder.
2. Do not approve the six-period horizon weighting for the current release;
   review the half-year rules instead.
3. Confirm whether site cost should remain per site or become a per-MW/interconnection-cost proxy.
4. Review score/ranking spread against actual pilot CSVs before approving release rules.
