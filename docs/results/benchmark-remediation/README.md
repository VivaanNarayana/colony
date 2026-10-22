# Strict gate remediation against main

**Trial 19 is in verification. Trial 18 passes 6/8 gates; both token intervals remain blocking. PR #3 remains draft and must not merge.** Work resumed at the user's request; no next feature or feature branch has started.

Main remains `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`. The current candidate is on `feat/benchmark-independent-evaluation`. Workloads, live prompts, quality rubrics, confidence requirements and all eight mandatory gates are unchanged throughout remediation.

## Current revision

Trial 19 corrects keyword selection to prefer explicit backticked identifiers, acronyms and camel-case names over generic prose. Their spelling is preserved, including final S in HTTPS/status; longest prose is only a fallback. The retained trial 18 `connect` suggestion caused an extra broad/failed search. Independent RPC/TLS/HTTPS/status fixtures reproduce the defect and verify literal lookup of the named subject without retrieval-score mocks. All scrubbing, miss status, source/access checks and benchmark requirements remain unchanged.

Trial 18 supplies one literal search keyword from the scrubbed question on a miss. It is a suggestion, supplies no facts/source reads, performs no research, and cannot turn a miss into a hit. Longest nonframing identifier/word is selected with singular-prefix normalization; recognized secret patterns are scrubbed before selection. No model package is imported for the hint. Full suite: 162 pass, one existing skip; Ruff/whitespace checks pass. This addresses the actual repeated empty phrase search, while retaining all source/access/age checks, budgets and model cutoffs.

Command-only and equivalent-question ranking diagnostics did not clear the existing cutoff. Those failed diagnostics are retained under attempt 18; neither experiment is shipped and no cutoff is lowered.

Trial 17 makes the miss/nonvalidated response self-contained at the decision to research: one literal keyword, requested facts only, follow dependencies only for unresolved requested facts, then stop at source support and answer concisely. This addresses trial 16’s actual empty phrase search and unnecessary dependency reads. No task-specific keywords, paths, answers, numerical budgets or benchmark criteria enter the runtime guidance. Backend, source validation, all reuse/provenance rules and tool operations are unchanged. A new isolated/full comparison is required.

Trial 16 shares the scoped preflight and answer database connection, avoiding a second sqlite-vec load/WAL setup for populated lookups. The answer transaction commits before evidence fallback; direct helper callers retain their own connection. No model output or absence is cached. All source/access checks and benchmark requirements remain unchanged. Full suite: 155 pass, one existing skip; Ruff/whitespace checks pass. A connection-only diagnostic measured 4.33 ms per setup over 100 connections; this is not gate evidence.

Trial 15 adds a certain-miss fast path before query embedding when no trail exists in the permitted namespaces. The scoped existence query runs fresh on every ask; no absence result is cached. It does not filter by status or age, so stale/expired diagnostics and newly pushed trails remain visible. Existing query reuse, provenance and all gates remain unchanged. Full suite: 155 pass, one existing skip; Ruff and whitespace checks pass.

Trial 14 compacts only complete standalone prose for nonprocedural requests. Conservative guards preserve surrounding context for dependencies, exceptions, redaction fragments, lists, headings and code. Procedure handling, all source proofs, thresholds and budgets are unchanged. Full suite: 153 pass, one existing skip; Ruff and whitespace checks pass. Real-source display diagnostics shrink capture 1063 to 866 characters and outage 483 to 410, with the setup recipe unchanged; these are not live cost measurements. Its isolated comparison passed all source-backed cases but failed the speed interval, so no live trial was started. Initial verification failures and their corrections are retained.

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
| 14 | `a02123a` | Stopped at local speed gate: 9.32% lower time, interval -1.40% to +19.19%; 117/117 facts in both arms, no live sessions |
| 15 | `909b366` | Stopped at local speed gate: 8.33% lower time, interval -2.94% to +21.95%; 117/117 facts in both arms, no live sessions |
| 16 | `898e4b7` | 5/8; local speed restored, quality/compliance pass; all three live performance intervals fail |
| 17 | `b5b2afe` | 6/8; time interval and compliance pass; both token intervals fail |
| 18 | `4e1f784` | 6/8; time/quality/compliance pass; both token intervals fail; hint regression and unnecessary research retained |

Each measured revision has a committed plan and fresh independent stores. Revisions 1–13 and 16–18 completed the same 48-session frozen-corpus comparison with alternating first arms. Revisions 14 and 15 did not advance past their failed local gates; complete local rows and import proofs remain retained. No identical-code retry was selected for better cache or sampling results. Every failure remains retained, including attempt 4's integrity rejection. A fingerprint matching the file cannot substitute for a required new research read.

### Latest trial: 12 (rejected)

The strict analyzer exits 1 with `main/capture-2.colony: push fingerprint does not match an actual source read`. That baseline push affects later memory. Its hash matching the file does not satisfy the new-research rule, and no live performance result from this trial qualifies. The complete public archive reproduces the exact rejection; no baseline exemption was added.

All 48 sessions, manual labels and raw diagnostics remain retained. Feature `mcp-2` is labelled an irrelevant return: its connection configuration and current reads did not include exposed-tool facts, and the correct final answer required new research. Manual correctness labels do not waive the integrity rejection. See `attempt-12/observations.json` for explicitly nonqualifying raw totals.

### Latest valid comparison: trial 18

| Measurement | Main | Candidate | Reduction interval (95%) |
| --- | ---: | ---: | ---: |
| Native input + output | 1,148,044 | 1,126,643 | -16.92% to +17.97% |
| Uncached input + output | 147,212 | 139,123 | -14.21% to +25.13% |
| Subject wall time | 250.273s | 220.796s | +3.47% to +18.83% |
| Correct requested facts | 24/24 | 24/24 | implementing-assistant labels |
| Valid citation targets | 13/24 | 24/24 | implementing-assistant labels |
| Tool protocol violations | 0 | 0 | unchanged compliance audit |
| Research calls | 21 | 17 | descriptive count |

Time clears the unchanged gate; both token intervals remain blocking. Full suite: 162 pass, one existing Windows skip; Ruff clean. Local lookup time fell 22.64%, interval +11.94% to +34.28%, with 117/117 oracle passes per arm. All 48 sessions remain retained; public archive replay exactly reproduces the comparison and exit 1. No citation was waived.

The `connect` hint led to a second search for MCP. The tools question then did unnecessary research even though both the saved summary and current Tools paragraph supplied the answer; that lookup remains labelled relevant. Its extra cost is retained, not relabelled as a missing fact. The feature MCP family used 218,505 tokens versus main's 145,889; no task retry or identical-code rerun is selected.

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

Trial 13 confirms the omitted-fact fix, while the remaining token intervals still fail. Trials 14 and 15 retain local-gate failures; trial 16 restores the local speed gate but still exposes excess miss-side research. Trial 17 clears time, but the two token gates remain blocking; trial 18 supplies a single-word search hint to address the retained empty phrase search. All eight remain mandatory. The full independent benchmark contract, scripts and quality rubrics remain unchanged. No identical-code retry is selected for provider/cache luck. Do not merge PR #3 or begin another feature.

## Replay and limits

Each complete attempt contains local rows, workload, source/import proofs, live rows, reviewer labels, verification, checksums and `live-native.zip` with every native event, answer, prompt, stderr, metadata and implementation proof. Private databases, credentials, caches and issuer keys are excluded. Only proof host prefixes become runtime-relative; measured values, revisions and source hashes are unchanged.

```powershell
Expand-Archive docs/results/benchmark-remediation/attempt-18/live-native.zip <fresh-output-dir>
python scripts/analyze_lookup_live.py --results <fresh-output-dir> --local docs/results/benchmark-remediation/attempt-18/lookup-summary.json
```

Expected live replay exit for completed attempts 1–13 and 16–18: **1**. Attempts 14 and 15 contain local results only and have no live archive; their local speed gates failed. Source commits must be available in Git. Checksum manifests have been checked against committed Git bytes. `attempt-1/prepare-review.py` prepares shuffled answers and citation checks; it does not assign fact correctness. Named Markdown anchors are manually verified against actual headings because the helper only recognizes numeric fragments.

All gates are mandatory, including positive lower paired-family interval bounds, full feature fact/citation correctness and both arms' tool compliance. No gate is advisory. These repeatedly exposed public development tasks, descriptive intervals and primary-assistant grades do not establish held-out production savings or independent approval.
