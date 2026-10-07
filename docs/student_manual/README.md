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
blocks for CSV examples and technical identifiers. The instructor should
update the round-specific hourly price/value vectors and storage-cost
coefficients before distributing a final release.

Students can check a submission before uploading it with:

```bash
PYTHONPATH=src python scripts/check_submission.py \
  --file team-blue.csv \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2017/jan_jun \
  --year 2017 --round jan_jun
```

Use `--year 2017 --round jul_dec` for the July--December half-year. The
The released calendar determines whether January--June has 4,368 hours (2016)
or 4,344 hours (2017/2018); July--December has 4,416 hours. The checker
validates the canonical `bus_id` schema against the released ACTIVSg2000 bus and
generator catalogs; the instructor-side evaluator remains authoritative.
