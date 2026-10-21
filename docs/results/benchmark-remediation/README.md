# Strict gate remediation against main

**Trial 12 is in verification. Trial 11 clears five of eight gates; all three live performance intervals remain blocking. PR #3 remains draft and must not merge.** Work resumed at the user's request; no next feature or feature branch has started.

Main remains `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`. The current candidate is on `feat/benchmark-independent-evaluation`. Workloads, live prompts, quality rubrics, confidence requirements and all eight mandatory gates are unchanged throughout remediation.

## Current revision

Trial 12 scopes memory writing to a concise answer to the exact question, based on new external research. The tool explicitly discourages expanding research merely to fill memory. In trial 11 the feature read the server's permission checks and then a helper to expand signature details; main answered the namespace question from the defining server file alone. This is a concrete hypothesis about extra source/model input, not an asserted saving.

Operational research rules remain in the registered ask description and are no longer duplicated in initialization. Direct paths, one keyword per literal search, broadening empty searches, reading defining matches and stopping at source-supported requested facts remain explicit. Initialization retains every shared trust/reuse rule, and misses retain the citation reminder. Source permissions, actual bytes, hashes, assertions, citations, namespace, status, taint and reuse recommendation remain unchanged. Full client/API receipts retain all audit metadata.

Full verification: **142 tests pass**, one existing Windows skip; Ruff and whitespace checks pass. The final registered-tool policy check also passes with a whitespace-insensitive assertion that still requires **one** keyword. The isolated local and complete live comparisons remain required for this revision. All eight gates remain mandatory.

## Retained completed trials

| Attempt | Measured candidate | Outcome |
| --- | --- | --- |
| 1 | `b2fb0b7` | 6/8; uncached cost and live-time intervals fail |
| 2 | `0f841fa` | 4/8; two incorrect command answers and all live performance gates fail |
| 3 | `035b996` | 5/8; quality restored, all live performance gates fail |
| 4 | `8e9ff1b` | Evidence rejected: push used memory reads rather than new research |
| 5 | `08e8ec4` | 6/8; both token intervals fail; time interval passes |
| 6 | `fe8e2d7` | 5/8; all three live performance intervals fail |
| 7 | `3574a88` | 4/8; one invalid feature citation and all live performance intervals fail |
| 8 | `21bc30c` | 5/8; facts/citations restored, all live performance intervals fail |
| 9 | `a997d98` | 5/8; cross-encoder padding reduced, all live performance intervals still fail |
| 10 | `f9f16e5` | 5/8; time interval passes, both token intervals and main tool compliance fail |
| 11 | `f4e423e` | 5/8; compliance passes, all three live performance intervals fail |

Each revision has a committed plan before measurement, fresh independent stores over the same frozen main corpus, alternating first arms, and one full 48-session live trial. No identical-code retry was selected for better cache or sampling results. Every failure remains retained, including attempt 4's integrity rejection. A fingerprint matching the file cannot substitute for a required new research read.

### Latest completed comparison: trial 11

| Measurement | Main | Candidate | Reduction interval (95%) |
| --- | ---: | ---: | ---: |
| Native input + output | 1,133,073 | 1,067,592 | -4.18% to +15.97% |
| Uncached input + output | 142,353 | 137,160 | -13.48% to +17.30% |
| Subject wall time | 238.317s | 224.570s | -6.23% to +16.10% |
| Correct requested facts | 24/24 | 24/24 | implementing-assistant labels |
| Valid citation targets | 15/24 | 24/24 | implementing-assistant labels |
| Tool protocol violations | 0 | 0 | unchanged compliance audit |
| Research calls | 18 | 12 | descriptive count |

All costs declined, but all three performance intervals crossed zero and remain blocking. All 48 sessions are retained without retries or omissions, and public archive replay reproduces the complete comparison and exit 1 exactly. This candidate passed the full suite, Ruff and isolated local gate before the live trial. The reduced number of research calls is not a substitute for passing the cost/time intervals.

## Runtime changes retained

- Skip neural ranking only where a sole eligible candidate cannot change selection; preserve validation, scope and age checks.
- Return canonical relative citations and bounded original source text, retaining complete command fences and adjacent recipe prerequisites.
- Avoid ONNX padding waste with individual source embedding and cross-encoder inference; real-model checks preserve vector values, logits and order. The 14-passage diagnostic fell from 1.12s to 0.44s; this is not live gate evidence.
- Focus nonprocedural text-document ranking on coherent neighboring sentences; retain full procedure inputs and existing coverage, coherence and model cutoffs.
- Normalize availability and queued-request paraphrases while preserving substantive query subjects and credential/token uses of “get.”
- Omit duplicate plain file identities already present in current reads; preserve assertions, undisplayed identities, warnings and hashes.
- Explicitly classify evidence-only derivation as memory reuse even for a new question; prohibit pushing or rereading solely to push it.
- Compact duplicated MCP session policy. Reuse valid v7 indexes: passage inputs match main exactly after the experimental positional-query mask was reverted; experimental v8/v9 stores still rebuild by version mismatch.

The positional-query masking experiment caused research misses and was reverted. Procedure focusing also selected unrelated provisioning prose; full procedure paragraphs are restored. Those failed outcomes are retained rather than hidden by the later fixes. The separate source-cache prototype is untouched.

## Current work

Trial 12 follows trial 11's extra implementation-detail research with question-scoped memory writing and deduplicated policy delivery. It is a concrete revision, not an identical-code rerun. All eight remain mandatory. The full independent benchmark contract, scripts and quality rubrics remain unchanged. No identical-code retry is selected for provider/cache luck. Do not merge PR #3 or begin another feature.

## Replay and limits

Each complete attempt contains local rows, workload, source/import proofs, live rows, reviewer labels, verification, checksums and `live-native.zip` with every native event, answer, prompt, stderr, metadata and implementation proof. Private databases, credentials, caches and issuer keys are excluded. Only proof host prefixes become runtime-relative; measured values, revisions and source hashes are unchanged.

```powershell
Expand-Archive docs/results/benchmark-remediation/attempt-11/live-native.zip <fresh-output-dir>
python scripts/analyze_lookup_live.py --results <fresh-output-dir> --local docs/results/benchmark-remediation/attempt-11/lookup-summary.json
```

Expected exit for completed attempts 1–11: **1**. Source commits must be available in Git. Checksum manifests have been checked against committed Git bytes. `attempt-1/prepare-review.py` prepares shuffled answers and citation checks; it does not assign fact correctness. Named Markdown anchors are manually verified against actual headings because the helper only recognizes numeric fragments.

All gates are mandatory, including positive lower paired-family interval bounds, full feature fact/citation correctness and both arms' tool compliance. No gate is advisory. These repeatedly exposed public development tasks, descriptive intervals and primary-assistant grades do not establish held-out production savings or independent approval.
