# Lecture data

All numbers in the slides come from the files in `raw/`, produced on Electrify
on 2026-10-17 from the six-hour game package
(`data/processed/activsg2000_scenarios_v2_6h`, 2028 July-December unless noted),
and are turned into plot tables and `numbers.tex` by
`scripts/make_lecture_data.py` (run from `docs/guest_lecture`).

| Raw file | Command |
| --- | --- |
| `raw/hc_allbuses_peakday/` | `ercot-game-hosting-capacity --artifact-dir .../2028/jul_dec --horizon 736 --start-period 304 --period-count 4 --solver-backend gurobi --workers 4 --write-long-form` (all 2,000 buses, peak-load day; 134 s) |
| `raw/hc_selected_allperiods/` | same, `--start-period 0 --period-count 736 --bus-id 1001 1002 1007 1010 7049 7346 --workers 8` (133 s) |
| `raw/hc_variable_allperiods/` | same, buses 7087 7077 7127 5253 7419 5370 (126 s) |
| `raw/pair_region_p306.json` | `scripts/pair_region.py 306`: two-bus region for buses 1002/1010 at the peak period and the two-team example (HiGHS via `solve_prepared_period`) |
| `raw/eight_team_6h_summary.json` | `ercot-game-batch run --submissions-dir docs/instructor_manual/examples/eight_team_round/submissions --year 2028 --round jan_jun --time-step-hours 6 --submission-number 2 --workers 4 --output-mode full --solver-backend gurobi` (45 s), summarized |

The peak-load day is day 76 (periods 304-307); period 306 has the highest
system load (77,510 MW). The storage curve uses the game rules: six-hour
periods, B = 4P, each day starts and ends at B/2, discharge only serves the
site's own load.
