# Trial55 — 3/8 strict gates pass; remediation unfinished

Runtime `8380724d8ed678b01cf2f76265ec441626574cec` against freshly fetched main `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`. One fresh local comparison and one full48-session native comparison, followed by one unchanged official analyzer invocation after frozen quality labels. All outcomes and costs retained; no session resampling, identical-code retry, proof repair or main exemption. PR3 remains draft, unmerged, without auto-merge. All eight strict required checks under ruleset25440133 remain active without bypass.

## Change and validation

An existing context read can observe a file change or deletion after successful validation. Previously, the candidate could retain validated/reuse status despite absent or partial current context, and could hide a valid alternative. Strict context reads now propagate that observed contradiction: downgrade the affected source check/status, discard the candidate's entire evidence and continue alternatives. Global source discovery remains tolerant. No extra source reads, cached snapshots or atomic-filesystem guarantee were added. Matching empty/binary sources remain validated without fabricated excerpts; redaction, source paths, hashes, assertions, scopes and freshness limits stay enforced.

Before patch: six new regressions failed, three controls passed. Final expanded suite: **356passed, onefailed, oneexisting Windows skip**; Ruff on src/tests/scripts and whitespace pass. The existing explanatory expiry/deletion-permission false hit remains red. One initial full suite also exposed an instrumented test wrapper not forwarding the new strict option; its signature was corrected without changing assertions. That failed outcome is retained. AstraHigh reviewed the implementation before measurement, finding no blocker; this fixes an observed source-integrity inconsistency, not the known semantic false hit or native performance regressions.

All retrieval models/weights and the gpt-6-luna/low subject remain unchanged. No auxiliary model, scoring/admission cutoff, workload, prompt, rubric, context/research/token/time budget, harness or source/scope/authentication/proof rule changed. MCP forced taint including explicitfalse and encrypted offline queue/retry remain unchanged. No research_paths, fixture hints, answer templates, rejected relevance boosts or unsafe context compaction.

## Eight gates and costs

Pass: loaded implementations, local lookup, correctness. Fail: tests/lint gate due the retained test failure, tool protocol, gross tokens, uncached tokens and live time. Feature gross tokens **+22.46%**, uncached **+32.38%**; elapsed **+0.31%**, with reduction95%CI **−13.78% to +10.51%**, crossing zero. Gross reductionCI−35.79%to−9.14%; uncached reductionCI−57.84%to−10.03%. No isolated native benefit is established by this run.

|Metric|Main|Feature|
|---|---:|---:|
|tokens|1,204,758.000|1,475,302.000|
|uncached_input_plus_output|194,326.000|257,254.000|
|seconds|264.415|265.228|
|research_calls|21.000|30.000|
|tool_calls|52.000|64.000|

Local117/117each;234raw observations audited. Feature warm lookup time34.761724%lower;95%reductionCI+27.392853%to+43.149486%. Warm public-development lookup excludes setup/push/coldstart/concurrency; repeated comparisons are not independent production confirmation.

## Quality, protocol and proof review

Coordinator reviewed all48 shuffled answers/current packets,41 current excerpts against exact frozen bytes/hash/line/redacted prefixes, and17 research/save traces. No new excerpt byte sequences versus history. Selected nonblind Astra review covered R21/R14/R27/R37/R28, not all48 independent blind grading. Core facts24/24both; citations15/24main and24/24feature. All nine invalid main targets remain false, including absolute R27/R37 paths. Initial R21 README96 receipt metadata remains irrelevant; final ask/push names answer the actual question after research. No newly required full signatures or all-family clauses. R14's changed-fingerprint fact is correct; non-Python symbol checks remain unverified, so do not generalize every unsupported symbol check as stale.

Every new-research session saved. All17 actual push attempts,22 fingerprints match actual preceding new reads and frozen corpus bytes; every receipt reports stored/queued. Main seven rows/two encrypted queue entries; feature ten rows/no queue. All actual taints true. Feature explicitfalse/offline handling remains unchanged test coverage, not native exercise here.

Tool protocol still fails: mainR33/scope1 and featureR48/scope1 each received a relevant validated packet, then performed one new server.py read and pushed. The unchanged gate expects zero pushes for relevant reuse. The new reads require saves under the existing research policy; eliminating unnecessary reads is still unresolved. Frozen labels and analyzer outcomes are retained without relabeling or rerun. These are unnecessary research/save sequences, not malformed proof or omitted-save cases. Initial metadata and wording-sensitive misses continue: feature mcp2 three research calls then valid save; stale2 four research calls then save (127209gross,six tools).

Native55gross2,680,060; cumulative50/51/53/54/55gross13,077,278;52native0. All historical failures, attempted saves, costs, labels and manifests1–54 remain byte-unchanged. No credits/reset/account switch/API billing. Fresh allowance2 was explicitly authorized22%to30%; latest observed23%, onepoint increase, reset2026-10-29 17:17:13UTC. Prior allowance/costs remain preserved. Reporting may lag; this is not a hard billing cap. Read usage before new phases; stop at30%, retain incomplete work and update RESUME.

Exact original native events/answers/prompts/labels, separately pinned implementations and frozen corpus are archived in live-native.zip; private databases/identity/queue keys excluded. Development.zip retains all scratch investigations, failed tests and supplemental errors, including a Unicode-decoding diagnostic failure. Public text usesLF; ZIP entries retain original bytes. sha256.json covers all other files. ZIP integrity verified; no offline analyzer replay claimed. Reversible wake requests released.
