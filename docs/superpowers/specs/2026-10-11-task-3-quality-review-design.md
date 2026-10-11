# Task 3 Quality-Review Hardening Design

## Goal

Harden the student tutorial execution and validation path without changing the
seven notebook names or the existing generated artifact filenames.

## Design

The notebook runner will validate that input and output directories are
disjoint, execute from a temporary isolated run root, and publish results only
after every source notebook succeeds. Source notebooks will be read from the
input tree and never overwritten. The output publication step will remove
stale notebook artifacts from the destination and replace them with the
complete successful run, preventing a prior successful artifact from making a
later partial run appear green.

The tutorial extra will include a bounded matplotlib dependency because the
visualization notebooks import it directly.

The three-bus tutorial will use the project DCOPF API and HiGHS-backed model.
It will define a three-bus network, generator limits/costs, and branch
susceptances/limits; call `solve_dcopf`; and assert the resulting generation,
angles, and flows satisfy the optimizer's balance and physical equations.
The JSON artifact will retain the existing filename while reporting the
optimizer-backed solution.

The tutorial validator will require the exact canonical CSV header, exactly
seven columns in every row, finite numeric capacity values, and nonnegative
capacities. It will continue to validate the existing artifact set and JSON
finiteness checks.

All seven notebooks will receive explicit unique nbformat cell IDs. Regression
tests will cover path overlap, malformed CSVs, deterministic reruns, and cell
ID presence. The preview notebook will explain that
`ACTIVSG2000_ARTIFACT_DIR` is informational unless code explicitly consumes
it.

## Verification

The focused tutorial tests will run first, followed by the full repository
test suite. The final commit will include only the intended tutorial,
dependency, test, and design/plan changes; existing unrelated untracked LaTeX
build artifacts will remain untouched.
