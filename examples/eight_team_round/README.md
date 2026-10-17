# Eight-team example round

A deterministic instructor demonstration of the current site-level submission
contract on the six-hour game package. It is not an official student result
and does not reveal any student's strategy.

## Scenario

- Scenario block: `2028/jan_jun` of `data/processed/activsg2000_scenarios_v2_6h`
- Horizon: 728 six-hour periods (4,368 source hours), daily-cycle storage
- Teams: 8, with 2 sites each; total requested and connected capacity 705 MW
- Private generation: 10--40 MW per team; four-hour storage on one site per team
- Submission number: 2, so energy purchases settle against LMPs from the joint dispatch

Two teams deliberately request bus `7049` to demonstrate joint competition. The
complete joint plan was feasible, so the fair projection was not activated and
every team received its full requested connection capacity.

## Check the CSV files

From the repository root:

```bash
for file in examples/eight_team_round/submissions/*.csv; do
  PYTHONPATH=src python3 scripts/check_submission.py --file "$file" \
    --artifact-dir data/processed/activsg2000_scenarios_v2_6h/2028/jan_jun
done
```

## Re-run the evaluation

```bash
ercot-game-batch run \
  --submissions-dir examples/eight_team_round/submissions \
  --year 2028 --round jan_jun --time-step-hours 6 \
  --submission-number 2 --workers 4 --output-mode full \
  --output-dir work/eight_team_round_results
```

`--year`/`--round` select the six-hour package block and the
`dispatch_scoring_rules_jan_jun_6h_v1.json` rules. Add `--solver-backend gurobi`
when Gurobi is available.

## Recorded result

[`results.json`](results.json) is the compact result of the run above on
Electrify on 2026-10-17 with Gurobi: 45 seconds, about 2.6 GiB peak memory,
all eight files accepted, joint dispatch feasible, no projection. Scores range
from $160M (team-08) to $378M (team-01). The run ID is recorded in the JSON.

This is a standalone Submission 2 run with no asset ledger, so every asset is
charged capital; in the classroom flow, assets built in Submission 1 would not
be charged again. Use the example to show the score decomposition, not as a
recommended calibration or a class leaderboard.

[`grid_map.svg`](grid_map.svg) is the classroom grid map for this run
(`ercot-game-grid-map --time-step-hours 6`): buses colored by the total
connection capacity of all teams, lines colored by peak loading over the 728
periods. Open it in a browser to hover, zoom, and pan.

The raw `evaluation.json` and per-period dispatch files are not checked in
because they are large.
