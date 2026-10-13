# Eight-Team Reproducible Example Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic eight-team instructor example that reproduces the current CSV format and records compact full-horizon evaluation results without committing large raw solver artifacts.

**Architecture:** A small standard-library Python generator will create eight site-level CSV files from the released 2028 January--June bus catalog. A compact JSON report will record the verified Electrify result and scoring components. The instructor manual will link the fixture, generation command, evaluation command, and interpretation; tests will validate the fixture schema and report totals.

**Tech Stack:** Python standard library, pytest, Markdown, LaTeX.

---

### Task 1: Add the deterministic submission fixture generator

**Files:**
- Create: `scripts/simulate_eight_team_round.py`
- Create: `docs/instructor_manual/examples/eight_team_round/submissions/team-01.csv` through `team-08.csv`
- Test: `tests/test_eight_team_round_example.py`

- [x] Write tests that verify the eight files exist, use exactly the seven canonical columns, contain two sites per team, use buses from the released catalog, and satisfy generator/storage capacity constraints.
- [x] Run the focused test and confirm it fails because the fixture does not exist.
- [x] Implement the generator with fixed plans and a bus-catalog check; write files with deterministic ordering and UTF-8 CSV output.
- [x] Run the focused test and confirm it passes.

### Task 2: Record the compact verified evaluation

**Files:**
- Create: `docs/instructor_manual/examples/eight_team_round/results.json`
- Test: `tests/test_eight_team_round_example.py`

- [x] Add the verified 4,368-hour Electrify result: eight accepted teams, joint feasible dispatch, full connection for every request, ranks, scores, score components, runtime, peak memory, solver settings, and run identifier.
- [x] Test that the report has eight unique teams, ranks 1--8, zero rejected teams, and aggregate totals equal the per-team rows.
- [x] Keep raw `evaluation.json`, dispatch-period JSON files, and logs out of the repository.

### Task 3: Update instructor documentation

**Files:**
- Create: `docs/instructor_manual/examples/eight_team_round/README.md`
- Modify: `docs/instructor_manual/main.tex`
- Modify: `docs/instructor_manual/README.md`

- [x] Document the fixture purpose, exact generator command, current batch-evaluation command, full-horizon result, scoring interpretation, and privacy/reproducibility boundary.
- [x] Add a concise table of team capacities, assets, connected amounts, and scores to the LaTeX manual.
- [x] Explain that the negative scores are caused by the current site-capital-cost calibration and that the example is synthetic, not an official class result.

### Task 4: Verify and report

- [x] Run the focused fixture tests.
- [x] Run the existing CSV and instructor-manual tests that do not require unavailable optional solver dependencies.
- [x] Compile the instructor manual with the repository LaTeX workflow if available.
- [x] Run `git diff --check` and review that no generated PDF, auxiliary file, raw evaluation artifact, or unrelated user change is staged.
