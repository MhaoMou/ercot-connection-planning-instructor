# Multi-team instructor example

This directory is a compact, reproducible **historical compatibility example**
of one four-team round. Its checked-in submissions use the retired long-form
hourly schema and matching historical results; they do not define the current
student submission format. Current submissions use one canonical row per site:

```text
team_id,site_id,bus_id,connection_capacity_mw,generator_capacity_mw,storage_power_mw,storage_energy_mwh
team-blue,site-north,3002041,40,20,10,40
```

The active CSV has no hourly horizon rows. Use the student/instructor manuals
for the current contract.

## Included artifacts

- `submissions/`: four retained historical long-form team CSV files;
- `rules/`: the exact scoring-rule file used for the run;
- `results/leaderboard.csv`: ranks, feasibility, scores, and cost/value fields;
- `results/submission_manifest.csv`: accepted-input hashes;
- `results/evaluation_summary.json`: compact run metadata and team scores;
- `results/diagnostic_team_01.json`: one representative team diagnostic;
- `results/dispatch_period_0.json`: one representative dispatch record;
- `results/evaluation_sha256.txt`: hash of the full remote evaluation record.

The full raw `evaluation.json` is intentionally not checked into the
repository because it is approximately 1.4 GB. The hash file records the
identity of that instructor-side artifact.

## Reproduce the run

From the repository root, use the current evaluator and the copied inputs:

```bash
PYTHONPATH=.:src python3 -m ercot_game.batch_cli run \
  --submissions-dir docs/instructor_manual/examples/multi_team_round/submissions \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2028/jan_jun \
  --rules docs/instructor_manual/examples/multi_team_round/rules/dispatch_scoring_rules_jan_jun_v1.json \
  --year 2028 --round jan_jun \
  --workers 8 --projection-iterations 10 \
  --output-dir work/instructor_manual_example/reproduced_results
```

This example is for instructor training and documentation. It is not a set of
current student submissions and should not be used to infer private strategies
from other teams.
