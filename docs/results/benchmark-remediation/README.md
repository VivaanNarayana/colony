# Strict gate remediation against main

**Trial 11 is in verification. Trial 10 clears five of eight gates: both token intervals and baseline tool compliance remain blocking. PR #3 remains draft and must not merge.** Work resumed at the user's request; no next feature or feature branch has started.

Main remains `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`. The current candidate is on `feat/benchmark-independent-evaluation`. Workloads, live prompts, quality rubrics, confidence requirements and all eight mandatory gates are unchanged throughout remediation.

## Current revision

Trial 11 makes literal-search instructions explicit: use one keyword, broaden after an empty result, read the defining matches and stop once the requested facts have source support. The same operational rule is in session policy and the model-visible ask description. Trial 10 searched `namespace permission` and `permissions` before finding `namespace`, and `memory server` before `server`; those empty searches consumed extra round trips. No path or workload-specific word is hard-coded in production guidance. Budgets, prompts, source permissions and relevance/quality cutoffs are unchanged.

The thinner validated MCP receipt remains: opaque audit IDs and a false age flag are omitted from model text, but the full client/API receipt, namespace, taint, status, recommendation, source bytes, assertions, hashes, citations and trust warnings are preserved. Existing integration expectations were not weakened. A document-introduction ranking prototype was rejected because it boosted unrelated test commands.

Full-suite, isolated local and complete live verification are required for this revision. Results are not cleared by diagnostic receipt sizes or the prior trial's numeric time gate. All eight checks remain blocking until the new 48-session audit is complete.

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

Each revision has a committed plan before measurement, fresh independent stores over the same frozen main corpus, alternating first arms, and one full 48-session live trial. No identical-code retry was selected for better cache or sampling results. Every failure remains retained, including attempt 4's integrity rejection. A fingerprint matching the file cannot substitute for a required new research read.

### Latest completed comparison: trial 10

| Measurement | Main | Candidate | Reduction interval (95%) |
| --- | ---: | ---: | ---: |
| Native input + output | 1,170,326 | 1,089,694 | -5.58% to +22.52% |
| Uncached input + output | 160,278 | 145,566 | -22.39% to +33.68% |
| Subject wall time | 243.845s | 213.602s | +1.90% to +23.69% |
| Correct requested facts | 24/24 | 24/24 | implementing-assistant labels |
| Valid citation targets | 18/24 | 24/24 | implementing-assistant labels |
| Tool protocol violations | 1 | 0 | main repeated ask in capture-1 |
| Research calls | 18 | 15 | descriptive count |

All costs declined, but both token intervals crossed zero. The numeric live-time gate passes; the overall trial remains blocked by the two token gates and main's repeated memory call. That baseline violation was not waived or patched out. All 48 sessions are retained without retries or omissions, and public archive replay reproduces the complete comparison and exit 1 exactly. This candidate passed 142 tests (one Windows skip), Ruff and the isolated local gate before the live run.

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

Trial 11 addresses the empty literal searches observed in trial 10; it follows a concrete policy clarification rather than an identical-code rerun. All eight remain mandatory. The full independent benchmark contract, scripts and quality rubrics remain unchanged. No identical-code retry is selected for provider/cache luck. Do not merge PR #3 or begin another feature.

## Replay and limits

Each complete attempt contains local rows, workload, source/import proofs, live rows, reviewer labels, verification, checksums and `live-native.zip` with every native event, answer, prompt, stderr, metadata and implementation proof. Private databases, credentials, caches and issuer keys are excluded. Only proof host prefixes become runtime-relative; measured values, revisions and source hashes are unchanged.

```powershell
Expand-Archive docs/results/benchmark-remediation/attempt-10/live-native.zip <fresh-output-dir>
python scripts/analyze_lookup_live.py --results <fresh-output-dir> --local docs/results/benchmark-remediation/attempt-10/lookup-summary.json
```

Expected exit for completed attempts 1–10: **1**. Source commits must be available in Git. Checksum manifests have been checked against committed Git bytes. `attempt-1/prepare-review.py` prepares shuffled answers and citation checks; it does not assign fact correctness. Named Markdown anchors are manually verified against actual headings because the helper only recognizes numeric fragments.

All gates are mandatory, including positive lower paired-family interval bounds, full feature fact/citation correctness and both arms' tool compliance. No gate is advisory. These repeatedly exposed public development tasks, descriptive intervals and primary-assistant grades do not establish held-out production savings or independent approval.
