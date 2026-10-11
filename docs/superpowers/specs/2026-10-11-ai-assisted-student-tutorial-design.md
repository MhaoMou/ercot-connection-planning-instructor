# AI-Assisted Student Tutorial Design

**Status:** Approved design for implementation planning

**Scope:** Student-facing tutorial companion for the ACTIVSg2000 competitive
data-center connection-planning assignment.

## 1. Purpose

The existing student manual is the authoritative assignment contract: it
defines the rules, data schema, horizons, scoring terms, and deliverables. The
new tutorial will be a separate learning document that teaches students how
to understand and complete the assignment with AI-assisted computational
workflows.

The tutorial will show complete prompt/response examples for dataset
inspection, visualization, DCOPF formulation, CSV construction, Colab/local
testing, debugging, strategy experiments, and reproducibility. It will not
replace the manual or expose private team plans.

## 2. Design principles

1. **AI assistance is explicit.** Students are encouraged to use ChatGPT,
   Codex, Claude, GitHub Copilot, Colab AI, MATLAB assistants, and similar
   tools.
2. **Generated answers are not authoritative.** Every AI response is labeled
   as representative and is paired with a verification checklist.
3. **Executable evidence beats invented output.** Python examples, figures,
   CSV checks, and numerical results are generated from executable notebooks or
   repository code before inclusion in the PDF.
4. **The current game contract is authoritative.** Examples use the current
   ACTIVSg2000 model, canonical seven-column site-level CSV, one-hour
   half-year horizons, current storage semantics, and current evaluator
   terminology.
5. **Public data and instructional assumptions remain separate.** The
   tutorial must distinguish the public synthetic ACTIVSg2000 source model,
   the instructor-prepared scenario package, and game-specific assumptions.
6. **Privacy is preserved.** Examples use synthetic or instructor-approved
   plans only; no cross-team private plan, ranking detail, or instructor-only
   diagnostic is included.

## 3. Deliverables

The tutorial is a standalone LaTeX project with a computational companion:

```text
docs/student_tutorial/
├── main.tex
├── references.bib
├── README.md
├── figures/
├── prompts/
├── transcripts/
├── notebooks/
├── outputs/
└── scripts/
```

The principal deliverable is a compiled PDF. The notebooks and prompt files
are supporting, inspectable artifacts that make the examples reproducible.

The tutorial must not alter the existing student or instructor manual
contracts.

## 4. Tutorial content

Each module has the same structure:

1. objective;
2. required context and files;
3. recommended AI prompt;
4. representative AI response;
5. verification checklist;
6. executable exercise;
7. expected artifact;
8. common failure modes.

The first release contains these modules:

### Module 1: Assignment orientation

Explain the game, student decisions, evaluator decisions, deliverables,
three-week schedule, six half-year submissions, and AI-use expectations.

### Module 2: ACTIVSg2000 data inspection

Show how to obtain the instructor-approved dataset/scenario package, identify
bus, branch, generator, cost, load, scenario, and metadata files, and verify
keys, shapes, missing values, and provenance. Explicitly distinguish the
public synthetic ACTIVSg2000 model from the processed scenario package used by
the game.

### Module 3: Python and optional MATLAB visualization

Show structural network plots, generator placement, demand summaries, and
basic geographic plots only when geographic fields exist. Python/Colab is the
required path; MATLAB is optional enrichment.

### Module 4: DCOPF formulation

Progress from conceptual explanation to mathematical formulation to a
verified three-bus implementation. Cover nodal balance, angle variables,
branch limits, generator limits, objective costs, reference angle, sign
conventions, and solver status.

### Module 5: CSV submission

Explain the current seven-column site-level schema, one row per selected site,
bus-ID validation, MW/MWh units, zero-capacity representation, and why hourly
rows are not submitted. Generate and validate a concrete example.

### Module 6: Storage and team-owned assets

Explain power capacity, energy capacity, state of charge, discharge limits,
terminal state of charge, grid-operated dispatch, and team-only service. The
student submits capacities, not hourly dispatch decisions.

### Module 7: Colab and local preview

Install the package, load a scenario, validate a CSV, run the conservative
student preview, inspect JSON output, and explain why preview output is not an
official score. Include setup and troubleshooting cells.

### Module 8: Strategy experiments

Use controlled ablations to study site count, connection capacity, private
generation, storage, concentration, dispersion, congestion, and joint
feasibility without revealing private strategies.

### Module 9: Debugging and reproducibility

Teach students to provide complete tracebacks and file context to an AI agent,
diagnose the failing layer, record package versions, preserve prompts, and
document human verification.

## 5. Prompt and transcript system

Each prompt is stored as a stable Markdown file with a unique identifier. The
prompt registry records:

- prompt ID;
- module;
- learning objective;
- required input files;
- allowed sources;
- expected output type;
- facts that must be verified;
- privacy restrictions.

For each prompt, the curation workflow is:

```text
approved prompt + approved context
        ↓
generation subagent
        ↓
fact-checking subagent
        ↓
instructor/editor review
        ↓
frozen transcript and PDF excerpt
```

The generation subagent must follow this rule:

```text
Use only the supplied repository files and cited sources.
If a fact cannot be established, say so explicitly.
Do not invent fields, bus IDs, generator data, costs, or solver behavior.
Separate verified facts, interpretations, and unknowns.
```

The fact-checking subagent checks:

- current repository terminology;
- current CSV columns;
- current horizons;
- current scoring and storage rules;
- dataset provenance;
- mathematical signs and units;
- executable code claims;
- privacy leakage.

The PDF includes a polished representative response, while the repository
retains the complete reviewed transcript and metadata. Transcript generation
does not run during ordinary LaTeX compilation; reviewed responses are frozen
for reproducibility.

## 6. Executable computational examples

The notebook companion contains at least:

1. dataset inventory and schema checks;
2. bus/branch/generator visualization;
3. three-bus DCOPF;
4. storage state-of-charge example;
5. canonical CSV validation;
6. student preview execution;
7. controlled strategy ablation.

Notebook execution is automated before release. A failed cell, missing figure,
unexpected schema, or changed numerical assertion fails the tutorial build.
The official student PDF may show selected outputs, but every computational
claim must have an executable source or a cited external source.

## 7. LaTeX presentation

Use the existing Times-based assignment style and add reusable framed
environments:

```latex
\begin{promptbox}{Prompt 1 --- Inspect the dataset}
...
\end{promptbox}

\begin{aibox}{Representative AI response}
...
\end{aibox}

\begin{verifybox}{What you must verify}
...
\end{verifybox}

\begin{checkpointbox}{Checkpoint}
...
\end{checkpointbox}
```

The default build uses `tcolorbox` and `listings`. `minted` is optional only
when the build environment supports the required external highlighting
workflow. TikZ is used for conceptual diagrams; Python-generated Matplotlib
figures are used for data-driven plots.

## 8. Recommended tools

- `nbclient` executes notebooks and preserves code outputs for verification.
- Jupyter Book/MyST is an optional future HTML companion, not the primary PDF
  build.
- Quarto is an optional alternative for executable computational reports, not
  the authoritative assignment document.
- `tcolorbox` provides breakable prompt, output, warning, and checkpoint
  panels.
- `listings` provides reproducible default code formatting.
- Matplotlib is the required deterministic plotting path; pandapower/Plotly
  may be shown as optional exploration tools.
- MATPOWER documentation provides the reference for case-file fields and DC
  modeling terminology.
- Google Colab is the recommended student execution environment, with its
  resource limits and non-authoritative runtime behavior stated explicitly.

## 9. Figures and diagrams

The PDF contains:

1. raw data → scenario package → student CSV → joint DCOPF → score;
2. relationships among bus, branch, generator, cost, and scenario files;
3. student decisions versus evaluator decisions;
4. three-bus DCOPF;
5. CSV-to-evaluator flow;
6. storage state of charge;
7. strategy experiment workflow.

Figures must use current terminology and must not show private team plans.

## 10. Acceptance criteria

The tutorial is ready for release when:

- every required module has at least one prompt/response pair;
- every response is marked representative and fact-checked;
- every code example executes from a clean environment;
- every numerical output is generated or cited;
- the current seven-column CSV format is used everywhere;
- the current half-year horizons are used everywhere;
- storage semantics match the evaluator;
- no retired six-period terminology remains;
- no private cross-team strategy appears;
- the PDF compiles without broken references or missing figures;
- a student unfamiliar with the project can complete the dataset, DCOPF,
  CSV, and preview checkpoints;
- the tutorial links to the assignment handout and repository guide.

## 11. References to include

- Texas A&M Electric Grid Test Case Repository, ACTIVSg2000 materials:
  <https://electricgrids.engr.tamu.edu/electric-grid-test-cases/synthetic-pmu-data/>
- MATPOWER case-file format:
  <https://matpower.org/docs/ref/matpower7.0/lib/caseformat.html>
- MATPOWER documentation:
  <https://matpower.org/doc-original/>
- Google Colab FAQ:
  <https://research.google.com/colaboratory/faq.html>
- `tcolorbox` manual:
  <https://tug.ctan.org/macros/latex/contrib/tcolorbox/tcolorbox.pdf>
- `minted` documentation:
  <https://tug.ctan.org/macros/latex/contrib/minted/minted.pdf>
- pandapower plotting documentation:
  <https://pandapower.readthedocs.io/en/stable/plotting.html>
- nbclient documentation:
  <https://nbclient.readthedocs.io/en/latest/client.html>
- Jupyter Book execution documentation:
  <https://jupyterbook.org/stable/execution/>
- Quarto Python computation documentation:
  <https://quarto.org/docs/computations/python.html>
- nbgrader documentation:
  <https://nbgrader.readthedocs.io/en/stable/>

## 12. Out of scope

- changing the game rules;
- changing evaluator mathematics;
- changing the canonical CSV schema;
- making AI output authoritative;
- embedding live model calls into the release PDF build;
- exposing private team plans or instructor-only diagnostics;
- replacing the assignment handout with a notebook-only workflow.
