# Student Manual

This directory contains the LaTeX source for the competitive data-center
connection-planning assignment manual.

For a compact inventory of the current game, scoring, storage, and
instructor-side evaluator settings, see
[`HYPERPARAMETERS.md`](HYPERPARAMETERS.md).

```text
main.tex       # document source
references.bib # cited sources
../scripts/check_submission.py  # student-facing CSV format checker
```

The manual uses Times-compatible text/math fonts and GitHub-style `listings`
blocks for CSV examples and technical identifiers. The checked-in canonical
profiles and rules are stored under
`data/processed/activsg2000_scenarios_v2_6h/` and
`data/teaching/dispatch_scoring_rules_{jan_jun,jul_dec}_6h_v1.json`. The game
uses six-hour periods (each the mean of six hourly source records; 724, 728, or
736 periods per half-year). Students should use those
repository-controlled inputs as provided. Only instructors may change the
profiles or rules in the repository, and any instructor-controlled change must
go through the normal release-validation process.

The current submission is a canonical site-level CSV: exactly one row per site
and no horizon rows. Students can check a submission before uploading it
with only the file path:

```bash
PYTHONPATH=src python scripts/check_submission.py \
  --file team-blue.csv
```

The optional `--artifact-dir` checks `bus_id` values against a released catalog;
optional `--year`, `--round`, or `--horizon` values add historical report
context. The released calendar still determines the evaluator's six-hour
scenario length, but students do not encode those periods as CSV rows. The
instructor-side evaluator remains authoritative.

The retired ten-column hourly format is retained only for clearly marked
historical fixtures and compatibility tests. Do not use it for a current
submission.
