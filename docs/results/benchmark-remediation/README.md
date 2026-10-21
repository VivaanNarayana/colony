# Strict gate remediation against main

**Trial 13 passes 5/8 gates. Both token intervals and main tool compliance remain blocking. PR #3 remains draft and must not merge.** Work resumed at the user's request; no next feature or feature branch has started.

Main remains `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`. The current candidate is on `feat/benchmark-independent-evaluation`. Workloads, live prompts, quality rubrics, confidence requirements and all eight mandatory gates are unchanged throughout remediation.

## Current revision

Trial 13 fixes a confirmed excerpt-selection gap: a related descriptive question asked which tools were exposed, but the response grounded only an older connection summary and omitted the Tools paragraph. The agent correctly performed two extra repository reads; the hit was graded irrelevant. When the strongest old-claim paragraphs have strictly worse query coverage than another cited paragraph, source selection now drops the old-claim bonus while retaining the existing query/model weights. Exact-question and procedural claim selection are unchanged. Validation, source access, assertions, age/scope checks, candidate bounds and response limits are unchanged.

The first diagnostic version promoted query keywords and instead selected receipt metadata. That failure remains retained. The corrected version supplies the actual requested tools paragraph from the same current, already-cited file. Independent ledger fixtures cover operations, retention, size facts and keyword-rich metadata decoys; these use no repository answers or mocked retrieval scores.

Full verification: **146 tests pass**, one existing Windows skip; Ruff and whitespace checks pass. The recorded-case reproduction is diagnostic, not independent live gate evidence. The isolated local gate passes with a 15.80% reduction (95% interval +7.52% to +23.88%); the complete 48-session live comparison is retained below. No benchmark script, prompt, rubric or gate has been changed.

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
| 12 | `6567c95` | Evidence rejected: main pushed an ask-derived hash without a new file read |
| 13 | `e14b0cb` | 5/8; all requested tools now supplied; time passes, token intervals and main repeated ask fail |

Each revision has a committed plan before measurement, fresh independent stores over the same frozen main corpus, alternating first arms, and one full 48-session live trial. No identical-code retry was selected for better cache or sampling results. Every failure remains retained, including attempt 4's integrity rejection. A fingerprint matching the file cannot substitute for a required new research read.

### Latest trial: 12 (rejected)

The strict analyzer exits 1 with `main/capture-2.colony: push fingerprint does not match an actual source read`. That baseline push affects later memory. Its hash matching the file does not satisfy the new-research rule, and no live performance result from this trial qualifies. The complete public archive reproduces the exact rejection; no baseline exemption was added.

All 48 sessions, manual labels and raw diagnostics remain retained. Feature `mcp-2` is labelled an irrelevant return: its connection configuration and current reads did not include exposed-tool facts, and the correct final answer required new research. Manual correctness labels do not waive the integrity rejection. See `attempt-12/observations.json` for explicitly nonqualifying raw totals.

### Latest valid comparison: trial 13

| Measurement | Main | Candidate | Reduction interval (95%) |
| --- | ---: | ---: | ---: |
| Native input + output | 1,110,356 | 1,063,883 | -3.81% to +12.99% |
| Uncached input + output | 134,100 | 123,851 | -6.40% to +19.07% |
| Subject wall time | 234.058s | 216.204s | +3.33% to +10.80% |
| Correct requested facts | 24/24 | 24/24 | implementing-assistant labels |
| Valid citation targets | 15/24 | 24/24 | implementing-assistant labels |
| Tool protocol violations | 1 | 0 | unchanged compliance audit |
| Research calls | 17 | 13 | descriptive count |

All three costs declined. Time clears its unchanged interval requirement; both token intervals cross zero. Main repeated ask in capture-2, so the both-arms protocol gate also fails. No baseline exemption applies. All 48 sessions are retained without retries or omissions; public archive replay reproduces the complete comparison and exit 1 exactly. Feature mcp-2 now supplies the actual Tools paragraph and answers in one call with valid evidence. This improvement does not substitute for passing the remaining gates.

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

Trial 13 confirms the omitted-fact fix, while the remaining token intervals still fail. Further changes require a concrete cost cause and a fresh full comparison. All eight remain mandatory. The full independent benchmark contract, scripts and quality rubrics remain unchanged. No identical-code retry is selected for provider/cache luck. Do not merge PR #3 or begin another feature.

## Replay and limits

Each complete attempt contains local rows, workload, source/import proofs, live rows, reviewer labels, verification, checksums and `live-native.zip` with every native event, answer, prompt, stderr, metadata and implementation proof. Private databases, credentials, caches and issuer keys are excluded. Only proof host prefixes become runtime-relative; measured values, revisions and source hashes are unchanged.

```powershell
Expand-Archive docs/results/benchmark-remediation/attempt-13/live-native.zip <fresh-output-dir>
python scripts/analyze_lookup_live.py --results <fresh-output-dir> --local docs/results/benchmark-remediation/attempt-13/lookup-summary.json
```

Expected exit for completed attempts 1–13: **1**. Source commits must be available in Git. Checksum manifests have been checked against committed Git bytes. `attempt-1/prepare-review.py` prepares shuffled answers and citation checks; it does not assign fact correctness. Named Markdown anchors are manually verified against actual headings because the helper only recognizes numeric fragments.

All gates are mandatory, including positive lower paired-family interval bounds, full feature fact/citation correctness and both arms' tool compliance. No gate is advisory. These repeatedly exposed public development tasks, descriptive intervals and primary-assistant grades do not establish held-out production savings or independent approval.
