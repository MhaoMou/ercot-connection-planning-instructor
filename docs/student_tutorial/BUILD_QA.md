# Student tutorial PDF build and visual QA

Date: 2026-10-11

This report records the Task 7 clean-build and visual inspection. The tutorial
source was not changed because the build and representative-page review found
no concrete layout or reference defect.

## Environment

- Repository: `<repository-root>`
- Python used by `build_pdf.py`: `/opt/homebrew/bin/python3`
- LaTeX toolchain selected by `build_pdf.py`: `latexmk` with `pdflatex` and
  `bibtex` from `/Library/TeX/texbin`
- Rendering tools: `/opt/homebrew/bin/pdftoppm` and `pdfinfo`
- `qpdf` was unavailable, so its optional structural check was skipped.
- The system Python did not provide `pytest`; the installed
  `/opt/anaconda3/bin/pytest` was used for the focused test command.

## Clean build

The PDF was built into a fresh temporary directory; no PDF or PNG was written
under `docs/student_tutorial/`:

```bash
qa_dir=$(mktemp -d /private/tmp/student-tutorial-qa.XXXXXX)
mkdir -p "$qa_dir/build" "$qa_dir/render"
PYTHONDONTWRITEBYTECODE=1 python3 docs/student_tutorial/scripts/build_pdf.py \
  --tutorial-root "$PWD/docs/student_tutorial" \
  --build-dir "$qa_dir/build"
```

Result:

- Build exit status: 0
- Output: `/private/tmp/student-tutorial-qa.t6EKVZ/build/main.pdf`
- Pages: 42
- Bytes: 360139
- SHA-256: `6b2b0b62879ddc9c5c8732027cd3c7bb56c92728b8f2a9e78a6ce5ef49e5101c`
- A second clean build was byte-identical and produced the same SHA-256.
- Compiler logs contained underfull boxes only. They contained no overfull
  boxes, undefined citations, undefined references, multiply-defined labels,
  or missing bibliography entries.

## Validation commands

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/pytest \
  tests/test_student_tutorial_artifacts.py \
  tests/test_student_tutorial_content.py -q
```

Result: `76 passed in 18.56s`.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 \
  docs/student_tutorial/scripts/validate_prompt_registry.py
PYTHONDONTWRITEBYTECODE=1 python3 \
  docs/student_tutorial/scripts/validate_transcripts.py
```

Results: `validated 13 prompt/transcript pairs`; `validated 13 transcripts`.

## Rendered-page inspection

Representative pages were rendered with `pdftoppm -png -r 150` into the clean
temporary render directory and inspected visually:

| Pages | Coverage | Result |
| --- | --- | --- |
| 1-2 | cover and contents | readable, aligned, page links/toc entries present |
| 4, 6-7, 8 | diagrams, equations, CSV examples, notebook guidance | figures and captions visible; no clipping or collisions |
| 12, 25, 28, 36 | prompt/transcript boxes and long code/table content | boxes continue cleanly; code wraps; no overflow |
| 41-42 | reproducibility checklist and references | footer/header clear; references legible; no broken final-page layout |

Additional text/structure checks found all seven expected figure captions in the
PDF text. The final page visibly contains references [1]-[10].

Generated PDF and PNG files remain in `/private/tmp` only and are not release
source artifacts.
