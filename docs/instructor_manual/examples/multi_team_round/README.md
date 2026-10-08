# Multi-team instructor example

This directory is a compact, reproducible example of one four-team round.
It uses the current 2,000-bus ACTIVSg2000 release for **2016 January--June**
(`4368` one-hour periods) and the `dispatch-scoring-jan_jun-v1` rules.

## Included artifacts

- `submissions/`: four complete long-form team CSV files;
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
  --artifact-dir data/processed/activsg2000_scenarios_v1/2016/jan_jun \
  --rules docs/instructor_manual/examples/multi_team_round/rules/dispatch_scoring_rules_jan_jun_v1.json \
  --year 2016 --round jan_jun \
  --workers 8 --projection-iterations 10 \
  --output-dir work/instructor_manual_example/reproduced_results
```

This example is for instructor training and documentation. It is not a set of
current student submissions and should not be used to infer private strategies
from other teams.
