# Merge review

Reviewed runtime `93bffe3035e95d9e8c28167b2ba6568bc02950b0` against main `202745a92f6c6c59e4c990321be9c5112697594e`.
Disposition: all eight blocking benchmark/verification gates pass; no blocking
implementation findings. No merge has been performed.

Reviewed request-local immutable query-vector reuse, standalone helper behavior,
namespace/age filtering, eligibility guards before inference, full-set IDF and tie
order, independent reranker logits and the excerpt-score upper bound. Source hashes,
validation, scrubbing and evidence limits remain enforced. Tests cover coherent
descriptive evidence and the intentional rejection of unrelated validation context.

MCP receipt/source fields and per-tool input contracts match main. Static tool
descriptions and next_action are shorter; session initialization carries complete
reuse/research rules, scoped to one answer. It requires assessing source facts and
does not certify saved-answer truth. Source bytes, warnings, fingerprints and
assertions remain intact; receipt and actual stdio integration checks pass.

The benchmark analyzer requires all eight gates and exits with failure while saving
failed reports. Sensitivity tests cover uncached regression/equality and otherwise
improving results. Every invalid source attempt is rejected by provenance audit;
duplicate ask and excess research attempts remain blocking. No advisory exception.

Evidence: 99 tests pass with one existing host skip; lint clean;
72 local pairs match; 48 live sessions and 50 import proofs pass audit; zero feature
protocol violations; correctness does not decrease; lookup, time, gross tokens and
uncached input plus output all improve. Native traces and earlier failures retained.

This review is by the primary implementing assistant, not independent approval.
README.md documents sampling/cache/corpus/citation limitations and the absence of
hosted CI. Final PR-head checks and mergeability are recorded in the PR review.
Other feature work remains paused.
