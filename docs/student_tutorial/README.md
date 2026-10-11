# AI-Assisted Student Tutorial

This directory contains a student-facing companion to the ACTIVSg2000
connection-planning assignment. It teaches dataset inspection, visualization,
DCOPF concepts, CSV preparation, storage, preview workflows, strategy
experiments, debugging, and reproducibility through reviewed AI examples and
executable notebooks.

The existing assignment handout remains authoritative for rules, data
schemas, horizons, scoring, storage semantics, and deliverables. The tutorial
is explanatory: AI answers are representative, must be checked against the
repository sources, and are not official evaluator results.

## Using the tutorial

The prompt, transcript, and notebook directories contain the reviewed tutorial
artifacts. Open the notebooks locally or upload them to Google Colab.
When using Colab, clone or otherwise provide the repository before running
cells so paths and versioned data resolve consistently. The local tutorial
dependencies can be installed with:

```bash
python3 -m pip install -e '.[tutorial]'
```

From the repository root, execute the examples locally with:

```bash
PYTHONPATH=src python3 docs/student_tutorial/scripts/execute_notebooks.py \
  --input-dir docs/student_tutorial/notebooks \
  --output-dir docs/student_tutorial/outputs/notebook_runs
```

That runner executes the notebooks with the repository package on the
Python path and writes executed notebooks to the indicated output directory.

The validator checks the executed notebooks and published artifacts:

```bash
PYTHONPATH=src python3 docs/student_tutorial/scripts/validate_tutorial_examples.py \
  --run-dir docs/student_tutorial/outputs/notebook_runs
```

Run the tutorial regression tests with:

```bash
pytest -q tests/test_student_tutorial_artifacts.py tests/test_student_tutorial_notebooks.py
```

Prompt files and reviewed transcripts are paired through `prompt_registry.json`.

## Verification and privacy

Treat every AI response as a starting point. Verify file names, columns, units,
bus IDs, solver status, numerical outputs, and provenance using the linked
repository files or executable examples. The student preview is conservative
and non-authoritative; it is not an official score.

Do not upload private team plans, credentials, rankings, instructor-only
diagnostics, or other confidential information to shared AI tools. Use public
data, synthetic examples, or instructor-approved fixtures only.
