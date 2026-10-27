# Preserved benchmark and runtime evidence

This benchmark-only candidate starts from main `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d` and retains evaluator
hardening from `164b126c97ec06dcb494618dd959057250b4e513`. It makes no product-performance claim.

## Complete original history

The original 116-commit PR history, including all runtime changes and runtime tests,
is retained at `164b126c97ec06dcb494618dd959057250b4e513`. A separate local archive ref
`refs/archive/pr3/full-20261026` points to that exact commit. The verified offline
`complete-history.bundle` preserves every original ref and reflog-reachable object.
Its SHA-256 is `362d1bd8a0aa56c4ff1e982afaf4dea3e7ddd0df4916a992b64b03e468891391`. The candidate was restored from that
bundle; the original PR branch was not rewritten or updated.

The existing published [audit evidence](https://github.com/VivaanNarayana/colony/tree/164b126c97ec06dcb494618dd959057250b4e513/docs/results/benchmark-audit)
and [complete remediation history](https://github.com/VivaanNarayana/colony/tree/164b126c97ec06dcb494618dd959057250b4e513/docs/results/benchmark-remediation)
remain attached to that historical source. This includes trial 59, its failures,
all prior attempts and the pause/resume records. No failed result or quality label
was discarded or changed.

## Separate workspace archive

A private, content-addressed archive preserves the original project workspace,
all earlier trial runtimes/results, this task's no-Colony/matched/full-trial
evidence, rejected experiments, CLI/OTel sources/build outputs/smokes, exact pinned
local models, the retained stock CLI package and read-only analyses. It contains
327,340 file paths; duplicate bytes are stored once without losing
path identities. Every unique blob and embedded manifest was hash-verified.

Archive SHA-256: `e2e93f43a2cac993c23a7dc8b616e13334f47031167e3a27d7f101dd15758814`.
Path-manifest SHA-256: `ccb832c8db8fe1aa8e5495b3563def203eaad93c645ecd990d432d38772f939a`.

Local archive locations, verification logs and recovery instructions accompany
the separately retained candidate-build record. The archive contains credentials,
encryption keys and private stores and remains private; it is not a public fixture
or an artifact to commit. Original files also remain in place. Disposable virtualenv
dependencies are represented by the captured interpreter/package inventory.

## Minimal genuine fixture

Only the four original trial-22 local-input files required by evaluator corruption
tests are copied into `tests/fixtures/benchmark-audit/trial-22`. They retain their
original bytes, revisions, measurements, labels and hashes. They test audit
reconstruction, not the performance of this candidate. All other remediation
evidence remains in the complete history and private archive.

Historical results are never transferred to the candidate's commit or treated as
new measurements. Candidate self-test and A/A outcomes are recorded separately.
