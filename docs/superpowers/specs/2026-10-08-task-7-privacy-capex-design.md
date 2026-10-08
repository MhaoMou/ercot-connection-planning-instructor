# Task 7 Privacy-Safe Classroom Outputs and Weekly Capex Design

## Goal

Repair the classroom CLI and weekly scoring coordinator so that classroom-facing files do not leak another team's raw submission or schedule, while weekly scored results charge newly built generation, storage, and connection-site capacity exactly once for the match.

## Design

The classroom CLI will use a dedicated privacy-safe writer instead of calling `write_batch_outputs` directly. It will produce:

- shared aggregate artifacts containing anonymized load/capacity/constraint totals and competition-level diagnostics;
- one private summary directory per team containing only that team's score, feasibility, failure diagnostics, and plan-relevant aggregate diagnostics;
- no team IDs, submission keys, manifests, raw schedules, or per-team dispatch/storage schedules in shared files;
- no instructor raw artifacts unless an explicit opt-in is added by a future caller. The current classroom command has no raw-output opt-in, so raw files are never written by default.

The privacy writer will derive shared data from the existing dispatch/evaluation objects but will whitelist fields rather than serialize `BatchEvaluation`, dispatch records, or projection maps wholesale. Team-private filenames will use a stable digest of the team ID so the shared tree does not reveal identity through filenames. The private payload may include the team's own identifier because it is delivered in that team's private directory.

Weekly capex will be separated from the submission used for the weekly dispatch score. `evaluate_round` will continue to materialize the current submission for operation, but it will compute new generator/storage asset IDs and new site capacity relative to the prior weekly ledger. For a non-feedback week, the scored result will use the Submission 2 operational score plus the weekly capex delta from both submissions, with each asset/site counted once. Reusing or omitting an asset/site in Submission 2 will not erase Submission 1 construction cost or charge it again. Week 1 will remain feedback-only, and entering Week 2 or Week 3 will reset the ledger before collecting that week's deltas.

The existing DCOPF dispatch, economic settlement, fair projection, and plan materialization behavior will remain unchanged. Changes will be limited to output serialization, capex scope propagation, and the weekly aggregation boundary.

## Tests

Add regressions that:

1. run the classroom CLI with two teams and scan every shared/private classroom output file for the other team's ID, raw per-team schedule keys, submission keys, and raw manifest fields;
2. assert a private summary contains only its own team's score/feasibility/diagnostics;
3. build a generator/storage/site in Submission 1, omit or reuse it in Submission 2, and verify the weekly scored result includes the construction cost once;
4. verify connection-site cost uses only newly built capacity and is not repeated when the existing site is submitted again;
5. verify Week 1 remains feedback-only and Week 2/3 reset behavior is preserved;
6. run focused tests, the full suite, a diff/status check, and commit the completed change.
