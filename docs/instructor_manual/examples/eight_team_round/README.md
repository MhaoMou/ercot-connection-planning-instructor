# Eight-team reproducible round

This directory is a deterministic instructor demonstration of the current
site-level submission contract. It is not an official student result and does
not reveal any student's strategy.

## Scenario

- Scenario block: `2028/jan_jun`
- Horizon: 4,368 one-hour periods
- Teams: 8
- Sites: 2 per team
- Total requested and connected capacity: 705 MW
- Private generation: 10--40 MW per team, depending on the plan
- Private storage: four-hour storage on one site per team
- Submission number: 2, so energy purchases settle against LMPs from the joint dispatch

The example deliberately has two teams request site bus `7049` to demonstrate
joint competition. The evaluator found the complete joint plan feasible, so no
fair continuous projection was activated and every team received its full
requested connection capacity.

## Recreate the CSV files

From the repository root:

```bash
python3 scripts/simulate_eight_team_round.py
for file in docs/instructor_manual/examples/eight_team_round/submissions/*.csv; do
  PYTHONPATH=src python3 scripts/check_submission.py --file "$file"
done
```

The generator reads the released bus catalog before writing files, uses the
canonical seven-column header, and enforces the four-hour storage relationship
`storage_energy_mwh = 4 * storage_power_mw`.

## Re-run the instructor evaluation

The full run was executed on Electrify with HiGHS, four parallel hourly
workers, and the current multi-storage Benders evaluator:

```bash
PYTHONPATH=src python3 -m ercot_game.batch_cli run \
  --submissions-dir docs/instructor_manual/examples/eight_team_round/submissions \
  --artifact-dir data/processed/activsg2000_scenarios_v1/2028/jan_jun \
  --rules data/teaching/dispatch_scoring_rules_jan_jun_v1.json \
  --horizon 4368 \
  --workers 4 \
  --submission-number 2 \
  --output-dir work/eight_team_round_results
```

For a local run, use an environment with the optional HiGHS dependency and
expect substantially different timing from the recorded Electrify result.

## Recorded result

The compact checked-in result is [`results.json`](results.json). The verified
remote run completed in 1,216.75 seconds (20.3 minutes), used approximately
9.65 GiB peak resident memory, accepted all eight files, and returned a
feasible joint dispatch. The run ID is recorded in the JSON for provenance.

All scores are negative in this demonstration because the active connection
site coefficient is $20 million per MW. The example is useful for showing the
full score decomposition, but it should not be presented as a recommended
calibration or as a class leaderboard.

The raw remote `evaluation.json`, period dispatch files, and logs are not
checked into Git because they are very large. Keep those artifacts in the
instructor's private run directory when a complete audit trail is required.
