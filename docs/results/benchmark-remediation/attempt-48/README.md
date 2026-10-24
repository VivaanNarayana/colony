# Tests-only taint and efficiency checkpoint 48

Source `2261d26fa76ef0e0a5e71356490a6233d537fdc0` on `feat/benchmark-independent-evaluation`. Full suite ran once: **300 passed, one existing Windows symlink skip**, 85.445s JUnit duration. Final focused suite: **148 passed**. Ruff lint and whitespace checks pass. Fifteen new regression cases are included. **No benchmark, profile or analyzer replay ran. The three performance failures have not been demonstrated fixed.**

## General changes

The MCP push boundary always creates a tainted trail, including when its caller explicitly supplies `tainted:false`. This happens before the ordinary client scrubbing, HTTP request or encrypted offline queue. A corrected receipt reports `tainted:true` without hiding save failures or changing the client audit. The MCP tool admits researched content; trusted authored CLI/HTTP trails retain their existing explicit taint choice. Tests cover real storage, scrubbing, encrypted queueing and retry as well as failed/inconsistent receipts.

The complete research, completeness, proof-copying, save-status, taint, trust and citation policy remains in MCP initialization. Receipts retain their immediate reuse/research/save-status decision and a session-policy reference, avoiding a second full copy of that static policy. Actual source text, fingerprints, citations, validation/trust fields and warnings are unchanged. Tests verify the full policy in the SDK's initialization options on miss, stale, unverified and validated paths. Existing policy tests now check its initialized placement rather than duplicate verbatim placement; no benchmark protocol requirement was changed. A future benchmark must still test live adherence.

One identity-scoped HTTP pool lasts for an MCP server session and closes on normal shutdown or failure. Requests retain the same credentials, payloads, five-second timeout, outage/queue behavior and disabled redirects. A real HTTP/1.1 test observes two requests on one TCP connection, while lifecycle tests check error propagation, cleanup and no redirect credential forwarding. CLI callers keep the existing one-request transport behavior.

Reranking computes identical passages only once within each invocation, using the same model, question, batch size one and exact passage bytes, then scatters scores to their original order. There is no persistent source/score cache. The existing real-model logit/order equivalence case includes duplicates and passes. For a saved nonliteral descriptive answer whose score is nonpositive, the question-only control cannot make it pass the existing positive-and-strictly-better rule; that unnecessary inference is skipped. All existing hit/miss decisions remain asserted, including ties, negative scores, unrelated attributes, scope and changed sources. Models, cutoffs, index versions, candidate pools, context budgets and source-admission rules are unchanged.

These are implementation changes intended to reduce receipt and execution costs. Unit work-count/connection checks prove the targeted work was removed; they do not establish native-token or confidence-interval improvements. The taint fix can add a correction field to a receipt. More compact guidance can affect model behavior. All three performance gates and the live correctness/protocol gates need new evidence in a separately authorized future run.

## Retained test phases

| Phase | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| red | 2 | 9 | 0 |
| focused | 107 | 7 | 0 |
| focused2 | 148 | 0 | 0 |
| full | 300 | 0 | 1 |

The red run exposed taint, duplicate-policy and duplicate-inference behavior. The first focused run caught a missing literal repository-relative citation reminder in the shortened miss guidance. That production reminder was restored; the seven existing assertions were not relaxed. Two additional receipt-failure cases were then added. All original phase stdout, JUnit, exit, wake and static-check bytes are preserved in `raw-test-logs.zip`, verified by `raw-test-logs-sha256.json`. The two failed phases are stored in the archive rather than altering traceback whitespace for Git. Passing loose logs are UTF-8/LF publication copies. Earlier uncommitted intermediate source variants were not separately archived; final committed source and tests are hashed.

Every trial through 47, including the 48 native sessions, failed performance intervals and R38 taint deviation, remains byte-unchanged. Trial 47's **5/8** is historical for its measured source, not a verdict on this changed runtime. There were no identical-code benchmark retries, extra sessions or outcome exclusions.

## Blocking gates and pause

Only tests/lint is green for this source. All **seven benchmark-dependent checks are pending and blocking**, including the three performance gates. No old benchmark green is carried onto changed code and no new eight-gate score is asserted. All eight original checks remain strict requirements under active main ruleset 25440133, with no bypass. The frozen benchmark scripts, both-arm protocol, oracles, prompts, workload, budgets, statistics and thresholds are unchanged.

Main was freshly fetched as `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d` for provenance only; it was not benchmarked this turn. Canonical source/harness/model hashes and required-rule snapshots are retained. No separate test-process imported-module proof was collected. All temporary wake requests were released, and persistent power settings did not change. `sha256.json` covers every other checkpoint file using its published bytes. PR #3 remains draft, unmerged and without auto-merge. Pause after publication; no further work or run is scheduled.
