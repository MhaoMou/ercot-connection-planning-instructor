# Task 5 Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** Correct the three student-tutorial figures and captions so their data-flow, evaluator-boundary, preview, and strategy-provenance claims match the repository contract, while ensuring the strategy figure compiles without clipping or overlap.

**Architecture:** Keep the existing TikZ presentation layer and update only the labels, arrows, annotations, captions, and figure sizing needed to express the reviewed semantics. Add focused source-level regression tests beside the existing tutorial artifact tests, then run the offline tutorial build and inspect the generated PDF/logs before committing.

**Tech Stack:** TikZ/LaTeX, pytest, the repository's offline `build_pdf.py` workflow.

---

### Task 1: Add semantic regression coverage

**Files:**
- Modify: `tests/test_student_tutorial_artifacts.py`

- [x] Add assertions that the pipeline names public/network/scenario context and validation inputs as package-supplied, names the seven-column CSV as student-created, and contains no package-to-CSV generation claim.
- [x] Add assertions that the CSV flow distinguishes the exact instructor joint storage-aware DCOPF/LP evaluator from the six-hour sampled idle-storage non-authoritative student preview.
- [x] Add assertions that the strategy figure/caption identifies notebook 07 synthetic candidate plans and hand-built preview metrics and rejects ACTIVSg2000-derived-input wording.
- [x] Run the focused tests and confirm they fail against the current sources for the intended semantic reasons.

### Task 2: Correct figures and captions

**Files:**
- Modify: `docs/student_tutorial/figures/pipeline.tex`
- Modify: `docs/student_tutorial/figures/csv_evaluation_flow.tex`
- Modify: `docs/student_tutorial/figures/strategy_experiment.tex`
- Modify: `docs/student_tutorial/main.tex`

- [x] Make the pipeline show package-supplied public/network/scenario context and validation inputs feeding downstream paths, with the student creating the seven-column CSV and no package-to-CSV arrow.
- [x] Label the official path as the exact instructor evaluator using the joint storage-aware DCOPF/LP, and label the student path as a six-hour sampled, idle-storage, non-authoritative screening preview.
- [x] Reframe the strategy experiment around synthetic candidate plans and hand-built preview metrics from notebook 07, without implying ACTIVSg2000-derived inputs.
- [x] Resize or reframe the strategy figure and surrounding LaTeX so the loop and explanatory note remain inside the compiled page without overlap or clipping.

### Task 3: Offline compile and commit

**Files:**
- No additional source files unless compiler diagnostics require a targeted correction.

- [x] Run the focused semantic tests, then the complete tutorial artifact regression tests; notebook execution remains blocked by the existing missing `highspy` dependency.
- [x] Run `python3 docs/student_tutorial/scripts/build_pdf.py` offline and verify the generated PDF plus compiler logs contain no overfull boxes or missing references.
- [ ] Review the diff and commit only the requested tutorial sources, tests, and plan.
