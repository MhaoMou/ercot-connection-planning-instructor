# Data-center computational-service value calibration

**Status:** Research note for instructor review; no production scoring values are changed by this document.

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

## 3. Recommended instructional IT-value bands

Use the following as scenario bands, not claims about the financial performance
of a specific company:

| Workload class | Suggested value ($/MWh facility load) | Interpretation |
|---|---:|---|
| General cloud / CPU services | 500–800 | Commodity or highly competitive service with moderate contribution margin |
| Batch analytics / model training | 800–1,500 | Delay-tolerant but valuable computational work |
| Premium inference / latency-sensitive service | 1,500–3,000 | High service value, but capacity and QoS obligations are stronger |

For the first classroom release, I recommend a single common base value of
`$1,000/MWh` for all teams. This avoids giving teams different economic rules
based on brand labels. If the course later wants heterogeneous business
strategies, expose workload class as an explicit scenario choice rather than
implicitly assigning teams to Google, Amazon, or OpenAI.

For the released hourly evaluator, a short six-level value profile can be
repeated across the selected half-year. The entries are profile levels, not
six operating periods and not a replacement for the 4,344/4,368/4,416-hour
submission horizon. A defensible starting profile is:

```json
[1100, 950, 800, 800, 1050, 950]
```

The evaluator repeats this six-level template across the selected hourly
calendar, preserving the one-hour time step. It represents higher-value
evening/peak service periods without creating an order-of-magnitude difference
between hours. A simpler constant profile of
`[1000, 1000, 1000, 1000, 1000, 1000]` is preferable for the first student
pilot because it isolates network-location and generator decisions.

## 4. Candidate-generator marginal cost

The current `$100/MWh` placeholder is not a good base case for a natural-gas
candidate generator. It caused the pilot generators to receive zero dispatch
because the grid could supply the load more cheaply.

For a simple-cycle natural-gas turbine, EIA reports an average heat rate close
to `9,920 Btu/kWh` in its technology assumptions. Combining this with a
Henry-Hub natural-gas price of `$3.52/MMBtu` in 2025 gives:

```text
fuel cost = 9.920 MMBtu/MWh × $3.52/MMBtu = $34.9/MWh
```

Adding approximately `$3.5–$7/MWh` of variable O&M produces a reasonable
short-run marginal-cost band of about `$38–$45/MWh` before startup, emissions,
transport, and scarcity premiums. For an instructional candidate generator,
use:

| Parameter | Recommended pilot value |
|---|---:|
| Candidate-generator marginal cost | `$45/MWh` |
| Sensitivity band | `$35`, `$45`, `$60/MWh` |
| Candidate technology | Simple-cycle natural-gas turbine |

The existing `$85,000/MW-year` capital coefficient can remain a separate
investment deduction. It should not be folded into the marginal dispatch cost.

Sources:

- [EIA natural-gas price for electric power](https://www.eia.gov/todayinenergy/detail.php?id=67106)
- [EIA natural-gas electric-power price FAQ](https://www.eia.gov/tools/FAQs/faq.php?id=51&t=8)
- [EIA natural-gas plant heat-rate comparison](https://www.eia.gov/todayinenergy/detail.php?id=52158)
- [EIA distributed-generation cost assumptions](https://www.eia.gov/analysis/studies/buildings/distrigen/pdf/dg_chp.pdf)

## 5. Why the scoring value should exceed wholesale electricity price

The value term represents computational service, while `energy_price_per_mwh`
represents the opportunity cost of electricity. They are economically different
quantities. A data center can rationally pay more than the wholesale energy
price to serve a valuable workload, while still choosing locations and local
generation based on congestion and operating costs.

The production evaluator uses the hourly, bus-specific ACTIVSg2000 baseline
DCOPF LMP artifact rather than a six-value ERCOT price vector. A base IT-service
value of `$125/MWh` is currently used as a transparent instructor-chosen
opportunity value; the artifact records the exact hourly values and provenance.

If the instructor wants the exercise to represent low-margin commodity cloud
services instead, use `$500/MWh` and document that choice explicitly. Values
near `$100/MWh` are difficult to defend as data-center service value because
they are too close to wholesale electricity prices and do not reflect the
commercial value of computation.

## 6. Recommended calibration experiment

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

The preferred first-release candidate is therefore:

```json
{
  "it_value_per_mwh": [1000, 1000, 1000, 1000, 1000, 1000],
  "default_generator_cost_per_mwh": 45
}
```

This is a recommended instructional calibration, not yet an approved change to
the production rules.
