# Task 4 Student Tutorial Review Fixes Design

**Status:** Approved for implementation

## Goal

Make the student-tutorial LaTeX presentation deterministic, offline, and safe
to rebuild from a clean checkout, while fixing focused pytest collection and
preserving the tutorial's existing student-facing scope.

## Design

`build_latex_inputs.py` will load the existing prompt-registry and transcript
validators by module path and fail before writing output when either validator
reports an error. It will reject placeholders and any transcript whose review
status is not `fact-checked`. Fenced code will be emitted with a listings
escape mechanism so literal `\\end{lstlisting}` in source code cannot close the
generated environment early. The helper remains pure standard-library Python
and never imports a network or model client.

The tutorial source will use a fixed source-controlled date. A new local build
script in `docs/student_tutorial/scripts/` will create a generated-input/build
directory, run the validator-backed input generator, verify the generated
include exists and is non-empty, and compile with an available local LaTeX
toolchain without network access. README instructions will document the exact
command and clean-checkout behavior.

Generated LaTeX intermediates, generated transcript inputs, and PDFs will be
ignored under the tutorial directory. Regression tests will cover collection,
validator enforcement, placeholder rejection, safe listings rendering, build
input presence checks, deterministic date, bibliography metadata, and no
network/model calls.
