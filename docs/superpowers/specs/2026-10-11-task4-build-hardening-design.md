# Task 4 Tutorial Build Hardening Design

## Goal

Make the offline student-tutorial PDF build fail closed on unsafe or incomplete
toolchains, render transcript text safely, and keep long generated examples
visible without clipping.

## Design

`docs/student_tutorial/scripts/build_pdf.py` will expose deterministic command
builders for `latexmk` and `pdflatex`, each with explicit `-no-shell-escape`.
The build will require the selected TeX engine and the bibliography toolchain
needed by `main.tex`; it will capture compiler output and reject unresolved
bibliography warnings. A small warning-policy helper will reject overfull boxes
and unexpected warnings while documenting accepted underfull-box warnings.

`build_latex_inputs.py` will escape dollar signs in ordinary LaTeX text. Code
fences will continue to use `listings`, with breakable line settings in
`main.tex`; generated transcript content will not be shortened or dropped.

Regression tests will inspect exact command arguments, exercise missing
toolchain and bibliography-warning failures, verify dollar escaping, and check
the source-level layout/warning contracts. The final verification will run the
targeted pytest module, the deterministic input generator, and the complete
offline PDF build.

## Scope and non-goals

The change will not switch TeX engines, download packages, alter transcript
content, or commit generated build products. Existing benign underfull-box
warnings may remain if the build log identifies them explicitly; overfull boxes,
clipping indicators, and bibliography warnings remain build failures.
