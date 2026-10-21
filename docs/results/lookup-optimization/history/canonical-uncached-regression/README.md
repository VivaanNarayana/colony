# Lookup optimization: completed review evidence

All seven blocking gates pass, including attempted-call compliance. The first
feature is ready for maintainer review and merge; it has not been merged. Other
feature work remains paused. The earlier advisory compliance judgment was wrong
and is superseded; failed trials remain in [history](history/).

The runtime shares one query vector across lookup stages, skips candidates that
cannot pass eligibility guards, and prunes excerpt scoring with safe bounds.
Descriptive evidence requires coherent local query-term support. The MCP response
now delivers ordinary validated file proofs once in current repository reads;
assertions and sources without displayed reads remain explicit. All source text,
validation, input schemas and reuse instructions are preserved.

## Measurements

Main is `202745a92f6c6c59e4c990321be9c5112697594e`. The live feature revision is
`c40fceb08be3c8de1f8f3a533507b88661fd8152`. Local timing used `956dde433ba9ace3101e3e53e1a18d9ff318637d`; the analyzer verifies unchanged Store dependencies. Report-only commits preserve the tested implementation.

| Metric | Main | Feature | Observed change |
| --- | ---: | ---: | ---: |
| Warm lookup time, 72 queries | 112.049s | 30.157s | 73.1% lower |
| Query embeddings | 87 | 72 | 17.2% fewer |
| Reranked passages | 1,788 | 486 | 72.8% fewer |
| Live subject time, 24 questions | 392.387s | 323.627s | 17.5% lower |
| Native input + output tokens, including cached input | 1,310,508 | 1,294,381 | 1.2% lower |
| Uncached input + output tokens | 183,724 | 219,181 | 19.3% higher |
| Core-fact correct answers | 23/24 | 24/24 | no loss |
| Protocol-violating tasks | 0 | 0 | feature gate passes |

All 72 seeded retrieval pairs matched. The earlier real-history replay matched
23/24 complete responses; the intentional difference rejects unrelated validation
evidence. Store dependencies are unchanged by the receipt representation change.
The representative validated MCP receipt shrank from 958 to 859 UTF-8 bytes with
identical factual information; this byte measurement is not an LLM-token estimate.

Both live stores start empty and learn independently over the same frozen main
corpus. All original questions, outer prompts, GPT-6 Luna low settings and native
usage accounting are retained. Each server and subject MCP process records actual
import paths and source hashes; the audit verifies all 50 process proofs. All 48
sessions are retained, without per-task replacements or retries. No local tests
or model benchmarks ran concurrently with live timing.

The identical shared research adapter reports a six-call remaining budget and an
exact source object for pushes. Rejected excess attempts still count as violations.
Pushed hashes must match preceding reads and the frozen corpus; lookup question
punctuation is checked exactly. The unsuccessful complete-file guidance experiment
was reverted. No gate was waived to qualify this result.

## Verification and limitations

Verification: **90 tests passed, one host symlink permission skip**, Ruff and
whitespace checks pass on Windows/Python 3.13.1. No hosted CI matrix is configured.
See [comparison](comparison.json), [local timing](lookup-summary.json),
[receipt bytes](mcp-payload.json), [native audit](audit-verification.json),
[import proofs](paired/feature/loaded-implementations.json), and
[merge review](MERGE_REVIEW.md).

This is an explored self-repository regression suite, not held-out production
evidence. Primary-assistant grading checks core facts; it is not independent blind
review. Citation targets were valid for 19/24 main and
19/24 feature answers; precise citation lines were not
graded. Formatting defects remain visible in the answer reviews. The descriptive
family-bootstrap token-reduction interval is
[-15.0%,
12.3%]; repeated
exploration and provider variation prevent a general token-savings claim.
Uncached input plus output increased 19.3%; cache-sensitive work did not improve
in this sample. The gross reduction includes cold-research sampling and independent
memory histories; it does not isolate the receipt byte reduction. No production
cost or uncached-compute saving is claimed.

The stricter descriptive filter can reduce recall and fall back to research.
MCP consumers parsing `sources` must also accept ordinary file identities from
`current_repository_reads` (`kind`, `path`, `sha256`); CLI/HTTP receipts are unchanged.
Assertions and undisplayed sources retain their original metadata. Local timing
excludes startup/indexing. All failed and invalid trials, plus one historical
interrupted session with no final usage, remain retained.
