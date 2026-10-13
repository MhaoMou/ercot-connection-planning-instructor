# Data-center computational-service value calibration

**Status:** Active calibration note for instructor review. The versioned
half-year rules files remain authoritative for production scoring.

**Current approved release economics:** The active half-year rules and hourly
artifacts use `$6500/MWh` for AI-intensive IT service value, `$1.5m/MW` for
team-generator capital, `$25m/MW` for the AI-data-center facility/site proxy,
an `$80/$150/$250` per MWh team-generator curve at 25/50/25%, `$400,000/MW`
plus `$250,000/MWh` for storage capital, and no storage operating-cost charge.
The versioned files under
`data/teaching/dispatch_scoring_rules_*_v1.json` and the processed hourly
artifacts are authoritative when they differ from this calibration note.

## 1. Modeling interpretation

The student teams represent data-center operators that sell computational
services. The score should therefore reward the value of served computational
load, but the coefficient should not be interpreted as a cloud provider's
published retail revenue or profit.

The recommended quantity is:

```text
IT service contribution value ($/MWh of facility load)
```

It is an instructional marginal contribution value: the value of keeping one
additional MWh of facility load available for computation after abstracting
away customer pricing, staffing, software, and corporate overhead. It is not a
market electricity price.

## 2. What public evidence can and cannot identify

Public cloud providers publish machine prices, but generally do not publish the
incremental contribution margin associated with one additional MWh of data-
center facility electricity. Therefore, no single official Google, Amazon, or
OpenAI `$ / MWh` value exists for direct adoption.

Public evidence is still useful for bounding the coefficient:

1. Google Cloud's current price sheet lists an `a3-highgpu-8g` machine with
   eight H100 GPUs at approximately `$88.49/hour` on demand in the displayed
   region. The same page notes that the price includes the accelerator-
   optimized machine configuration, not only the GPU silicon.
2. NVIDIA lists H100 SXM thermal design power up to `700 W` per GPU. Eight GPUs
   therefore have a GPU-board upper-bound draw of `5.6 kW`, before CPUs,
   memory, networking, storage, power conversion, and cooling.
3. The U.S. Department of Energy reports an average data-center PUE of about
   `1.6`, with `1.4` characterized as good and `1.1` as better in its design
   guide. PUE converts IT energy into total facility energy.

Using a rough 10–15 kW facility-power envelope for an eight-GPU machine gives
an illustrative gross service-price equivalent of roughly `$5,900–$8,900/MWh`
from the Google list price. This is an upper-bound retail-revenue comparison,
not a recommended game coefficient. Applying a conservative 10–25% contribution
fraction gives approximately `$600–$2,200/MWh` of facility-load value.

The range is deliberately broad because workload mix, utilization, contract
discounts, hardware amortization, and service-level obligations are not public
at the team level.

Sources:

- [Google Cloud GPU pricing](https://cloud.google.com/products/compute/pricing/accelerator-optimized)
- [NVIDIA H100 specifications](https://www.nvidia.com/en-us/data-center/h100/)
- [DOE Best Practices Guide for Energy-Efficient Data Center Design](https://www.energy.gov/sites/default/files/2024-07/best-practice-guide-data-center-design.pdf)
- [LBNL 2024 U.S. Data Center Energy Usage Report](https://escholarship.org/uc/item/32d6m0d1)

## 3. Historical instructional IT-value bands (superseded)

The bands in this section are historical research alternatives. They are
superseded for the active release; use the approved `$6500/MWh` IT value stated
above and in the versioned half-year rules.

Use the following as scenario bands, not claims about the financial performance
of a specific company:

| Workload class | Suggested value ($/MWh facility load) | Interpretation |
|---|---:|---|
| General cloud / CPU services | 500–800 | Commodity or highly competitive service with moderate contribution margin |
| Batch analytics / model training | 800–1,500 | Delay-tolerant but valuable computational work |
| Premium inference / latency-sensitive service | 1,500–3,000 | High service value, but capacity and QoS obligations are stronger |

The historical first-classroom recommendation was a single common base value
of `$1,000/MWh` for all teams. That recommendation is superseded by the
approved `$6500/MWh` release value. If the course later wants heterogeneous
business strategies, expose workload class as an explicit scenario choice
rather than implicitly assigning teams to Google, Amazon, or OpenAI.

For the released hourly evaluator, a short six-level value profile can be
repeated across the selected half-year. The entries are profile levels, not
six operating periods and not a replacement for the 4,344/4,368/4,416-hour
submission horizon. For historical provenance, the note records this profile:

The following profile is historical and superseded; it is not an active release
vector.

```json
[1100, 950, 800, 800, 1050, 950]
```

The evaluator repeats this six-level template across the selected hourly
calendar, preserving the one-hour time step. It represents higher-value
evening/peak service periods without creating an order-of-magnitude difference
between hours. A simpler constant profile of
`[1000, 1000, 1000, 1000, 1000, 1000]` is preferable for the first student
pilot because it isolates network-location and generator decisions.

## 4. Candidate-generator technology and calibration

The marginal-cost and capital anchors in this section are source-informed
instructional inputs. The active release uses the approved `$1.5m/MW` generator-capital
coefficient and the `$80/$150/$250` per MWh team-generator curve at 25/50/25%.

Colocated data centers use a mixture of conventional diesel standby generation,
natural-gas reciprocating engines, and simple-cycle gas turbines. Large AI
campuses increasingly announce gas-fired prime or bridge power, while diesel
remains common for emergency backup. The game therefore represents a generic
dispatchable colocated fleet rather than a single named manufacturer.

For reference, EIA's recent technology reports identify simple-cycle turbine
and internal-combustion configurations, and EIA's AEO2026 framework continues
to treat combustion turbines as a capacity-resource technology. The exact
student curve additionally includes onsite fuel logistics, standby/bridge
operation, emissions controls, maintenance, and scarcity premiums.

```text
fuel cost + variable O&M = technology-dependent short-run cost
```

For the instructional candidate generator, use:

| Parameter | Recommended pilot value |
|---|---:|
| Candidate-generator marginal-cost curve | `$80`, `$150`, `$250/MWh` at 25/50/25% capacity segments |
| Candidate technology context | Natural-gas reciprocating engine/simple-cycle turbine with diesel-standby or scarcity premium |
| Curve status | Source-informed instructional abstraction, not a quoted tariff |

The approved current coefficient is `$1.5m/MW`, kept separate
from marginal dispatch cost.

For historical reproducibility, the retired pilot note used a `$1.3m/MW`
generator-capital placeholder and a `$85,000/MW-year` annualized research
coefficient. Those values are retained here only as superseded historical
context; neither is used by the active half-year rules.

Sources:

- [EIA AEO2026 electricity-generation costs](https://www.eia.gov/outlooks/aeo/electricity_generation/)
- [EIA AEO2025 technology-cost report](https://www.eia.gov/analysis/studies/powerplants/capitalcost/pdf/capital_cost_AEO2025.pdf)
- [EIA 2024 generator construction costs](https://www.eia.gov/electricity/generatorcosts/)
- [California Energy Commission data-center overview](https://www.energy.ca.gov/programs-and-topics/topics/data-centers)
- [Burns Engineering colocated-power comparison](https://www.burns-group.com/taking-the-reins-are-on-site-power-solutions-a-cost-effective-choice-for-todays-data-centers/)

## 5. Why the scoring value should exceed wholesale electricity price

The value term represents computational service, while `energy_price_per_mwh`
represents the opportunity cost of electricity. They are economically different
quantities. A data center can rationally pay more than the wholesale energy
price to serve a valuable workload, while still choosing locations and local
generation based on congestion and operating costs.

The production evaluator uses the hourly, bus-specific ACTIVSg2000 baseline
DCOPF LMP artifact rather than a six-value ERCOT price vector. The historical
base IT-service values are superseded; the approved release value is
`$6500/MWh`, and the artifact records
the exact hourly values and provenance.

The former `$125/MWh` pilot value is retained only as historical context and
is superseded by the active AI-intensive value.

If the instructor wants a future, separately approved exercise to represent
low-margin commodity cloud services, a `$500/MWh` sensitivity case could be
documented explicitly. Values near the historical `$100/MWh` anchor are
difficult to defend as data-center service value because they are too close to
wholesale electricity prices and do not reflect the commercial value of
computation.

## 6. Historical calibration experiment (not current release)

Before freezing the rules:

1. Compare constant IT-value cases at `$500`, `$1,000`, and `$2,000/MWh`.
2. Compare candidate-generator marginal costs at `$35`, `$45`, and `$60/MWh`.
3. Use actual student pilot CSVs plus the deterministic fixtures.
4. Require at least one case where local generation is dispatched and one case
   where it is not.
5. Check that network location, site count, generator capacity, and load volume
   can each affect ranking in at least one scenario.
6. Keep the final value vector and generator assumptions in the versioned JSON
   rules file with source URLs and retrieval dates.

The superseded research candidate was therefore:

```json
{
    "it_value_per_mwh": [6500, 6500, 6500, "... full horizon ..."],
    "default_generator_cost_per_mwh": 150
}
```

This was a historical instructional calibration, not the approved production
configuration. The approved release economics are listed at the top of this
note and in the versioned half-year rules files.
