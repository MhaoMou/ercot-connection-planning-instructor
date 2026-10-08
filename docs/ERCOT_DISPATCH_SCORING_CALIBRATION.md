# Dispatch-game scoring calibration

**Status:** Provisional calibration package for instructor review.

This document separates parameters that can be anchored to public engineering or
market data from parameters that are intentionally instructional. It applies to
the CSV/DCOPF competition path, which uses fixed submitted load and no
curtailment.

## Source-backed anchors

### Energy price

The scored evaluator settles Submission 2 grid purchases at the nonnegative LMP
from the selected joint dispatch at the selected bus and hour. Submission 1 is
practice and has no energy-purchase cost. These are instructional model-derived
LMPs, not official ERCOT market prices. Baseline ACTIVSg2000 LMP artifacts are
released economic-profile references and fallbacks, not a replacement for the
joint-dispatch settlement when a dispatch LMP is available.

Sources:

- [ERCOT 2024 ORDC report](https://www.ercot.com/files/docs/2024/10/31/2024-biennial-ercot-report-on-the-ordc-20241031.pdf)
- [ERCOT real-time SPP display](https://www.ercot.com/content/cdr/html/20241107_real_time_spp.html)

The two active rule files reference the hourly economic-profile artifacts used
to describe the price surface. Production Submission 2 settlement uses the
joint-dispatch LMP; no repeated six-value price vector is used in production
scoring.

### Existing and candidate generator costs

For existing units, use the ACTIVSg2000 gencost records carried into the
validated scenario block. For candidate technologies, calculate a
marginal cost as:

```text
fuel price × heat rate + variable O&M
```

NREL ATB provides technology-specific CAPEX, fixed O&M, variable O&M, fuel,
capacity-factor, and performance assumptions. EIA also publishes generator
construction costs and historical operating expenses.

Sources:

- [NREL 2024 ATB technologies](https://atb.nrel.gov/electricity/2024/technologies)
- [NREL ATB equations and variables](https://atb.nrel.gov/electricity/2024/equations_%26_variables)
- [EIA generator construction costs](https://www.eia.gov/electricity/generatorcosts/)
- [EIA generation-cost data](https://www.eia.gov/tools/faqs/faq.php?id=19&t=6)

**Provisional rule:** do not use one universal `$100/MWh` value in the released
game. Select a named candidate technology and publish its cost assumptions, or
retain the default only for a clearly labeled pilot.

### Storage construction and operation cost

The evaluator separates storage capital cost into power capacity and energy
capacity:

```text
storage capital cost = $350,000/MW × power capacity
                     + $200,000/MWh × energy capacity
storage operating cost = not charged
```

This gives approximately `$1,150/kW` for a four-hour system. It is close to
EIA's 2024 capacity-weighted battery construction benchmark of `$1,469/kW`
and NREL's 2021 utility-scale four-hour total-system benchmark of about
`$1,365/kW`. Storage operating cost is intentionally omitted from the score.

Sources:

- [EIA construction cost data](https://www.eia.gov/electricity/generatorcosts/)
- [EIA battery-storage cost assumptions](https://www.eia.gov/analysis/studies/powerplants/capitalcost/pdf/capcost_assumption.pdf)
- [NREL 2021 utility-scale battery storage ATB](https://atb.nrel.gov/electricity/2021/utility-scale_battery_storage)
- [NREL 2024 utility-scale battery storage ATB](https://atb.nrel.gov/electricity/2024/2023/utility-scale_battery_storage)

These are rounded instructional coefficients, not a project-specific bid or
tariff. The half-year rules files record the same provenance in JSON.

### Generator capital cost

Convert source CAPEX from `$ / kW` to `$ / MW`, then annualize it with the
chosen capital-recovery assumptions. The legacy calibration note used
`1,460` hours per representative period. The released half-year files use
literal one-hour intervals, so capital coefficients are applied once to the
plan and hourly operating quantities are multiplied by `1` hour.

The missing calibration choices are:

1. technology;
2. economic life;
3. discount rate or fixed-charge rate;
4. fraction of annualized cost assigned to the game.

NREL gives the relevant fixed-charge-rate and LCOE equations in its ATB
methodology.

The approved teaching rule is `$1,300,000/MW`, charged once for generator
capacity newly built in a weekly match.

### Site/interconnection cost

LBNL reports empirical generator interconnection costs, including an average of
approximately `$194/kW` for completed projects during 2018–2024 in its recent
study. This is a useful anchor for generation interconnection, but it is not a
universal data-center load-connection tariff.

Source:

- [LBNL generator interconnection cost report](https://emp.lbl.gov/publications/generator-interconnection-costs-0)

**Approved rule:** use `$20,000,000/MW` of newly built connection capacity.
This is a capacity-dependent AI-heavy connection-site cost and is charged once
within a weekly match.

## IT-service value profile

The released IT-service value is a complete, deterministic vector:

```text
IT_value[t] = 1,500 USD/MWh, for every hour t in the selected block
```

January--June contains 4,344 hours/values in non-leap years and 4,368 hours/values in the leap year; July--December contains 4,416 hours/values in
every released year. This is
an instructor-chosen opportunity value for
served computational load. It is deliberately constant in the first release;
it is not a repeated six-hour cycle and is not an electricity price. See
[`docs/IT_SERVICE_VALUE_PROFILE.md`](IT_SERVICE_VALUE_PROFILE.md) for the
generation contract and future replacement requirements.

## Instructional parameters

The following cannot be responsibly inferred as a single market number:

| Parameter | Current decision needed |
|---|---|
| IT value of served load | Released as the AI-intensive full-horizon constant `$1,500/MWh`. |
| Infeasible score | Confirm `0`; no partial-service score is used. |
| Maximum load per site/team | Choose from DCOPF feasibility sweeps so both feasible and infeasible plans exist. |
| Maximum candidate-generator capacity | Set relative to site peak load. |
| Number of sites/generators | Decide whether assets are unlimited or bounded. |
| Period horizon and price artifact | The submission horizon is the complete released half-year: 4,344 non-leap Jan-Jun, 4,368 leap Jan-Jun, or 4,416 Jul-Dec; Submission 2 energy prices come from the selected joint-dispatch LMP. |
| Weekly submission aggregation | Decide replace-versus-accumulate policy before student release. |
| Tie breaking | Confirm deterministic team-ID ordering or choose an explicit secondary metric. |

LBNL studies show that data-center demand flexibility and large-load tariffs
are real planning considerations, but they do not establish one universal
`$/MWh` IT-service value. See [LBNL data-center demand-response field study](https://eta.lbl.gov/publications/demand-response-opportunities-and) and [LBNL large-load rate-design brief](https://datacenters.lbl.gov/publications/electricity-rate-designs-large-loads).

## Release gate

Before publishing the student rules:

1. decide whether the teaching release should use this DAM vector or a
   multi-day historical RTM/DAM aggregate;
2. select and document candidate generator technologies;
3. review the annualized combustion-turbine anchor and horizon weighting;
4. review the `$20,000,000/MW` connection-capacity rule and storage coefficients;
5. generate and validate the complete baseline ACTIVSg2000 LMP artifact;
6. run a parameter sweep over IT value, price, generator cost, CAPEX, storage
   cost, site cost,
   and load limits;
6. accept only a configuration with meaningful feasible/infeasible separation
   and no single cost coefficient dominating all feasible scores.
