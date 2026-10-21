# Lookup optimization: strict eight-gate comparison

> Audit notice: the [benchmark independence audit](../../benchmark-audit.md)
> supersedes the readiness conclusion below. Reassessment under the new blocking
> criteria fails five of eight gates. Original measurements and historical review
> text are retained; their earlier pass does not establish independent improvement.

All eight blocking gates pass. The first feature is ready for maintainer review;
the PR remains unmerged and other feature work remains paused. No gate is advisory.
The earlier +19.3% uncached regression and unsuccessful follow-ups are retained in
[history](history/); none qualify under the current requirements.

The runtime reuses one immutable query vector across lookup stages, skips candidates
that cannot pass eligibility guards, and prunes excerpt scoring with safe bounds.
Descriptive evidence requires coherent local query-term support. The MCP receipt
fields, source proofs, current read fields and source bytes match main. Tool input
constraints/defaults and annotations are unchanged. Repeated static guidance is
shorter in tool descriptions and next_action, with the complete policy provided
at session initialization: once per answer, reuse sufficient current source facts,
and research missing facts, contradictions or nonvalidated results. Both tool
descriptions and the hit response explicitly classify evidence-only derivation as
reuse, so an answer from supplied source reads must not trigger a push. The per-result
untrusted-data warning and saved-answer truth limitation remain explicit.

## Measurements against current main

Main: `202745a92f6c6c59e4c990321be9c5112697594e`. Measured runtime: `93bffe3035e95d9e8c28167b2ba6568bc02950b0`.
Local timing: `93bffe3035e95d9e8c28167b2ba6568bc02950b0`; dependencies are verified unchanged.
Subsequent report-only commits preserve the measured code.

| Metric | Main | Feature | Observed change |
| --- | ---: | ---: | ---: |
| Warm lookup time, 72 queries | 75.415s | 17.138s | 77.3% lower |
| Query embeddings | 87 | 72 | 17.2% fewer |
| Reranked passages | 1,755 | 447 | 74.5% fewer |
| Live subject time, 24 questions | 317.301s | 291.185s | 8.2% lower |
| Native input + output, including cached input | 1,173,949 | 1,154,871 | 1.6% lower |
| Uncached input + output | 234,941 | 209,591 | 10.8% lower |
| Core-fact correct answers | 23/24 | 24/24 | no loss |
| Protocol-violating tasks | 0 | 0 | zero feature violations |
| Research calls | 21 | 18 | actual native calls |

Every seeded retrieval pair matched. The earlier real-history replay matches 23/24
complete responses; the intentional difference rejects unrelated file-validation
evidence and requires research. Fingerprint checks, source permissions, scrubbing,
age limits, two-excerpt and 2,800-character caps remain in force.

## Blocking gates and verification

The analyzer requires all eight: actual imports match pinned revisions; full local
tests/lint pass; local lookup improves; core-fact correctness does not decrease;
feature tool protocol has zero violations; gross native tokens decrease; uncached
input plus output decreases; and summed live subject time decreases. A failed gate
retains the report and exits the analyzer with failure. Regression tests ensure
an uncached increase, equality, extra ask, quality loss or other regression cannot
be offset by improvements in other metrics. Invalid source attempts fail provenance
audit even when later corrected; no failure is waived.

Verification: **99 tests passed, one existing Windows symlink skip**,
Ruff and whitespace checks clean on Python 3.13.1. No hosted CI matrix is configured.
Each server and subject MCP process records actual import paths and source hashes;
all 50 process proofs are audited against the pinned revisions.

Both live stores start empty and learn independently over the same frozen main
corpus. All 24 original questions, outer prompts, model settings, budgets and native
usage accounting are unchanged. The identical research adapter delivers at most six
operations per session and reports remaining calls; denied attempts still count.
All 48 sessions are retained, without task replacements or retries. No local model
benchmark, replay or tests ran concurrently with live timing. Uncached input plus
output is native total input minus cached input plus native output, without estimates.

See [comparison](comparison.json), [local timing](lookup-summary.json),
[native audit](audit-verification.json), [protocol](paired/protocol.json),
[receipt comparison](mcp-payload.json), and [merge review](MERGE_REVIEW.md).

## Limits

This is an explored self-repository regression suite, not held-out production
evidence. Primary-assistant core-fact grading is not independent blind review.
Citation targets were valid for 19/24 main and 16/24 feature
answers; precise line support was not graded. Citation formatting defects remain
visible in retained answers and reviews.

The descriptive family-bootstrap gross-token reduction interval is
[-4.8%,
8.5%]. Sampling,
prompt-cache reuse, independent learned histories and repeated exploration remain
material. This comparison passes the observed gates; it does not establish general
production cost savings or isolate the causal effect of session guidance.

The stricter descriptive guard can reduce recall and fall back to research. Local
timing excludes startup and indexing. All previous failed/invalid trials, plus one
historical interrupted session with unavailable final usage, remain retained.
