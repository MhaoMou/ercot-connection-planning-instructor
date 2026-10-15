# Google Colab: exact instructor-evaluator timing test

This folder is packaged from the current working tree to measure the same
eight-team instructor evaluator used for the reproducible round example. It
contains the current evaluator source, the `2028/jan_jun` 4,368-hour scenario
block, the active January--June scoring rules, and eight example submissions.

## What to upload

Upload these two files to Google Colab:

1. `ercot_colab_instructor_evaluator.ipynb`
2. `ercot_colab_instructor_evaluator.zip`

The notebook asks you to upload the zip, installs HiGHS and the small runtime
dependencies, runs a 24-hour smoke test, and then runs the full 4,368-hour
batch evaluation. The default solver request is `auto`: Colab will normally
select HiGHS because it does not have the instructor's Gurobi license.

## What is measured

The full command is the same production command used by the instructor:

```text
python run_colab_evaluator.py \
  --horizon 4368 \
  --workers 4 \
  --solver-backend auto
```

The script reports:

- wall-clock time measured on the Colab runtime;
- requested and resolved solver backend;
- whether all eight submissions were accepted;
- joint feasibility and projection status;
- team scores;
- output size and the location of the compact result.

The benchmark uses `submission_number=2`, so it includes the LMP-based energy
settlement behavior of the second submission. It does not change evaluator
semantics or substitute the price-taking screening approximation.

## Important interpretation

The result is hardware- and runtime-dependent. Record the Colab runtime type,
Python version, `highspy` version, and reported wall time. Do not compare a
Colab HiGHS run with an Electrify Gurobi run as if they were the same solver.
The notebook prints the resolved backend so the comparison is explicit.
