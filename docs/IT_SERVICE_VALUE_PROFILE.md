# IT-service value profile

**Status:** Current first-release policy for the two half-year rule files.

The evaluator assigns a value to served computational load. This is an
instructor-chosen **opportunity value of IT service**, not an electricity
market price and not a claim about a cloud provider's production economics.

## Current release

Both released half-year profiles use the same transparent full-horizon policy:

```text
IT_value[t] = 125.00 USD/MWh,  for every hour t
```

| Rule file | Horizon | IT-value entries |
|---|---:|---:|
| `dispatch_scoring_rules_jan_jun_v1.json` | 4,368 hours | 4,368 values |
| `dispatch_scoring_rules_jul_dec_v1.json` | 4,416 hours | 4,416 values |

The vector is intentionally constant rather than a six-hour pattern. A
constant vector is easier for students to audit, avoids unexplained periodicity,
and makes strategic time variation come from the network operating state, load,
renewable availability, and the eventual hourly energy-price surface.

## Provenance and reproducibility

- **Source category:** `INSTRUCTOR-CHOSEN`.
- **Profile version:** `it-service-value-v2-constant-hourly`.
- **Units:** USD per MWh of served computational load.
- **Generation:** `tools/activsg2000/build_economic_profiles.py`.
- **Determinism:** given the horizon and base value, regeneration produces the
  same vector.

The profile is not derived from the ACTIVSg2000 generator-cost table. Existing
generator operating costs remain dataset-specified, while the IT-service value
is a separate scoring assumption.

## Future extensions

Any replacement must provide one value for every hour, document its source or
instructor rationale, record units/version/hash, pass positivity and
deterministic-regeneration tests, and be announced before student submission.
No future profile should silently restore a repeated six-value template.
