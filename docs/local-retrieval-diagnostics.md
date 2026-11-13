# Local retrieval diagnostics

A local caller can pass a fresh `RetrievalDiagnostics()` as `diagnostics=` to
`Store.find_answer` or `Store.ask`, then inspect `snapshot()`. The default remains
disabled. There is no HTTP/MCP setting, response field, disk write or model call.
The buffer retains at most 128 records (configurable 1–512) and counts dropped
records. Use one buffer per request; snapshots are detached copies.

Records cover authorized active answer candidates only: dense similarity,
rerank logits, fused score, exact-question bypass, procedure and question gates,
Git rejection/recheck, lifecycle recheck and declared-source validation/age.
The scope filter runs before candidates are recorded. Candidate-limit saturation
means additional candidates are unknown, not that none exist. Query text, saved
content, namespace names, source paths, hashes and excerpts are not logged.
Trail IDs remain local diagnostic identifiers and should be handled as private.

`Store.ask` records when it enters its existing evidence fallback. The adaptive
compact path also records this event with
`reason: budget_limited` when a validated answer's automatic bundle needs its
existing same-finding evidence salvage. Diagnostics remain local and can be used
alongside context budgets/profiles; their records do not consume response budget.
Detailed passage ranking in that fallback is not instrumented by this change. Source
counts and validation status describe declared references, not semantic coverage
of every claim or the whole repository. Failure or saturation of observation
cannot block the answer. Ranking, thresholds and response contracts are unchanged.

These diagnostics must be enabled during a future separately authorized run to
explain its rejection stages. They cannot reconstruct missing historical scores.
Tests use deterministic fake scores/vectors with real SQLite, scope, lifecycle and
source validation. No live evaluation or retrospective replay is needed.
