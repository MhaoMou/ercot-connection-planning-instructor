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

Open the notebooks in `notebooks/` locally or upload them to Google Colab.
When using Colab, clone or otherwise provide the repository before running
cells so paths and versioned data resolve consistently. The local tutorial
dependencies can be installed with:

```bash
python -m pip install -e '.[tutorial]'
```

Prompt files and reviewed transcripts are paired through
`prompt_registry.json`. A later curation task will add those deferred files;
the registry establishes their stable paths now.

## Verification and privacy

Treat every AI response as a starting point. Verify file names, columns, units,
bus IDs, solver status, numerical outputs, and provenance using the linked
repository files or executable examples. The student preview is conservative
and non-authoritative; it is not an official score.

Do not upload private team plans, credentials, rankings, instructor-only
diagnostics, or other confidential information to shared AI tools. Use public
data, synthetic examples, or instructor-approved fixtures only.
