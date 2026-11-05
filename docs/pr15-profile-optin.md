# Compact-response opt-in boundary

An unsolicited `colony.mcp-compact/1` marker could previously change a
repository-verified client's ordinary evidence handling into whole-file handling.
The caller did not need to request a context budget, compact responses or complete
files. The reproduced response contained 7,946 bytes of locally verified file
text where the parent implementation rejected the same oversized hint. Source
hashes were still checked; this was an opt-in and response-size regression, not a
demonstrated provenance or permission bypass.

The client now accepts a non-null response profile only when it matches the
profile explicitly requested and advertised during capability negotiation.
Requesting compact mode from a server that does not advertise it still uses the
bounded legacy fallback, but that server cannot opt the client in by returning a
compact marker. Missing or mismatched negotiated markers fail closed. An absent
or null marker retains the ordinary legacy meaning. Direct Git verification also
rejects unknown profile markers and compact responses without a context budget.
Valid negotiated compact responses retain the complete budget, source-identity
and caller-worktree verification checks.

## Token-impact boundary

This fix prevents unexpected response expansion from a misbehaving server. It
does not add a provider request or change normal ranking, publication or routing.
The last 28-session campaign at `210254f` negotiated compact mode correctly, so
this defect did not explain its 10,044-token gross increase. Those results remain
attached to that revision and their original evaluator; they are not regraded.

The earlier complete-bundle budget fallback is a different code path. It can
replace a short miss with larger useful evidence, which may or may not displace
enough downstream research to save tokens. Tests using the pinned offline
embedding and ranking models supplement the deterministic transport and safety
regressions. Local response counts, ONNX computation and passing tests are not
native gross-token savings evidence.

## Prospective measurement plan

1. Keep the existing 28-session evidence immutable. Use the merged PR18 evaluator
   through a separately verified read-only export, not by merging evaluator code
   into this child branch. Future manifests must use its prospective `/3` protocol.
2. For normal-workflow costs, explicitly select the natural track. Run controlled
   publication/reuse diagnostics separately and retain all required mechanism
   checks and forced learning costs. Do not pool tracks or compare an old `/2`
   percentage with a new `/3` percentage as a product-only effect.
3. Freeze the product commit, installed package, evaluator, model/client settings,
   source snapshots, shared path hints, output contract, fixed horizon and resource
   limits before any newly authorized native collection. Keep correctness,
   grounding, format, accounting and tool-policy checks strict for both arms.
   Preserve every attempt and freeze independent quality labels before costs.
4. A fresh unchanged 28-session Colony/no-Colony comparison can measure the whole
   feature at its new revision. It cannot isolate this compatibility guard, and
   it cannot establish a fallback benefit unless the fallback is actually used.
   To investigate the fallback itself, first predeclare a separate boundary-case
   diagnostic covering useful evidence, insufficient evidence, non-fitting
   evidence and ordinary fitting responses. Never replace difficult benchmark
   cases with favorable boundary examples after observing costs.
5. Causal before/after claims require the old and new product versions to be run
   under the same new evaluator, with matched work and all baseline/learning/
   publication/research/failure costs retained. Decide the finite schedule and
   limits before results; do not repeat until savings appear. Unknown provider
   request boundaries or serialization costs remain unknown.

No native campaign or token-improvement claim is part of this compatibility fix.
PR15 remains a child of `feat/adaptive-context-budgets`; its savings requirement
is not satisfied by the evaluator merge or these offline checks.
