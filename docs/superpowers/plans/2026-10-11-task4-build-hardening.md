# Task 4 Build Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the offline tutorial PDF build and generated transcript presentation against shell escape, missing bibliography tools, unsafe LaTeX text, and visible clipping.

**Architecture:** Keep the existing Python build scripts as the single build boundary. Add pure command/log-policy helpers that are directly unit-testable, then use those helpers from the real build. Keep transcript rendering deterministic and make the LaTeX source explicitly break long listings while enforcing a narrow warning policy.

**Tech Stack:** Python 3, pytest, `latexmk`/`pdflatex`, `bibtex`, LaTeX `listings` and `tcolorbox`.

---

### Task 1: Add regression tests for command safety and build failure policy

**Files:**
- Modify: `tests/test_student_tutorial_artifacts.py`
- Test: `docs/student_tutorial/scripts/build_pdf.py`

- [ ] **Step 1: Write failing tests** for exact `-no-shell-escape` arguments, missing `bibtex`, and bibliography warnings in compiler output.
- [ ] **Step 2: Run the focused tests** with `pytest tests/test_student_tutorial_artifacts.py -k 'pdf_build or bibliography or shell_escape' -q` and confirm they fail for the missing behaviors.
- [ ] **Step 3: Implement minimal command builders and log checks** in `build_pdf.py`.
- [ ] **Step 4: Re-run the focused tests** and confirm they pass.

### Task 2: Escape dollar signs and strengthen listing layout contracts

**Files:**
- Modify: `docs/student_tutorial/scripts/build_latex_inputs.py`
- Modify: `docs/student_tutorial/main.tex`
- Modify: `tests/test_student_tutorial_artifacts.py`

- [ ] **Step 1: Add a regression assertion** that `$` becomes `\\$` in ordinary prose and that generated code/listings keep long CSV/path content.
- [ ] **Step 2: Run the new focused test** and confirm it fails before the implementation.
- [ ] **Step 3: Add dollar escaping and breakable listing/box settings** without changing transcript data.
- [ ] **Step 4: Re-run focused generator/source-contract tests** and confirm they pass.

### Task 3: Document and enforce the warning policy

**Files:**
- Modify: `docs/student_tutorial/scripts/build_pdf.py`
- Modify: `docs/student_tutorial/README.md`
- Modify: `tests/test_student_tutorial_artifacts.py`

- [ ] **Step 1: Add a failing source/documentation assertion** for explicit benign-underfull versus fatal-overfull warning policy.
- [ ] **Step 2: Implement the policy and README explanation** using deterministic log text matching.
- [ ] **Step 3: Run the full tutorial artifact test module** and inspect failures.

### Task 4: Build, inspect, and commit

**Files:**
- Generated verification only: `docs/student_tutorial/generated/`, `docs/student_tutorial/build/`

- [ ] **Step 1: Run `pytest tests/test_student_tutorial_artifacts.py -q`.
- [ ] **Step 2: Run `python3 docs/student_tutorial/scripts/build_latex_inputs.py` and `python3 docs/student_tutorial/scripts/build_pdf.py`.
- [ ] **Step 3: Check the build log for fatal overfull/clipping or bibliography warnings and confirm the PDF exists.
- [ ] **Step 4: Review `git diff` and status, excluding pre-existing instructor-manual auxiliaries.
- [ ] **Step 5: Commit the intended source, test, plan, and spec changes with `git commit -m "Harden Task 4 tutorial PDF build"`.
