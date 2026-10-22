# Strict gate remediation against main

**Trial 25 preparation in progress on the same branch.** All four findings have fixes and regression tests; completed trials 23 and 24 each pass 5/8. Trial 24 has complete retained evidence and an exact hardened public replay; all three live confidence intervals still fail. All eight benchmark statuses are now required by main's active GitHub ruleset with strict up-to-date enforcement. Historical trial 22 remains retained; PR #3 stays draft and unmerged.

Main remains `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`. The current candidate is on `feat/benchmark-independent-evaluation`. Workloads, live prompts, quality rubrics, confidence requirements and all eight mandatory gates are unchanged throughout remediation.

## Current revision

Trial 25 plans an explicit per-read missing-fact check and requested-facts-only capture policy, addressing retained dependency overresearch. Four independent one-shot native behavioral fixtures passed, including two cases where dependencies are necessary; traces, plan and runner are retained. These do not establish performance improvement. Sorted-length inference batching was slower and rejected. Trial 24 below remains the latest completed benchmark; all eight gates remain blocking.

Trial 24 (`6031c44`) completed all 48 sessions against freshly fetched main `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d` and passes **5/8**. Full suite: **214 pass**, one existing Windows skip; Ruff and whitespace checks pass. Both arms have 24/24 correct facts and zero protocol violations; citations are main 18/24, feature 24/24. Local lookup has 117/117 oracle passes per arm, 21.528818% lower time, interval +11.337512% to +33.064323%; all 234 raw rows pass independent reconstruction.

| Measurement | Main | Candidate | 95% reduction interval |
| --- | ---: | ---: | --- |
| Native input + output | 1,114,021 | 1,066,429 | -4.694647% to +12.401369% |
| Uncached input + output | 186,533 | 170,429 | -15.417096% to +28.942590% |
| Subject wall seconds | 268.672 | 238.970 | -1.981681% to +20.628403% |

All three live interval gates remain **blocking failures** despite lower totals. Main/feature research calls are 15/12, tool calls 45/41, asks 24/24. The operation-context hint removed the extra validation search, but feature scope-2 still researched more dependencies than main; feature outage-3 also had higher native usage despite only one ask. Every cost remains included. The complete public archive reproduces identical comparison JSON and exit 1 under the strengthened verifier. No baseline exemption, identical-source retry, task retry or outcome exclusion occurred.

Follow-up diagnostics are retained in `attempt-24/next-step-diagnostic.json`. Choosing the final property noun for passive questions was rejected because it regresses the existing independent bucket-access defining-source example. The actual author-written introduction plus the full startup recipe remains below the unchanged model cutoff. Neither candidate was implemented or benchmarked. Earlier unsafe document-introduction boosting remains rejected. No follow-up source revision has been selected; the request to clear all eight gates is **unfinished**.

Trial 23 (`6b7c6ae`) completed all 48 sessions and passes 5/8. Facts are 24/24 per arm, citations main 17/24 and feature 24/24. Main capture-1 makes two identical relevant asks: both remain counted and the both-arm protocol gate fails (main 25 asks versus feature 24). Gross tokens 1,164,225 versus 1,041,762, reduction interval -0.552886% to +20.449537%; uncached 176,833 versus 166,114, interval -17.390335% to +31.853820%; both fail. Live seconds 286.069 versus 251.850, interval +0.827324% to +21.585302%, passes. Complete failed results and exact hardened public replay exit 1 remain in attempt-23. The broad file hint caused an extra validation search and remains cost. Trial 24 addresses explicit operation-context selection with independent positive/negative hint fixtures; no baseline protocol failure is waived.


Trial 23 fixes exponential backtracking in keyword hints, preserves adjacent prose qualifications, requires researched-miss pushes in both arms, and recomputes the local gate from the canonical workload and raw rows. The analyzer/local verifier changes strengthen acceptance; all existing numerical gates, workload, prompts, rubrics, model snapshots, score weights, cutoffs and budgets remain unchanged. Regression suite/full verification: 207 pass, one existing Windows skip; Ruff/whitespace clean. The initial 206-pass/one-failure verification and fixture correction are retained. Main's ruleset snapshots are retained in attempt-23. Trial 22 still replays to identical JSON/exit 0 through the hardened verifier, but its measured source is historical and does not validate the new source.


Trial 22 (`b6691a859d3c82b6658ceb7624214745d7758bbc`) clears all eight unchanged blocking gates against freshly fetched main `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`. It adds conservative question-only command/subject/object hints to the eligible descriptive-field ordering from trial 21. Independent inspect/reconcile/bucket/archive/worker/daemon fixtures reproduce the longest-word defect, with ambiguous and scrubbed negative cases. No repository path, subject, answer, benchmark criteria, model, score weight, cutoff, budget, or source/access validation is changed.

Full suite: **184 pass**, one existing Windows symlink skip; Ruff/whitespace checks pass. Whole-package isolated local comparison: **117/117 oracle checks per arm**, main 2.698998199950438s, feature 2.061513399705291s, **23.619312% lower time**, 95% interval **+14.161378% to +34.239363%**. Verification, local and live use the same measured source revision. All 48 live answers and source packets were reviewed, including current packet text identical to previously reviewed source bytes; actual new research reads were checked before every push. Both arms have zero protocol violations. Main facts 24/24 and citations 16/24; feature facts and citations 24/24 each. An invalid en-dash numeric fragment and root/host-absolute targets remain invalid under the unchanged rubric.

| Measurement | Main | Candidate | Reduction | 95% reduction interval |
| --- | ---: | ---: | ---: | --- |
| Native input + output | 1,189,867 | 1,072,264 | 9.883710% | +1.219991% to +19.790718% |
| Uncached input + output | 186,987 | 161,032 | 13.880644% | +5.614520% to +20.838154% |
| Subject wall seconds | 270.637 | 239.554 | 11.485126% | +4.427843% to +17.692149% |

The live run has 24 asks per arm, 20 main versus 13 feature research calls, and 50 versus 42 tool calls. Main relevant/raw hits are 18/18; feature 19/19. Feature research-free correct answers are 18 because scope-1 performs an extra read despite a relevant hit. The broad `file` hint also causes a second validation search; the stale family uses more gross and uncached tokens, installation uncached cost is higher, and scope/tests time is worse. All costs remain included. Positive aggregate interval bounds do not imply every family improves.

The complete exported archive replays to **identical comparison JSON and exit 0**, including all eight passes. Attempt 21 remains a complete failed trial with exact public replay exit 1. Attempt 20 remains diagnostic-only. No identical-code retry, dropped outcome, task retry, gate waiver, merge, new feature or new branch occurred. This meets the public development regression contract; repeatedly exposed tasks and implementing-assistant labels do not establish held-out production savings or independent approval.


Trial 21 (`c33ef3785949ec5310bdfeaa91fb9195d6449460`) prioritizes an explicit named descriptive field only inside the existing eligible excerpt window. Independent real-model Operations/Modes/Codecs fixtures and negative tests preserve exact/procedural behavior, score eligibility, line identities, bounds and stale-byte checks. Full suite: 173 pass, one existing skip. Local: 117/117 per arm, 21.12% lower time, interval +11.19% to +32.57%. All 48 sessions/source packets reviewed; zero protocol violations. Gross tokens 1,200,035 vs 1,039,279, interval +2.050700% to +27.652270% (pass); uncached tokens 204,963 vs 174,895, interval -9.007132% to +36.317572% (fail); time 275.888s vs 247.556s, interval -2.157653% to +23.628740% (fail). Main/feature facts: 24/24 each; citations 14/24 vs 24/24. Exact public replay exits 1. Complete failed evidence is retained under attempt-21.


Trial 19 (`bb9ac24194577bcfec1bcb206d46c03c89f793a5`) corrects keyword selection to prefer explicit backticked identifiers, acronyms and camel-case names over generic prose. Their spelling is preserved, including final S in HTTPS/status; longest prose is only a fallback. The retained trial 18 `connect` suggestion caused an extra broad/failed search. Independent RPC/TLS/HTTPS/status fixtures reproduce the defect and verify literal lookup of the named subject without retrieval-score mocks. All scrubbing, miss status, source/access checks and benchmark requirements remain unchanged.

Trial 18 supplies one literal search keyword from the scrubbed question on a miss. It is a suggestion, supplies no facts/source reads, performs no research, and cannot turn a miss into a hit. Longest nonframing identifier/word is selected with singular-prefix normalization; recognized secret patterns are scrubbed before selection. No model package is imported for the hint. Full suite: 162 pass, one existing skip; Ruff/whitespace checks pass. This addresses the actual repeated empty phrase search, while retaining all source/access/age checks, budgets and model cutoffs.

Command-only and equivalent-question ranking diagnostics did not clear the existing cutoff. Those failed diagnostics are retained under attempt 18; neither experiment is shipped and no cutoff is lowered.

Trial 17 makes the miss/nonvalidated response self-contained at the decision to research: one literal keyword, requested facts only, follow dependencies only for unresolved requested facts, then stop at source support and answer concisely. This addresses trial 16’s actual empty phrase search and unnecessary dependency reads. No task-specific keywords, paths, answers, numerical budgets or benchmark criteria enter the runtime guidance. Backend, source validation, all reuse/provenance rules and tool operations are unchanged. Its completed isolated/full comparison is retained below.

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
| 19 | `bb9ac24` | 6/8; time/quality/compliance pass; both token intervals remain blocking; paused after full audit/public replay |
| 20 | diagnostic-only | No implementation or benchmark; original diagnostics preserved |
| 21 | `c33ef37` | 6/8; gross tokens pass; uncached-token and live-time intervals fail; full audit/public replay retained |
| 22 | `b6691a8` | **8/8** historical result; later audit found runtime/verifier defects, corrected in trial 23 |
| 23 | `6b7c6ae` | 5/8; main duplicate ask and gross/uncached token intervals fail; complete hardened replay exit 1 retained |
| 24 | `6031c44` | 5/8; quality, protocol and local pass; all three live intervals fail; complete hardened replay exit 1 retained |

Each measured revision has a committed plan and fresh independent stores. Revisions 1–13, 16–19 and 21–24 completed the same 48-session frozen-corpus comparison with alternating first arms. Revisions 14 and 15 did not advance past their failed local gates; complete local rows and import proofs remain retained. No identical-code retry was selected for better cache or sampling results. Every failure remains retained, including attempt 4's integrity rejection. A fingerprint matching the file cannot substitute for a required new research read.

### Earlier integrity rejection: trial 12

The strict analyzer exits 1 with `main/capture-2.colony: push fingerprint does not match an actual source read`. That baseline push affects later memory. Its hash matching the file does not satisfy the new-research rule, and no live performance result from this trial qualifies. The complete public archive reproduces the exact rejection; no baseline exemption was added.

All 48 sessions, manual labels and raw diagnostics remain retained. Feature `mcp-2` is labelled an irrelevant return: its connection configuration and current reads did not include exposed-tool facts, and the correct final answer required new research. Manual correctness labels do not waive the integrity rejection. See `attempt-12/observations.json` for explicitly nonqualifying raw totals.

### Prior valid comparison: trial 19

| Measurement | Main | Candidate | Reduction interval (95%) |
| --- | ---: | ---: | ---: |
| Native input + output | 1,236,018 | 1,102,543 | -0.106263% to +26.372975% |
| Uncached input + output | 187,442 | 157,135 | -0.955310% to +26.469006% |
| Subject wall time | 273.365s | 218.489s | +8.272746% to +29.995407% |
| Correct requested facts | 24/24 | 24/24 | implementing-assistant labels |
| Valid citation targets | 17/24 | 24/24 | implementing-assistant labels |
| Tool protocol violations | 0 | 0 | unchanged compliance audit |
| Research calls | 23 | 15 | descriptive count |
| Tool calls | 54 | 44 | descriptive count |

Time clears the unchanged gate; both token intervals remain blocking. Their lower bounds are negative even though point totals improve (10.80% gross tokens, 16.17% uncached tokens). Full suite: **166 pass**, one existing Windows skip; Ruff and whitespace checks pass. Local lookup time fell **22.04%**, interval +12.29% to +33.07%, with 117/117 oracle passes per arm. All 48 sessions and manual labels remain retained. Public archive replay exactly reproduces the comparison and exit 1. No citation or protocol failure was waived.

All feature citations are valid under the existing rubric. Main's explicit bare repository filename in the server answer is allowed by that same rubric; notes explain the grading without changing requirements. Main's invalid absolute targets still fail citation grading. The feature MCP family used 146,674 tokens versus main's 145,809, and server-start used 246,433 versus 241,421. The literal hint is not a universal improvement, and those costs remain included.

Trial 18's `connect` suggestion caused a second MCP search. Its tools lookup supplied both saved facts and the current Tools paragraph yet still researched; it remains labelled relevant. Trial 19 fixes the hint selection, while both token uncertainty gates remain unresolved.

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

## Historical paused checkpoint (superseded by explicit resume)

At the historical pause, trial 19 was complete, audited and publicly replayed, with 6/8 gates passing and no subsequent source change. The explicit resume led to trials 21 and 22 above. All eight remain mandatory, and the full benchmark contract, scripts, prompts, workloads and quality rubrics remain unchanged. No identical-code retry is selected for provider/cache luck. Do not merge PR #3 or begin another feature.

`attempt-20` contains only two source-order diagnostics and an explanatory outcome, not an implementation, test run, benchmark plan or completed trial. Raw neural ordering and sentence-focused ordering still favor metadata and were rejected. An independent ledger fixture reproduces metadata preceding the requested Operations declaration; prioritizing an explicit requested field within the existing eligible window is an unimplemented idea for the next chat to assess. No cutoff, scoring weight or response bound has changed. The user requested this pause before that fix began. See [RESUME.txt](RESUME.txt) for exact paths, commands and restrictions.

## Replay and limits

Each complete attempt contains local rows, workload, source/import proofs, live rows, reviewer labels, verification, checksums and `live-native.zip` with every native event, answer, prompt, stderr, metadata and implementation proof. Private databases, credentials, caches and issuer keys are excluded. Only proof host prefixes become runtime-relative; measured values, revisions and source hashes are unchanged.

```powershell
Expand-Archive docs/results/benchmark-remediation/attempt-22/live-native.zip <fresh-output-dir>
python scripts/analyze_lookup_live.py --results <fresh-output-dir> --local docs/results/benchmark-remediation/attempt-22/lookup-summary.json
```

Expected live replay exit for completed attempts 1–13, 16–19, 21, 23 and 24: **1**; trial 22: **0** with all eight gates true. Attempt 20 is diagnostic-only. Attempts 14 and 15 contain local results only and have no live archive; their local speed gates failed. Source commits must be available in Git. Checksum manifests have been checked against committed Git bytes. `attempt-1/prepare-review.py` prepares shuffled answers and citation checks; it does not assign fact correctness. Named Markdown anchors are manually verified against actual headings because the helper only recognizes numeric fragments.

All gates are mandatory, including positive lower paired-family interval bounds, full feature fact/citation correctness and both arms' tool compliance. No gate is advisory. These repeatedly exposed public development tasks, descriptive intervals and primary-assistant grades do not establish held-out production savings or independent approval.
