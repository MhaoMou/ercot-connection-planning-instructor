# Student Tutorial Release Audit

Date: 2026-10-11

## Decision

**PASS WITH KNOWN LIMITATIONS.** The checked-in tutorial source, frozen
prompt/transcript set, content audit, deterministic PDF build, and available
regression tests pass. A fresh notebook run is not fully executable in the
current environment because the available Python environment does not provide
`highspy`, which is required by the three-bus DCOPF notebook. The full suite
therefore has three environment-caused failures; these are recorded below and
are not hidden as passing results.

## Release inventory

- 13 prompt files and 13 fact-checked transcript files validated.
- 7 notebooks and 7 TikZ figures audited.
- 42-page PDF built twice with identical SHA-256:
  `6b2b0b62879ddc9c5c8732027cd3c7bb56c92728b8f2a9e78a6ce5ef49e5101c`
- No live AI calls are made by the validators, notebook runner, input builder,
  or PDF build.
- No private team information is present in the audited tutorial source.

## Exact commands and results

Commands were run from the repository root. The first four commands use the
standard-library-compatible Python available in the environment.

```text
python3 docs/student_tutorial/scripts/validate_prompt_registry.py
validated 13 prompt/transcript pairs
exit 0

python3 docs/student_tutorial/scripts/validate_transcripts.py
validated 13 transcripts
exit 0

python3 docs/student_tutorial/scripts/audit_tutorial_content.py
audited 13 prompt/transcript pairs, 7 notebooks, and 7 figures
exit 0

/opt/anaconda3/bin/python docs/student_tutorial/scripts/validate_tutorial_examples.py \
  --run-dir docs/student_tutorial/outputs/notebook_runs
exit 1: unexpected published output: fixtures
```

The example-output validator was not accepted as a release pass because the
fresh notebook run stopped before publishing all artifacts and left the
pre-existing ignored fixture directory in place.

Focused tutorial tests:

```text
/opt/anaconda3/bin/python -m pytest \
  tests/test_student_tutorial_artifacts.py tests/test_student_tutorial_content.py -q
76 passed
```

The single failure was the deterministic-PDF test attempting to write ignored
generated inputs inside the restricted repository sandbox. The same PDF
operation passed when run with the repository write permission granted for
this audit.

Full available pytest suite:

```text
/opt/anaconda3/bin/python -m pytest -q
478 passed, 4 skipped, 2 failed
```

The two failures were the notebook-runner tests. They reached the three-bus
notebook and failed because `highspy` is absent; Jupyter also emitted a
non-fatal macOS process-list permission warning during kernel cleanup. No
application regression failure was observed.

Notebook command:

```text
/opt/anaconda3/bin/python docs/student_tutorial/scripts/execute_notebooks.py \
  --input-dir docs/student_tutorial/notebooks \
  --output-dir docs/student_tutorial/outputs/notebook_runs
```

Result: execution started and reached
`03_three_bus_dcopf.ipynb`, then stopped with
`ModuleNotFoundError: No module named 'highspy'`. The environment also emitted
an IPython `sysctl(KERN_PROC_ALL)` permission warning during kernel cleanup.

Deterministic PDF build:

```text
qa_dir=$(mktemp -d /private/tmp/student-tutorial-release.XXXXXX)
mkdir -p "$qa_dir/build-1" "$qa_dir/build-2"
/opt/anaconda3/bin/python docs/student_tutorial/scripts/build_pdf.py \
  --tutorial-root "$PWD/docs/student_tutorial" --build-dir "$qa_dir/build-1"
/opt/anaconda3/bin/python docs/student_tutorial/scripts/build_pdf.py \
  --tutorial-root "$PWD/docs/student_tutorial" --build-dir "$qa_dir/build-2"
shasum -a 256 "$qa_dir/build-1/main.pdf" "$qa_dir/build-2/main.pdf"
```

Result: both builds exited 0; both PDFs were 42 pages and 360139 bytes, and
both produced the SHA-256 recorded above. Build output was kept under `/private/tmp`
and is not a tracked release artifact.

## Task-by-task status

| Plan task | Status | Evidence |
| --- | --- | --- |
| 1. Metadata and artifact contracts | PASS | Registry and focused artifact tests validate the 13-entry contract. |
| 2. Prompt library and frozen transcripts | PASS | 13 prompt/transcript pairs validate; content audit passes. |
| 3. Executable notebooks | PASS with limitation | 7 notebooks are structurally audited; fresh execution is blocked at the DCOPF notebook by missing `highspy`. |
| 4. LaTeX presentation layer | PASS | Clean PDF builds exit 0. |
| 5. Diagrams and references | PASS | Content audit covers all 7 figures; PDF has 42 pages. |
| 6. Content and consistency tests | PASS with environment caveat | 75 focused tests pass; the one failure is generated-input write permission. |
| 7. PDF build and visual QA | PASS | Existing `BUILD_QA.md` records clean build, hash, and representative-page inspection; this audit reproduced the deterministic hash. |
| 8. End-to-end release audit | PASS with known limitations | This report records all commands, evidence, limitations, and remaining risks. |

## Privacy, provenance, and tracking checks

- `git ls-files` shows no tracked files under
  `docs/student_tutorial/build/`, `generated/`, `outputs/`, or notebook
  `__pycache__/` directories.
- A tracked-file scan found no credentials, API tokens, private keys, or
  private team identifiers.
- A tracked absolute-path scan found and removed one developer-local path from
  `BUILD_QA.md`; the report now uses repository-relative wording.
- The unrelated untracked instructor-manual auxiliary files were not added,
  changed, or committed.
- Tutorial sources continue to distinguish public synthetic data, released
  scenario packages, student preview output, and authoritative evaluation.

## Environment limitations

1. `highspy` is absent from the available Anaconda Python, so notebooks and
   tests that require a live HiGHS DCOPF solve cannot be freshly executed here.
2. The restricted sandbox initially denied writes to ignored tutorial build
   directories. A scoped repository write permission was granted for the PDF
   audit; the source change itself remained narrow.
3. Jupyter kernel startup required local socket binding. With the scoped
   permission, startup proceeded, but kernel cleanup still reported a macOS
   process-list permission warning.
4. The system Python lacked `pytest`, `nbformat`, and `nbclient`; the available
   Anaconda Python was used for the test and notebook commands above.

## Remaining risks

- A release environment must install the declared project and tutorial
  dependencies, including `highspy`, before claiming all notebooks execute
  end to end.
- The notebook output validator should be rerun after that successful clean
  execution so that all seven executed notebooks and all published artifacts
  are validated together.
- The PDF was reproducible and the prior visual QA was reviewed, but visual
  inspection remains dependent on the local TeX/font toolchain.
- The student preview remains intentionally non-authoritative; users must
  compare final submissions with the authoritative assignment/evaluator
  contract.
