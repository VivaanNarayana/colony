# Lookup optimization: review evidence

The current comparison fails the attempted-call compliance gate and blocks merge
review readiness. The earlier advisory judgment was rejected by the user and is
superseded. The PR remains draft while compliance is fixed and a complete new
paired benchmark is run. No other feature is being started.

The final runtime shares one query embedding across lookup stages, skips answer and
evidence candidates that cannot pass eligibility guards, and prunes excerpt reranking
using the existing score bounds. Descriptive evidence now requires coherent local
query-term support, replacing the observed unrelated validation packet with a miss.
The MCP implementation matches main byte for byte; experimental wording changes
were reverted after regressions.

## Measurements

Main is `202745a92f6c6c59e4c990321be9c5112697594e`. The final live implementation is
`d9bfc100cf8324e7e9424a56e49ddeb0099f825e`. Local timing used `4f321c0`; the analyzer
verifies that every Store benchmark dependency is unchanged in the live revision.

| Metric | Main | Feature | Observed change |
| --- | ---: | ---: | ---: |
| Warm lookup time, 72 queries | 86.383s | 22.264s | 74.2% lower |
| Query embeddings, 72 queries | 87 | 72 | 17.2% fewer |
| Reranked passages, 72 queries | 1,767 | 492 | 72.2% fewer |
| Live summed subject time, 24 questions | 285.115s | 245.219s | 14.0% lower |
| Native input + output tokens, including cached input | 1,248,150 | 1,209,180 | 3.1% lower |
| Uncached input + output tokens | 173,846 | 138,204 | 20.5% lower |
| Core-fact correct answers | 24/24 | 24/24 | unchanged |
| Correct tasks needing no repository research | 18/24 | 18/24 | unchanged |
| Tool attempts, including rejected operations | 54 | 53 | 1 fewer |

Local timing alternates implementations over eight identically seeded trails and
excludes startup/indexing. All 72 paired retrieval results matched. A separate replay
of main's real memory history matched 23/24 complete responses; the intentional
change replaces off-topic evidence for the validation-mechanism question with a miss.

The live comparison alternates which arm goes first per question. Both start with
empty independent stores, research the same frozen main corpus, and use the original
questions, outer prompts, GPT-6 Luna low and CLI version. Every server and subject MCP
process records actual imported module paths and SHA256 hashes, verified against its
pinned Git revision. No local model benchmark or test suite ran concurrently.

## Compliance and limits

All attempted calls and their native cost remain in the result. Main repeated an
identical lookup on two tasks. The feature attempted eight repository operations on
one task; the last two returned budget errors without data. Both arms received at
most six repository operations per session, and repeated lookups returned identical
data. These are reported as model policy mistakes, not hidden or replaced trials.

Attempted-call compliance is mandatory. Rejected over-budget calls still violate
the protocol. `all_gates_passed`, `all_predeclared_gates_passed`, and
`merge_review_ready` are all **false** for this trial. The original mistaken
advisory report remains in `history/original-mcp-noncompliant/` for auditability.

The token reduction is noisy: its descriptive family-bootstrap interval ranges from
a 4.8% regression to an 11.5% improvement. Earlier trials failed token/compliance
criteria and remain under `history/`; this repeatedly observed self-repository suite
does not establish general token savings. Grading was by the primary assistant,
not an independent blind reviewer. It covers core facts, not exhaustive completeness
or precise citation locations. Citation targets were valid on 20/24 main answers
and 18/24 feature answers; malformed model-generated references remain documented.

Local verification: **86 passed, 1 skipped**, Ruff and whitespace checks pass on
Windows/Python 3.13.1. The skipped test requires host symlink permission. No hosted
CI matrix is configured. The more conservative descriptive filter can reduce recall;
unsupported matches fall back to research. Existing slow-push queue behavior remains
visible in both arms' audits.

## Audit and next work

See [the comparison](comparison.json), [local timing](lookup-summary.json),
[memory-history replay](memory-history-replay.json), [manifest](manifest.json),
and [implementation import proofs](paired/feature/loaded-implementations.json).
Each compressed native trace container retains numerical usage and outcomes; only
local workspace prefixes are redacted. Failed, invalid and interrupted runs are
preserved, including the one interrupted session whose final usage is unavailable.

Review this PR before advancing. Repeated cited-source vector reuse remains the next
separate optimization candidate, with its own branch, PR and before/after benchmark.
That work remains paused. Use a held-out repository/workload before production claims.
