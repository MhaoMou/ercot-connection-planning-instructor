# Student Tutorial Release Audit

Date: 2026-10-11

## Decision

**PASS WITH KNOWN LIMITATIONS.** The checked-in tutorial source, frozen
prompt/transcript set, content audit, deterministic PDF build, and available
regression tests pass. Notebook execution and `highspy`-dependent behavior
remain unverified in the current environment. The only test failures are two
notebook-runner tests blocked by sandbox local-socket permissions; they are not
code-logic failures.

## Release inventory

- 13 prompt files and 13 fact-checked transcript files validated.
- 7 notebooks and 7 TikZ figures audited.
- 42-page PDF built in an isolated generated-input directory with SHA-256:
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
validated published tutorial examples
exit 0
```

The validators and content audit pass.

Focused tutorial tests:

```text
/opt/anaconda3/bin/python -m pytest \
  tests/test_student_tutorial_artifacts.py \
  tests/test_student_tutorial_content.py \
  tests/test_student_tutorial_reproducibility.py -q
82 passed
```

Full available pytest suite:

```text
/opt/anaconda3/bin/python -m pytest -q
all non-notebook tests passed; 4 skipped; 2 failed
```

The two failures were the notebook-runner tests. They were blocked by
`PermissionError` during sandbox local-socket operations; this is an
environment permission failure, not a code-logic failure. Notebook execution,
including the `highspy`-dependent three-bus notebook, remains unverified.

Notebook command:

```text
/opt/anaconda3/bin/python docs/student_tutorial/scripts/execute_notebooks.py \
  --input-dir docs/student_tutorial/notebooks \
  --output-dir docs/student_tutorial/outputs/notebook_runs
```

Result: the notebook-runner tests were blocked before a fresh end-to-end
notebook verification could be completed by a sandbox local-socket
`PermissionError`. The availability and execution of `highspy` therefore
remain unverified.

Deterministic PDF build:

```text
qa_dir=$(mktemp -d /private/tmp/student-tutorial-release.XXXXXX)
mkdir -p "$qa_dir/generated"
/opt/anaconda3/bin/python docs/student_tutorial/scripts/build_pdf.py \
  --tutorial-root "$PWD/docs/student_tutorial" --build-dir "$qa_dir/generated"
shasum -a 256 "$qa_dir/generated/main.pdf"
```

Result: the isolated build exited 0; the PDF was 42 pages and 360139 bytes,
and produced the SHA-256 recorded above. Generated inputs and build output were
kept under `/private/tmp` and are not tracked release artifacts. A clean
checkout no longer mutates source-tree generated inputs.

## Task-by-task status

| Plan task | Status | Evidence |
| --- | --- | --- |
| 1. Metadata and artifact contracts | PASS | Registry and focused artifact tests validate the 13-entry contract. |
| 2. Prompt library and frozen transcripts | PASS | 13 prompt/transcript pairs validate; content audit passes. |
| 3. Executable notebooks | PASS with limitation | 7 notebooks are structurally audited; fresh execution and `highspy` availability remain unverified because notebook-runner tests hit sandbox local-socket permissions. |
| 4. LaTeX presentation layer | PASS | Clean PDF builds exit 0. |
| 5. Diagrams and references | PASS | Content audit covers all 7 figures; PDF has 42 pages. |
| 6. Content and consistency tests | PASS | Fresh combined artifact/content/reproducibility tests pass: 82 passed. |
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
- A clean checkout no longer mutates source-tree generated inputs; generated
  PDF inputs are isolated in the build directory.
- Tutorial sources continue to distinguish public synthetic data, released
  scenario packages, student preview output, and authoritative evaluation.

## Environment limitations

1. Notebook execution and `highspy` availability remain unverified. The two
   notebook-runner test failures were sandbox local-socket `PermissionError`s,
   not application logic failures.
2. The PDF audit used an isolated generated-input directory under `/private/tmp`;
   the clean checkout no longer mutates source-tree generated inputs.
3. The system Python lacked `pytest`, `nbformat`, and `nbclient`; the available
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

## Canonical six-hour release audit — 2026-10-16

The canonical game configuration is now the six-hour ACTIVSg2000 release with
`daily_exact_cycle` storage. The hourly package remains available as a
derivation/reference input and is not the default student-game configuration.
Submission 2 energy purchases settle against LMPs from the actual joint
dispatch; a baseline `economic_profiles.npz` is optional diagnostic material,
not a required scoring input.

### Scenario-package validation

```text
PYTHONPATH=/private/tmp/ercot_highs_target:src:. \
  python tools/activsg2000/validate_scenario_package.py \
  --package data/processed/activsg2000_scenarios_v2_6h --workers 4
```

Result: `hour_count=4384`, `expected_hour_count=4384`,
`passed_hour_count=4384`, `failed_hour_count=0`, and
`unverified_hour_count=0`. All six 2027--2029 half-year blocks have
`validation_status: passed` and a validated state hash.

### Official eight-team benchmark

The benchmark used the checked-in six-hour package and rules directly, with no
temporary manifest or zero-price override:

```text
PYTHONPATH=/private/tmp/ercot_highs_target:src:. \
  python -m ercot_game.batch_cli run \
  --submissions-dir docs/instructor_manual/examples/eight_team_round/submissions \
  --artifact-dir data/processed/activsg2000_scenarios_v2_6h/2028/jan_jun \
  --rules data/teaching/dispatch_scoring_rules_jan_jun_6h_v1.json \
  --horizon 728 --workers 4 --submission-number 2 \
  --output-mode compact --solver-backend auto --solver-threads 1 \
  --output-dir /private/tmp/ercot_official_6h_release/results
```

Result: 8 teams scored, 0 rejected, all 8 feasible. Wall time was
`330.11` seconds on the local machine. The evaluation hash was
`1198ea0ef2fe6f3ff99b4d94437fe7738d2d3d2dd20cabb96dc3c0a7b60b4125`.

The benchmark output is intentionally kept under `/private/tmp`; it is not a
student-facing ranking artifact.
