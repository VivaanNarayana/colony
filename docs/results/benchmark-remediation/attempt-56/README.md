# Trial 56: 3/8 strict gates pass; remediation unfinished

Measured **fec2c119d2964e99e01cf00239caab02625b34bb**, identical runtime source to75b4af3, against freshly fetched main **6fa6826aa4c835001c0d2bc8f3bec838499e5b5d**. One fresh local comparison, one complete48-session native comparison and one unchanged official analyzer invocation after frozen quality labels. Human explicitly requested measurement of actual impact. All sessions, failures, attempted saves and costs remain; no identical-code retry, selective resampling, proof repair or main exemption.

The runtime now includes the actual-source-byte cap, matched Markdown fences/indexv10, targeted source-index recovery with a zero-read completion marker, and durable acknowledgment checks before deleting encrypted retry items. These are reviewed general fixes, documented in checkpoints57/59/62/66. They are not isolated in this comparison, and several paths are not exercised by this native workload. The source corpus remains frozen at6fa6826 in both arms. Saved-answer wording, model sampling and cache variation remain alternative explanations. **This run does not establish that the recent fixes caused the observed differences.**

## Strict results

Pass: loaded implementations, local lookup, live time. Fail: tests/lint, correctness, tool protocol, gross tokens and uncached tokens. The prior complete55 also passed3/8, but correctness passed then and live time failed; the passing set changed.

Feature live time is **8.50% lower**,95%reductionCI **+3.15% to+14.63%**. Gross tokens are **12.08% higher**,95%reductionCI **-25.03% to-0.87%**. Uncached tokens are **2.08% lower**, but95%reductionCI **-21.07% to+17.64%** crosses zero, so that gate fails. No confidence threshold changed.

|Metric|Main|Feature|
|---|---:|---:|
|tokens|1,141,402.000|1,279,227.000|
|uncached_input_plus_output|167,322.000|163,835.000|
|seconds|257.065|235.226|
|research_calls|18.000|22.000|
|tool_calls|49.000|55.000|

Local117/117each; all234observations reconstructed by the unchanged raw audit. Warm lookup time is27.0754%lower,95%reductionCI+18.3674%to+37.5730%. This excludes setup/push/coldstart/concurrency. Repeated public-development comparisons are not independent production confirmation.

Premeasurement full suite: **421passed/1knownwrong-attribute false-hit failure/1existingWindows skip**; Ruff/whitespace pass. That failure is retained with no waiver. All models/weights remain unchanged; subjectgpt-6-luna/low and frozen harness/workload/order/prompts/rubric/oracles/relevance/confidence/context/research/token/time budgets are unchanged. Eight original strict checks under ruleset25440133 remain active, without bypass; PR3 remains draft/unmerged/noauto.

## Quality and protocol review

Coordinator reviewed all48 shuffled answers/packets and15research/save traces;41current excerpts were checked against exact frozen bytes, SHA256, source line and scrubbed prefix. Three newly displayed contexts are retained. Selected nonblind Astra review covered R03/R14/R21/R25/R29/R31/R33/R48; it was not independent blind grading of all48.

Core facts24/24both. Citations **19/24main,23/24feature**. FeatureR29 uses a named README heading anchor, which the unchanged citation oracle rejects. All five invalid main citations remain false. Do not repair links or exempt a plausible named anchor. Bare README references retain the same existing acceptance rule.

R21's initial README96 metadata remains irrelevant to the actual tool-name question; its final answer after research is sufficient. Feature useful hits16versus main19; research-free correct15versus18. Feature research calls fell30→22versus55, and stale2 now reuses a sufficient answer in one ask. These observations are not causal proof: generated saved wording differs. Metadata selection and wording sensitivity remain unresolved.

Protocol violations: mainR31/capture1 asks twice despite sufficient reads, retaining both calls/costs. FeatureR25/outage2 receives a relevant saved answer explicitly describing queued pushes, then reads client.py and saves. Its displayed source covers ask rather than push queuing, so do not claim current displayed text already proved the saved fact. Apply the original relevant-packet protocol calculation unchanged; do not relabel the packet to avoid failure. Actual new reads correctly precede the save. Neither currentR33norR48 researches/pushes; the initial review brief accidentally carried prior55 claims and was corrected before labels/analyzer. Motivation for extra reads is unobserved.

All15push attempts/16fingerprints match actual preceding new reads and frozen corpus bytes; all receipts report stored/queued. Main receipts3stored/3queued, finalDB6rows/3queue entries; queued receipts can overlap durableDBrows after lost responses. Feature9stored receipts/9rows/noqueue. **MainR03 explicitly sets tainted:false and stores false**; preserve this baseline finding separately from proof validity. All actual feature taints are true. Feature explicitfalse/offline enforcement remains test coverage, not exercised by this run. No taint safeguard was weakened.

Native gross2,420,629; cumulative50/51/53/54/55/56gross15,497,907;52native0. Every prior outcome/cost/label/manifest1–55 and checkpoints56–66 remains unchanged. Shared allowance3 baseline16%used, latest19%, stop21%; reset2026-10-30 17:40:50UTC. All coordinator/workers/reviews/subjects share it. Prior costs retained; reporting may lag. No reset/credit purchase/account switch/API-billing bypass.

Exact native events/prompts/answers/labels, pinned implementations and frozen corpus are archived in live-native.zip, excluding private databases/identity/storage/queue keys. Development.zip preserves preflight, review, audit scripts/logs and corrections. Public text normalizesLF; ZIP entries retain original bytes. Frozen-label hashes address `live-native.zip/results/{main,feature}/reviews.json`; LF-normalized public copies have separate publication hashes in label-hash-scopes.json. All archives/manifests verified; no offline analyzer replay claimed. Wake requests were released. See [RESUME](../RESUME.txt). No further identical-runtime comparison is authorized.
