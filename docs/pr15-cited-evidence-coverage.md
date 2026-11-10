# Cited evidence coverage and unresolved retrieval admission

The retained `42c8585` traces contain two distinct problems. The gateway and worker
useful follow-ups pass the existing answer relevance score but fail the saved-question
similarity gate. Separately, some admitted findings omit the current source text needed
to support their claims. Lowering a similarity cutoff does not establish answer truth
or deliver missing configuration and caller code.

## Shipped correction

Adaptive compact saved-answer selection now keeps complete small cited bundles within
the existing eight-source search ceiling and 2,800-character allowance. Previously it
excluded bundles with more than four files even when their combined text fit. The
retained worker finding has five cited files and 1,339 redacted characters; selection
now preserves its policy, dispatcher, executor, backend and unit documentation together.
The gateway's four-file, 1,032-character selection is unchanged. These are offline
selection previews, not newly admitted hits or measured token savings.

The complete serialized response, including identities, hashes, metadata and coverage,
must still fit its caller's budget. All selected source cores remain atomic. A bundle
that cannot fit may use the existing single, same-finding evidence fallback; it cannot
silently discard a required core and claim to have delivered the whole bundle.

`evidence_scope: "cited_files"` means all cited files are delivered completely.
`evidence_scope: "partial"` means complete coverage of all cited files is not established.
Packing recomputes this
description from the actual delivered reads. Neither value proves that the publication
cited all necessary dependencies or that its prose is correct. Larger bundles retain
bounded source selection and must be assessed for missing evidence by the consumer.

Legacy/no-budget behavior, read-only roles, publication policy, model pins, retrieval
thresholds, freshness, Git compatibility, permissions and full-payload accounting stay
unchanged. No evaluator, workload, oracle, quality label, research allowance or token
accounting change is included.

## Recovery candidate withheld

A fixed offline feasibility pass examined a query-independent operational source
representation as a possible evidence-only recovery signal. It did not establish a
discriminator that accepts the useful retained cases while rejecting misleading
same-topic/query-echo material. The gateway source score remained below the proposed
nonnegative source relevance requirement. The prototype also excluded the worker's
mixed five-file bundle; exclusion is not evidence of a semantic rejection.

Consequently no new recovery route or lower question threshold is shipped. The two
retained question-similarity misses remain unresolved. The independently frozen
recovery acceptance controls are preserved outside the shipped suite, unchanged, with
their positive case still failing; passing negative cases alone would be vacuous.
They are a blocker for a future recovery implementation, not skipped passing tests or
evidence of improved retrieval. A future change needs a supported source relevance
signal tested on independent examples before another threshold proposal.

## Regression and evaluation boundaries

The shipped regression tests cover five, six and eight cited files, reversed source
order, the ninth-source work boundary, legacy selection, real MCP serialization,
exact full-payload accounting, fail-closed small budgets and recomputation of partial
coverage. Existing source validation, source scope, encoding, lifecycle, Git, semantic
control and atomic fallback tests remain applicable. The prior four-file overflow
fixture moves to nine files to test the actual bounded work ceiling; new positive
cases test the newly supported file counts explicitly.

A newly authorized evaluation uses fourteen fresh PR15 Colony sessions only, with
the unchanged merged PR19 `/4` evaluator and audited external single-arm collector.
The existing fourteen No-Colony sessions are immutable shared controls, not new or
copied attempts. The ten reuse sessions are reported separately from four learning
sessions. New correctness, grounding and format reviews are frozen before inspecting
new costs. Failed quality or accounting cannot be waived by lower token usage.
Sequential timing and shared historical controls limit causal conclusions; this is
not a new simultaneous paired campaign. No merge is authorized.
