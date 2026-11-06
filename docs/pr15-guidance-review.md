# Adaptive guidance: conditional implementation decision

This review applies the conditional guidance plan to PR15 after evaluator PR19
merged at `7483df10cd616e78c1fcbbdc415f9b3e0d017705`. It retains the production
policy at `f65299b12e6e777a55e4a735852d8b75bd34ced1` and adds verification controls.
No product behavior, evaluator, benchmark, acceptance threshold or historical result
is changed. PR15 remains draft; its token-evidence requirement remains unsatisfied.

## Why the policy stays unchanged

The eight retained failures comprise five missing-observed-evidence cases and three
inference/scope errors despite source reads, across five No-Colony and three Colony
answers. Their labels and traces are unchanged. The natural run made 13 Colony asks,
all misses, and no publications. These failures do not demonstrate a defect in a
retrieved-hit policy or establish that a larger instruction block would help.

The current adaptive policy already requires verification of current repository
reads and provenance, actual source coordinates, investigation of gaps and
contradictions, and a distinction between file hashes and answer truth. Setup and
MCP initialization must each deliver this policy independently. Read-only and
publication restrictions must not be removed to offset added wording.

A proposed clarification about observed evidence, dependencies and unverified
assumptions did not establish a nonredundant benefit: several failed answers already
qualified assumptions while drawing incorrect or incomplete conclusions. One
candidate lookup block increased from 754 to 760 UTF-8 bytes and from 139 to 142
tokens under the pinned offline `o200k_base` tokenizer. Independent checks of other
rewrites, serialized MCP instructions and managed setup framing likewise found no
tested candidate that met the nonincrease condition while preserving all policy
requirements. This is not a claim that every possible rewrite is worse.

These are local text measurements, not provider gross-token estimates. No wording
candidate is shipped, so the existing runtime, prompts, tool catalogs and their
costs remain unchanged. There is no new accuracy or savings claim.

## Added controls

`tests/test_workflow_policy_delivery.py` launches the actual command emitted by
setup, initializes MCP over stdio, and checks the delivered policy for adaptive
and legacy writable/read-only configurations. It checks independent policy delivery,
the bounded routine tool set, and read-only publication rejection. These checks
prove delivery and compatibility, not model adherence.

`tests/test_workflow_semantic_controls.py` contains small portable source-review
models with varying limits, delays and injected implementations. Exact invocation,
send and wait traces establish that an uncaught dependency exception prevents outer
retry, a concrete implementation's bound does not constrain an unspecified injected
callable, and the final permitted send can raise after the full send count. Terminal
results and last-result variations are positive controls. Deliberate mutations to
catching, wiring, termination and waiting must fail these controls.

The semantic models calibrate independent review; they do not exercise or fix a
Colony runtime defect. They import no canonical benchmark fixtures, supply no
answers to agent prompts or memory, and do not replace the frozen correctness,
grounding, format or mechanism checks. No test awards credit merely for calling
Colony or mentioning an expected marker.

## Current measurement decision

Use the merged PR19 `/4` evaluator through a separately verified export for future
work. Keep that code outside this child branch. Earlier `/2` and `/3` evidence must
keep its original collector and cannot be relabeled, regraded or pooled with `/4`.
The prior natural totals remain 888,227 gross tokens for No-Colony and 1,006,188 for
Colony (+13.28%), with both arms failing the strict all-case quality requirement.

No new native campaign is warranted by these offline controls alone. A future
separately authorized diagnostic must have fixed cases, revisions, limits and
stopping criteria before collection. If a specific residual policy gap motivates a
candidate, compare it with unchanged PR15 under the same evaluator. Keep natural
publication optional and controlled mechanisms separate. Retain all attempts,
learning, publication, failures and unknown costs; do not repeat until favorable.

Only a justified candidate should proceed to the fresh 28-session matched evaluation.
Freeze independent quality judgments before inspecting costs. Improved accuracy
cannot waive a gross-token increase, and lower tokens cannot waive a quality failure.
Local representation savings, passing tests and evaluator validity do not satisfy
PR15's token-savings gate.

For any later concrete implementation issue, first add a regression that reproduces
the behavior, make the smallest in-scope fix, and retain the rejection and compatibility
controls. Evaluator defects belong in a separate evaluator change; product changes
stay on `feat/adaptive-context-low-overhead` and do not merge or rewrite its parent.

Audit artifacts: `D:/Codex/colony/pr15-guidance-review-f65299b/`. Historical failure
diagnosis remains in `D:/Codex/colony/pr15-accuracy-diagnosis-f65299b/` and the original
run in `D:/Codex/colony/pr15-pr18-natural28-f65299b/`.
