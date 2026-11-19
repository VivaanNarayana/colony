# Adaptive complete-bundle budget fallback

This follow-up fixes an evidence-availability boundary in PR15. An automatically
selected complete bundle may exceed the final response budget even though relevant
source excerpts fit. The fallback is scoped to adaptive MCP compact responses;
it does not change public schemas, ranking thresholds, budget units/defaults,
publication policy, explicit whole-file requests or the evaluation protocol.

## Token assessment made before implementation

The direction of gross-token impact is not guaranteed. The fallback happens inside
one existing lookup and adds no provider model request or agent-visible tool round
trip. It does perform local embedding/reranking work. It can
save downstream research when its evidence answers the question. It can also cost
more input than the old short miss when the agent still needs the same research.
Local retrieval work, response bytes and local encoding counts are not provider
gross-token measurements or billing estimates.

The previous matched 28-session campaign at `370ecc2` had fourteen Colony asks and no
budget-limited result: eight validated hits, two stale hits and four ordinary
misses. Its complete published bundles contain three files and fit the 2,048-byte
budget. For identical inputs, this fix is expected to leave those responses
unchanged. A fresh unchanged 28-session campaign measures the full feature at the new
revision; it cannot isolate a fallback benefit unless that path is exercised.
No cases, source fixtures, oracle rules, prompts, budgets or quality thresholds
are changed to create a favorable result.

## Required behavior

Try the complete automatic bundle first. Only an actual budget-limited miss from
that selection may try one evidence-only fallback, restricted to the original
finding and its cited sources. Existing evidence relevance rules and current
source/lifecycle/Git checks still apply. The saved answer must be withheld; the
returned source excerpts do not assert complete coverage of the user's question.
The budget receipt remains marked limited. The agent must assess the excerpts and
research gaps under the existing shared policy.

Every selected fallback source unit is indivisible and counted with all source
identities, metadata and the final receipt. If that result cannot fit or cannot
be validated, keep a safe miss. Do not progressively remove required units until
a response fits. Successful ordinary lookups, explicit whole-file requests,
legacy interfaces and non-adaptive requests retain their behavior. The receipt's
limited flag alone must never trigger another lookup.

## Review and evidence boundaries

Focused regressions must demonstrate useful source evidence at the same budget
for the reproduced four-file boundary, retained all-unit failure behavior,
current source binding, lifecycle and permission failures, and unchanged ordinary
hits. A real native campaign includes all 28 attempts and every learning, lookup,
publication, research and failure cost, with cached input included in gross.
Correctness, grounding, format, mechanism and accounting checks remain strict.

The authorized new campaign uses the merged PR17 evaluator with the previously
reviewed PR15 overlay, gpt-6.1-sol/low and the existing authored gateway/worker
horizon. Limits remain 28 attempts, zero retries, 120 seconds/session, 3,600 seconds
collection, 2.3M observed gross stop between sessions, 1 GiB campaign files and at
least 5 GiB free storage. The sampled gross stop is not a hard billing cap.
Historical failures stay attached to their original revisions. Independent
quality labels must be frozen before current costs are opened. No repeat-until-
green run or merge is part of this work.
