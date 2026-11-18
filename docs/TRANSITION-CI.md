# Publication transition verification

The existing `validate-runtime-control` check now requires `validate-transition`
to succeed, and explicitly fails if that dependency fails, cancels or skips.
A green snapshot check alone is insufficient.

## What is checked

A pull request compares its accepted `base.sha` deployment with the prospective
merged checkout. A main-branch push compares the event's `before` deployment
with `after`. Missing baselines, branch creation/deletion, force pushes,
noncanonical branches and mismatched checkout identities fail closed.

Each atomic publication may express at most one valid transition. Freeze,
Core promotion (frozen to frozen with epoch +1), and activation must remain
separate publications. Historical intermediate commits in one PR/push are not
evidence that a host ever observed a frozen deployment.

The transition implementation is imported from the exact Core commit named
by the **previously accepted** contract. The Core repository is resolved by its
stable numeric ID. Both documents are checked with that Core's bounded loader
and canonical contract validator before its existing transition function runs.
The candidate Core still receives the pre-existing exact-pin snapshot check.
No second transition state machine is maintained here. The candidate contract is read from its exact Git commit rather than the working tree; CI runs the bootstrap/check process with Python isolated mode to avoid candidate import-path shadowing.

## Local synthetic checks

Select an explicit Core checkout containing the current validator:

```sh
TRUSTED_CORE_ROOT=/path/to/core python -m unittest discover -s tests -v
```

These tests use synthetic deployment documents, including a schema-valid
active-to-active Core promotion that the former snapshot-only CI accepted.
The `check` CLI additionally verifies the actual validator checkout SHA against
the prior contract. Run `python ci/transition_gate.py --help` for its two phases.

## Scope and compatibility

Baseline: Runtime-Control `9a3e3102f0de1856c26faca88d26d8b812554aff`, Core
`d1ab86a4d0192bcdbf5bed84405f07af401d848c` (2026-10-03). The gate relies on
repository workflow and branch protection and reviewer trust.

A future schema migration incompatible with the accepted Core requires an
explicit reviewed migration path rather than falling back to candidate
self-validation. If Core later provides a native publication validator, preserve
the independent accepted-base obligation when retiring this bootstrap adapter.
