# AI-Assisted Student Tutorial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Build a reproducible student-facing tutorial PDF and computational companion that teaches the ACTIVSg2000 connection-planning assignment through verified AI prompt/response examples and executable Python notebooks.

**Architecture:** Keep the existing assignment handout authoritative. Add an independent `docs/student_tutorial/` project containing frozen prompt/transcript artifacts, executable notebooks, deterministic validation scripts, and a Times-based LaTeX PDF. In-session subagents generate and fact-check transcripts during curation; ordinary builds consume frozen transcripts and never call an AI service.

**Tech Stack:** Python 3.11+, the existing `ercot-game` package, Jupyter notebooks, `nbclient`, `pytest`, LaTeX with `newtxtext/newtxmath`, `tcolorbox`, `listings`, `tabularx`, `booktabs`, `tikz`, `graphicx`, `hyperref`, and `cleveref`.

---

## Scope and source boundaries

The implementation must use the current repository contract:

- ACTIVSg2000 is the public synthetic 2,000-bus model;
- the released scenario package is distinct from the raw public source;
- the canonical student submission has seven site-level CSV columns;
- submissions govern one-hour half-year scenarios and do not contain hourly rows;
- storage is submitted as power and energy capacity and dispatched by the evaluator;
- student-owned generation and storage serve only the submitting team;
- student preview output is non-authoritative;
- the existing student and instructor manuals are not rewritten as part of this project.

No task may add live model calls to the PDF build, expose private team plans, change evaluator mathematics, or introduce a second submission schema.

## Files to create

```text
docs/student_tutorial/
├── README.md
├── main.tex
├── references.bib
├── prompt_registry.json
├── figures/
│   ├── pipeline.tex
│   ├── data_relationships.tex
│   ├── student_vs_evaluator.tex
│   ├── three_bus_dcopf.tex
│   ├── csv_evaluation_flow.tex
│   ├── storage_soc.tex
│   └── strategy_experiment.tex
├── prompts/
│   ├── 01_assignment_overview.md
│   ├── 02_dataset_inspection.md
│   ├── 03_python_visualization.md
│   ├── 04_matlab_visualization.md
│   ├── 05_dcopf_concept.md
│   ├── 06_dcopf_formulation.md
│   ├── 07_three_bus_implementation.md
│   ├── 08_csv_format.md
│   ├── 09_storage_model.md
│   ├── 10_colab_preview.md
│   ├── 11_debugging.md
│   ├── 12_strategy_experiments.md
│   └── 13_reproducibility.md
├── transcripts/
│   ├── 01_assignment_overview.json
│   ├── 02_dataset_inspection.json
│   ├── 03_python_visualization.json
│   ├── 04_matlab_visualization.json
│   ├── 05_dcopf_concept.json
│   ├── 06_dcopf_formulation.json
│   ├── 07_three_bus_implementation.json
│   ├── 08_csv_format.json
│   ├── 09_storage_model.json
│   ├── 10_colab_preview.json
│   ├── 11_debugging.json
│   ├── 12_strategy_experiments.json
│   └── 13_reproducibility.json
├── notebooks/
│   ├── 01_dataset_inspection.ipynb
│   ├── 02_network_visualization.ipynb
│   ├── 03_three_bus_dcopf.ipynb
│   ├── 04_storage_soc.ipynb
│   ├── 05_csv_validation.ipynb
│   ├── 06_student_preview.ipynb
│   └── 07_strategy_ablation.ipynb
├── outputs/
│   ├── figures/
│   ├── tables/
│   └── notebook_runs/
└── scripts/
    ├── execute_notebooks.py
    ├── validate_prompt_registry.py
    ├── validate_transcripts.py
    ├── validate_tutorial_examples.py
    └── build_latex_inputs.py
```

## Task 1: Add tutorial project metadata and stable artifact contracts

**Files:**
- Create: `docs/student_tutorial/README.md`
- Create: `docs/student_tutorial/prompt_registry.json`
- Modify: `pyproject.toml`
- Test: `tests/test_student_tutorial_artifacts.py`

- [ ] **Step 1: Write the failing artifact-contract tests.**

Create tests that assert:

```python
def test_tutorial_registry_lists_all_required_prompt_ids():
    registry = json.loads(REGISTRY.read_text())
    assert [item["id"] for item in registry["prompts"]] == [
        "assignment-overview",
        "dataset-inspection",
        "python-visualization",
        "matlab-visualization",
        "dcopf-concept",
        "dcopf-formulation",
        "three-bus-implementation",
        "csv-format",
        "storage-model",
        "colab-preview",
        "debugging",
        "strategy-experiments",
        "reproducibility",
    ]

def test_registry_points_to_existing_prompt_and_transcript_files():
    registry = json.loads(REGISTRY.read_text())
    for item in registry["prompts"]:
        assert (TUTORIAL_ROOT / item["prompt_file"]).is_file()
        assert (TUTORIAL_ROOT / item["transcript_file"]).is_file()
```

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_student_tutorial_artifacts.py -q
```

Expected: FAIL because the tutorial files do not exist yet.

- [ ] **Step 2: Define the registry schema.**

Use JSON from the Python standard library. Each entry must contain:

```json
{
  "id": "dataset-inspection",
  "module": "ACTIVSg2000 data inspection",
  "prompt_file": "prompts/02_dataset_inspection.md",
  "transcript_file": "transcripts/02_dataset_inspection.json",
  "notebook_files": ["notebooks/01_dataset_inspection.ipynb"],
  "required_sources": ["README.md", "docs/ACTIVSG2000_SCENARIO_PACKAGE.md"],
  "verification_requirements": ["file shapes", "keys", "provenance"],
  "privacy_level": "public-or-instructor-approved-fixture"
}
```

- [ ] **Step 3: Add the student-facing README.**

Explain:

- what the PDF teaches;
- that the assignment handout remains authoritative;
- how to open the notebooks in Colab;
- how to run the examples locally;
- that AI answers are representative and must be verified;
- that the preview is not an official score;
- that students must not upload private team plans to shared tools.

- [ ] **Step 4: Add only required tutorial dependencies.**

Add a `tutorial` optional dependency group containing `nbclient`, `nbformat`,
and `jupyter`. Do not add `quarto`, `jupyter-book`, or `nbgrader` to the
required build because they are optional companion tools, not prerequisites for
the official PDF.

- [ ] **Step 5: Run the artifact tests.**

Expected: FAIL until the prompt and transcript files are created in Tasks 2
and 3; leave the test as the contract for those tasks.

## Task 2: Create the prompt library and subagent-generation procedure

**Files:**
- Create: `docs/student_tutorial/prompts/*.md`
- Create: `docs/student_tutorial/transcripts/*.json`
- Create: `docs/student_tutorial/scripts/validate_prompt_registry.py`
- Create: `docs/student_tutorial/scripts/validate_transcripts.py`
- Test: `tests/test_student_tutorial_artifacts.py`

- [ ] **Step 1: Write prompt files with explicit constraints.**

Each prompt must include these headings:

```markdown
# Prompt metadata

## Goal
## Context supplied to the AI
## Prompt
## Expected evidence
## Privacy constraints
```

Every prompt must instruct the AI to distinguish verified facts,
interpretations, and unknowns. Code prompts must require executable code,
clear file paths, units, and a small test before scaling to ACTIVSg2000.

- [ ] **Step 2: Run one in-session generation subagent per prompt.**

For each registry entry, dispatch a fresh subagent with:

```text
Act as a student-facing AI teaching assistant for the tutorial prompt below.
Use only the supplied repository files and listed sources. Do not invent data,
bus IDs, costs, evaluator behavior, or solver details. Separate verified facts,
interpretations, and unknowns. Return a technically precise answer with code
only when requested. Include a short verification checklist.
```

The main agent must provide the prompt file, relevant repository excerpts, and
the current source-contract files. Do not pass private evaluation results,
credentials, or unrelated chat history.

- [ ] **Step 3: Run a fact-checking subagent for each generated response.**

Use a separate fresh subagent with:

```text
Fact-check this candidate tutorial response against the supplied repository
files and sources. Identify every unsupported or stale claim, unit error,
obsolete six-period reference, incorrect CSV field, mathematical sign issue,
or privacy leak. Return corrected wording and a pass/fail decision. Do not
rewrite correct sections unnecessarily.
```

- [ ] **Step 4: Freeze reviewed transcripts.**

Each JSON transcript must have this shape:

```json
{
  "prompt_id": "dataset-inspection",
  "prompt": "...",
  "response": "...",
  "verification_checklist": ["..."],
  "agent_label": "in-session tutorial generation subagent",
  "generated_at": "YYYY-MM-DD",
  "review_status": "fact-checked",
  "review_notes": "...",
  "sources": ["..."],
  "private_information_present": false
}
```

The implementation must not call a model during PDF or notebook builds.

- [ ] **Step 5: Validate the registry and transcripts.**

`validate_prompt_registry.py` must reject missing IDs, duplicate IDs, missing
files, missing required headings, and notebook paths that do not exist.

`validate_transcripts.py` must reject missing response text, missing prompt
IDs, missing verification checklists, non-fact-checked transcripts, and
`private_information_present: true`.

Run:

```bash
.venv/bin/python docs/student_tutorial/scripts/validate_prompt_registry.py
.venv/bin/python docs/student_tutorial/scripts/validate_transcripts.py
```

Expected: both commands exit 0 and report 13 validated prompt/transcript
pairs.

## Task 3: Build the executable notebook examples

**Files:**
- Create: `docs/student_tutorial/notebooks/*.ipynb`
- Create: `docs/student_tutorial/scripts/execute_notebooks.py`
- Create: `docs/student_tutorial/scripts/validate_tutorial_examples.py`
- Test: `tests/test_student_tutorial_artifacts.py`

- [ ] **Step 1: Create notebook skeletons with stable cell contracts.**

Every notebook must begin with:

1. a Markdown objective cell;
2. a version/path configuration cell;
3. an environment-report cell;
4. a verification/assertion cell;
5. an output cell producing a named artifact.

The notebooks must work with repository-relative paths and expose a single
`TUTORIAL_ROOT` or `REPO_ROOT` configuration variable rather than hard-coded
user paths.

- [ ] **Step 2: Implement dataset inspection.**

The notebook must report file names, row/column counts, required columns,
duplicate keys, missing values, bus-reference validity, scenario horizon, and
manifest status. It must never claim a scenario is validated unless the
manifest says `validation_status: passed`.

- [ ] **Step 3: Implement network visualization.**

Use Matplotlib for deterministic output. Produce:

- a structural bus/branch plot;
- generator locations or bus overlays;
- a demand summary plot;
- a clear statement when geographic coordinates are unavailable.

Use a small deterministic fixture for CI and an instructor-approved
ACTIVSg2000 artifact for the release notebook.

- [ ] **Step 4: Implement three-bus DCOPF and storage examples.**

The three-bus notebook must print status, objective, generation, branch flows,
and balance values. The storage notebook must demonstrate state transitions,
nonnegative state of charge, discharge bounded by stored energy, and terminal
state of charge.

- [ ] **Step 5: Implement CSV and preview examples.**

Use the canonical seven-column CSV. Run the repository validator, then run the
student preview with an instructor-approved fixture. Print a warning that the
preview is conservative and non-authoritative.

- [ ] **Step 6: Implement strategy ablation.**

Generate controlled candidate plans varying one factor at a time. Record
candidate parameters, validation status, preview metrics, and plots. Do not
include cross-team private data or claims that an ablation is an optimal
strategy.

- [ ] **Step 7: Execute notebooks with `nbclient`.**

`execute_notebooks.py` must:

- execute notebooks in a clean working directory;
- set a finite cell timeout;
- fail on unexpected exceptions;
- write executed notebooks to `outputs/notebook_runs/`;
- write figures and tables to `outputs/`;
- print a compact execution summary.

Run:

```bash
.venv/bin/python docs/student_tutorial/scripts/execute_notebooks.py \
  --input-dir docs/student_tutorial/notebooks \
  --output-dir docs/student_tutorial/outputs/notebook_runs
```

- [ ] **Step 8: Validate generated examples.**

`validate_tutorial_examples.py` must check that required output files exist,
all executed notebooks have no error outputs, CSV examples use exactly seven
columns, figures are nonempty, and numerical examples contain finite values.

## Task 4: Add the LaTeX presentation layer

**Files:**
- Create: `docs/student_tutorial/main.tex`
- Create: `docs/student_tutorial/references.bib`
- Create: `docs/student_tutorial/scripts/build_latex_inputs.py`
- Test: `tests/test_student_tutorial_artifacts.py`

- [ ] **Step 1: Define the preamble.**

Use the current assignment style:

```latex
\usepackage[margin=1in]{geometry}
\usepackage{newtxtext,newtxmath}
\usepackage[T1]{fontenc}
\usepackage{microtype}
\usepackage{amsmath,mathtools}
\usepackage{booktabs,tabularx,longtable,array}
\usepackage[most]{tcolorbox}
\usepackage{listings}
\usepackage{enumitem}
\usepackage{graphicx}
\usepackage{tikz}
\usepackage{hyperref}
\usepackage{cleveref}
```

Do not make `minted` a required dependency.

- [ ] **Step 2: Define reusable environments.**

Create `promptbox`, `aibox`, `verifybox`, `checkpointbox`, `warningbox`, and
`outputbox` with distinct accessible titles, breakable content, and stable
colors. Use `listings` for Python, MATLAB, CSV, JSON, and shell blocks.

- [ ] **Step 3: Build the document structure.**

The PDF order must be:

1. how to use this tutorial;
2. AI-use and verification policy;
3. assignment orientation;
4. data and provenance;
5. dataset inspection;
6. visualization;
7. DCOPF;
8. storage;
9. CSV submission;
10. Colab/local preview;
11. strategy experiments;
12. debugging and reproducibility;
13. final checklist;
14. references and appendices.

- [ ] **Step 4: Import frozen prompt/response content.**

`build_latex_inputs.py` must read validated transcript JSON and generate
LaTeX-safe include files. It must escape `%`, `&`, `_`, `#`, `{`, `}`, and
backslashes when rendering plain response text, while routing code blocks to
`listings` inputs.

- [ ] **Step 5: Add figures and links.**

Include the seven conceptual diagrams and selected notebook-generated figures.
Link to:

- the student assignment handout;
- the repository guide where appropriate;
- the current CSV checker instructions;
- the Colab notebook entry point.

## Task 5: Add diagrams and references

**Files:**
- Create: `docs/student_tutorial/figures/*.tex`
- Modify: `docs/student_tutorial/references.bib`
- Test: `tests/test_student_tutorial_artifacts.py`

- [ ] **Step 1: Create current-terminology TikZ diagrams.**

All diagrams must use `Planning Model`, `ACTIVSg2000`, `student CSV`,
`DCOPF`, `storage power`, `storage energy`, and `student preview` correctly.
Do not use retired terms such as `teaching bus`, `path corridor`, or the old
six-period plan.

- [ ] **Step 2: Add verified references.**

Include entries for:

- Texas A&M ACTIVSg2000 materials;
- MATPOWER case format and manuals;
- Google Colab FAQ;
- `tcolorbox` and `minted` manuals;
- pandapower plotting;
- nbclient;
- Jupyter Book execution;
- Quarto Python computation;
- nbgrader.

- [ ] **Step 3: Add source notes under data-driven figures.**

Every figure generated from a dataset must state the source artifact, scenario
block, date/version, and whether it is a structural or geographic plot.

## Task 6: Add content and consistency tests

**Files:**
- Modify: `tests/test_student_tutorial_artifacts.py`
- Create: `tests/test_student_tutorial_content.py`

- [ ] **Step 1: Test current submission terminology.**

Scan tutorial prompts, transcripts, LaTeX, and notebook Markdown for the exact
seven-column header and reject obsolete hourly fields such as `period`,
`load_mw`, `submission_id`, `generator_id`, and `storage_id` when they appear
as current submission fields.

- [ ] **Step 2: Test current horizon terminology.**

Require explicit references to one-hour half-year scenarios and reject
unqualified six-period language. Permit historical terminology only inside an
explicitly labeled comparison or warning.

- [ ] **Step 3: Test transcript privacy and status.**

Reject transcripts that are not marked `fact-checked` or that contain
`private_information_present: true`.

- [ ] **Step 4: Test notebook and figure coverage.**

Require all seven notebooks, seven diagrams, 13 prompt files, and 13
transcripts to be registered and present.

- [ ] **Step 5: Run focused content tests.**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  tests/test_student_tutorial_artifacts.py \
  tests/test_student_tutorial_content.py -q
```

## Task 7: Build and visually inspect the PDF

**Files:**
- Modify: `docs/student_tutorial/main.tex`
- Modify: `docs/student_tutorial/scripts/build_latex_inputs.py`
- Create: `docs/student_tutorial/README.md` build instructions

- [ ] **Step 1: Compile with the repository-supported LaTeX compiler.**

Run the built-in LaTeX compiler or the repository-approved Tectonic binary
from the tutorial directory. Compile until references, table of contents,
figures, and cross-references stabilize.

- [ ] **Step 2: Inspect the rendered PDF.**

Check:

- prompt and response boxes do not overflow;
- long code blocks wrap or continue across pages;
- equations and CSV examples remain legible;
- figures have captions and source notes;
- hyperlinks and references resolve;
- headers and footers do not collide with content;
- the Times font remains consistent.

- [ ] **Step 3: Add clean-build instructions.**

Document the exact commands for:

1. installing tutorial dependencies;
2. executing notebooks;
3. validating prompts/transcripts;
4. building LaTeX inputs;
5. compiling the PDF;
6. running tutorial tests.

## Task 8: End-to-end release audit

**Files:**
- Modify: `docs/student_tutorial/README.md`
- Create: `docs/student_tutorial/RELEASE_CHECKLIST.md`
- Test: `tests/test_student_tutorial_content.py`

- [ ] **Step 1: Run all validation scripts.**

```bash
.venv/bin/python docs/student_tutorial/scripts/validate_prompt_registry.py
.venv/bin/python docs/student_tutorial/scripts/validate_transcripts.py
.venv/bin/python docs/student_tutorial/scripts/execute_notebooks.py \
  --input-dir docs/student_tutorial/notebooks \
  --output-dir docs/student_tutorial/outputs/notebook_runs
.venv/bin/python docs/student_tutorial/scripts/validate_tutorial_examples.py
```

- [ ] **Step 2: Run the project regression suite.**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
```

No tutorial change may regress the application suite.

- [ ] **Step 3: Perform the student usability walkthrough.**

Starting from the README, verify that a new student can:

1. open the dataset/scenario instructions;
2. inspect file schemas;
3. produce a network figure;
4. solve the three-bus example;
5. create a valid CSV;
6. run the preview;
7. interpret the non-authoritative result;
8. record a reproducible submission.

- [ ] **Step 4: Review privacy and provenance.**

Search all tutorial outputs for private plan IDs, team names, credentials,
absolute local paths, stale scenario names, and unsupported claims about
official ERCOT data.

- [ ] **Step 5: Commit the tutorial as a focused change.**

```bash
git add docs/student_tutorial tests/test_student_tutorial_artifacts.py \
  tests/test_student_tutorial_content.py pyproject.toml
git commit -m "Add AI-assisted student tutorial"
```

## Verification summary required at completion

The final implementation report must include:

- prompt/transcript counts;
- subagent generation and fact-checking method;
- notebook execution results;
- PDF compilation result;
- full test-suite result;
- figures and notebooks included;
- known limitations;
- confirmation that no live AI calls occur during ordinary builds;
- confirmation that no private team information is included.

