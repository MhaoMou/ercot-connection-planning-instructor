# Student Tutorial

This directory contains the student tutorial for the ACTIVSg2000 data-center
connection game (`main.tex`, built to `main.pdf`), its figures, and seven
small companion notebooks.

The game uses six-hour operating periods and the `daily_exact_cycle` storage
boundary. A half-year is the planning horizon: for example, 4,368
January--June 2028 source hours become 728 game periods, and 4,416
July--December source hours become 736 game periods. The student CSV is a
site-level plan; it does not contain one row per period. The numbers in the
tutorial come from the scoring-rules files in `data/teaching/` and the
scenario package in `data/processed/activsg2000_scenarios_v2_6h/`.

## Using the tutorial

The notebooks use tiny made-up fixtures to show individual mechanics. Open
them locally or upload them to Google Colab. When using Colab, clone or
otherwise provide the repository before running cells so paths resolve
consistently. The local tutorial dependencies can be installed with:

```bash
python3 -m pip install -e '.[tutorial]'
```

From the repository root, execute the notebooks with:

```bash
PYTHONPATH=src python3 docs/student_tutorial/scripts/execute_notebooks.py \
  --input-dir docs/student_tutorial/notebooks \
  --output-dir docs/student_tutorial/outputs/notebook_runs
```

and check the executed notebooks and published artifacts with:

```bash
PYTHONPATH=src python3 docs/student_tutorial/scripts/validate_tutorial_examples.py \
  --run-dir docs/student_tutorial/outputs/notebook_runs
```

Run the tutorial regression tests with:

```bash
pytest -q tests/test_student_tutorial_artifacts.py tests/test_student_tutorial_content.py \
  tests/test_student_tutorial_notebooks.py
```

`scripts/audit_tutorial_content.py` checks the tutorial text: the canonical
CSV header, horizon statements, figure integration, citations, privacy
guidance, and that retired submission fields only appear as rejected or
historical.

### Reproducible PDF build

From the repository root, build the tutorial PDF with the local LaTeX
toolchain:

```bash
python3 docs/student_tutorial/scripts/build_pdf.py
```

The command compiles `docs/student_tutorial/main.tex` into
`docs/student_tutorial/build/main.pdf` using installed `latexmk` or
`pdflatex` plus `bibtex`, always with `-no-shell-escape`. It never calls a
model or downloads data, and it fails if bibliography warnings or
overfull boxes remain in the compiler logs. Underfull boxes from long breakable examples
are benign; long CSV and path lines wrap rather than clip. The `build/`
directory is ignored.

## Verification and privacy

Treat every AI response as a starting point. Verify file names, columns, units,
bus IDs, solver status, numerical outputs, and provenance using the repository
files or executable examples. The student preview is conservative and
non-authoritative; it is not an official score.

Do not upload private team plans, credentials, rankings, instructor-only
diagnostics, or other confidential information to shared AI tools. Use public
data, synthetic examples, or instructor-approved fixtures only.
