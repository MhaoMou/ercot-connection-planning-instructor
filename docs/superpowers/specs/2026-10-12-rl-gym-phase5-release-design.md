# RL Gym Phase 5 Research Release Design

## Goal

Turn the validated Phase 1--4 RL wrapper into a reproducible research-release
surface without changing the evaluator, network model, dispatch mathematics,
projection, scoring, or team-privacy behavior.

## Scope

Phase 5 provides four release capabilities:

1. explicit release and environment version metadata;
2. stable hashes and JSON-serializable manifests for scenario/rule/config
   provenance;
3. minimal single-agent and competitive multi-team examples;
4. release documentation and focused tests.

The release remains an offline Python package. It does not add a web service,
training algorithm, solver implementation, or new classroom feature.

## Architecture

The existing domain packages remain authoritative:

```text
scenario/rules/configuration
          -> release manifest + stable hashes
          -> RL environment/replay metadata
          -> existing adapter/evaluator boundary
```

`rl_gym` owns only metadata, reproducibility helpers, examples, and the already
validated environment interfaces. A manifest records identifiers and hashes,
not private team plans, solver internals, or large generated artifacts. Hashing
must canonicalize mappings and sequences so equivalent JSON-compatible inputs
produce the same digest.

## Version contract

The release will expose:

- a package/release version string;
- an environment API version;
- a plan schema version;
- scenario year and half-year block;
- evaluator version when supplied by the caller;
- optional rule/config hashes;
- supported block metadata.

Existing replay files must continue to load. New replay manifests may carry the
additional release fields, while validation remains strict for fields required
to reproduce an episode.

## Example contract

The examples will be executable Python modules rather than notebooks:

- a single-agent example runs a deterministic baseline, prints a compact
  summary, and optionally saves a replay JSON file;
- a parallel example runs two private teams to the joint terminal boundary and
  prints only sanitized per-team rewards/statuses and aggregate counts.

Examples default to no evaluator so they work in a clean install. An evaluator
callback can be injected by application code; examples must not invent scoring
math or hard-code solver outputs.

## Privacy and failure behavior

Examples and manifests must never print or serialize another team’s plan,
team-keyed projection diagnostics, raw evaluator internals, or private asset
details. Evaluator failures remain deterministic terminal outcomes according to
the Phase 4 contract.

## Verification

Tests will cover:

- stable canonical hashes and manifest round trips;
- version metadata and supported-block declarations;
- example smoke execution without a solver;
- replay compatibility with release metadata;
- preservation of Phase 1--4 privacy and terminal semantics.

The focused RL suite and `git diff --check` are required before completion.

## Non-goals

- no changes to DCOPF, storage constraints, fair projection, or scoring;
- no new network reduction or dataset;
- no automatic training benchmark claims;
- no removal of the existing single-agent replay format;
- no cleanup of unrelated tutorial or generated-document changes already in the
  working tree.
