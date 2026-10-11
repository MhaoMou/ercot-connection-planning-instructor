# Task 4 Student Tutorial Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Harden and make reproducible the student-tutorial LaTeX build while fixing its test collection failure.

**Architecture:** Keep frozen transcripts as the only build inputs. The input builder invokes the existing offline validators, then safely renders prose and code into generated LaTeX. A tutorial-local build script owns temporary build paths and selects an installed offline LaTeX toolchain.

**Tech Stack:** Python standard library, pytest, LaTeX (`latexmk`, `tectonic`, or `pdflatex`/`bibtex`).

---

### Task 1: Restore focused pytest collection

**Files:**
- Modify: `tests/test_student_tutorial_artifacts.py:289-346`

- [ ] Move the `private_information_present` assertion into the transcript-schema test loop and run `python3 -m pytest --collect-only -q tests/test_student_tutorial_artifacts.py`.

### Task 2: Add validator-backed, safe LaTeX input generation

**Files:**
- Modify: `docs/student_tutorial/scripts/build_latex_inputs.py`
- Test: `tests/test_student_tutorial_artifacts.py`

- [ ] Add tests for validator failures, placeholder/non-fact-checked rejection, and a code fence containing literal `\\end{lstlisting}`.
- [ ] Run those tests red, then implement validator loading, error propagation, and `listings` escape handling with no network/model imports.
- [ ] Run the focused tests green.

### Task 3: Make source and local build deterministic

**Files:**
- Modify: `docs/student_tutorial/main.tex`
- Create: `docs/student_tutorial/scripts/build_pdf.py`
- Modify: `docs/student_tutorial/README.md`
- Modify: `.gitignore`
- Test: `tests/test_student_tutorial_artifacts.py`

- [ ] Add a fixed date and tests that reject `\\today`.
- [ ] Add the tutorial-local build command that generates inputs, verifies the generated include, selects an available offline compiler, and fails clearly when inputs are missing.
- [ ] Document the command and generated-output behavior.
- [ ] Ignore generated tutorial LaTeX/PDF artifacts and add tests for the hygiene contract.

### Task 4: Improve straightforward bibliography metadata and verify

**Files:**
- Modify: `docs/student_tutorial/references.bib`
- Test: `tests/test_student_tutorial_artifacts.py`

- [ ] Add stable URLs/identifiers for the MATPOWER manual and package/documentation entries where already known from the repository.
- [ ] Run focused tests, the complete test suite, and the local tutorial build if a LaTeX toolchain is available.
- [ ] Inspect the diff, stage only requested tutorial files, and commit with `Harden Task 4 student tutorial build`.
