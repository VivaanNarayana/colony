> Superseded readiness: the user requires uncached input plus output below main. The retained trial fails that blocking gate (+19.3%). PR is draft; merge review pending a passing rerun. All eight gates are mandatory.

# Merge review

Reviewed runtime: `c40fceb08be3c8de1f8f3a533507b88661fd8152` against `202745a92f6c6c59e4c990321be9c5112697594e`.
Disposition: no blocking findings in the reviewed implementation; all seven
blocking benchmark/verification gates pass. The PR remains unmerged.

The review checked request-local immutable vector reuse, namespace/age filtering,
pre-inference guard equivalence, full-set IDF/tie ordering, independent reranker
logits and the excerpt-score upper bound. Fingerprint checks, scrubbing, two-excerpt
and 2,800-character limits, stale/unverified handling and source permissions remain
in place. The intentional descriptive precision change has positive and negative
regression tests.

The MCP change removes only file proofs already represented by current reads.
Advanced assertions, undisplayed sources and nonvalidated diagnostics are retained.
Focused receipt tests and real stdio integration cover those cases. Input schemas
and reuse instructions are unchanged. The documented MCP `sources` representation
change is the compatibility consideration for direct JSON consumers.

Evidence: 90 tests passed with one existing host symlink skip; lint/whitespace
clean; 72 paired retrieval responses match; all 48 live sessions and actual import
proofs pass audit; feature protocol violations are zero; correctness does not
decrease; observed lookup, live time and gross tokens improve. No hosted CI matrix
is configured. The PR review records the final head check and mergeability status.

This review is by the primary implementing assistant, not an independent approval.
The statistical, corpus and citation limits in README.md remain material. Earlier
failed trial outcomes and the rejected advisory interpretation remain in history.
No other feature has been started or resumed, and no merge was performed.
