# Strict gate remediation against main

**Trial 8 clears five of eight gates. All three live performance gates remain blocking; PR #3 remains draft and must not merge.** Work is checkpointed after completing the in-flight trial at the user's request to pause. No further revision, trial, feature or feature branch was started.

Main remains `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`. Candidate `21bc30c7fa38f43d2f39cc983fb0221368f240fb` is on `feat/benchmark-independent-evaluation`. Workloads, live prompts, quality rubrics, confidence requirements and all eight mandatory gates are unchanged throughout remediation.

## Current revision

Trial 8 changes only MCP guidance for missing facts: read known defining paths directly, use documented search syntax, stop once requested facts are supported, and use actual repository-relative citation targets. It follows native traces with repeated empty phrase searches and an extra guide read after the defining implementation. Existing trust, new external research, source-hash and no-push-after-reuse rules remain in place.

Full suite: **138 passed**, one existing Windows symlink skip; Ruff and whitespace checks pass. Isolated local: **117/117 source-fact checks per arm**, 16.14% less lookup time, descriptive 95% family-bootstrap interval **+9.37% to +23.17%**. All 48 live sessions completed without retries. Evidence/import checks, correctness and tool compliance pass. Both token gates and the live-time gate fail their unchanged interval requirements.

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

Each revision has a committed plan before measurement, fresh independent stores over the same frozen main corpus, alternating first arms, and one full 48-session live trial. No identical-code retry was selected for better cache or sampling results. Every failure remains retained, including attempt 4's integrity rejection. A fingerprint matching the file cannot substitute for a required new research read.

### Latest completed comparison: trial 8

| Measurement | Main | Candidate | Reduction interval (95%) |
| --- | ---: | ---: | ---: |
| Native input + output | 1,136,022 | 1,064,656 | -3.81% to +20.29% |
| Uncached input + output | 166,038 | 170,448 | -21.59% to +7.42% |
| Subject wall time | 339.185s | 295.524s | -5.79% to +27.80% |
| Correct requested facts | 24/24 | 24/24 | implementing-assistant labels |
| Valid citation targets | 17/24 | 24/24 | implementing-assistant labels |
| Research calls | 17 | 13 | descriptive count |

All feature citation targets now pass, including `serve-3`. Both arms have zero protocol violations. Gross native tokens declined 6.28% and subject time declined 12.87%, but their intervals cross zero. Uncached input plus output **increased 2.66%** and its interval also fails. These are three blocking failures, not advisory checks. Five fewer tool calls and four fewer research calls do not substitute for clearing them.

Public archive replay reproduces the complete comparison and blocking exit 1 exactly. Trial 7 remains retained with its invalid citation and failed performance intervals. No earlier failed run was replaced.

## Runtime changes retained

- Skip neural ranking only where a sole eligible candidate cannot change selection; preserve validation, scope and age checks.
- Return canonical relative citations and bounded original source text, retaining complete command fences and adjacent recipe prerequisites.
- Avoid ONNX padding waste with individual source embedding inference; real-model checks preserve vector values and order.
- Focus nonprocedural text-document ranking on coherent neighboring sentences; retain full procedure inputs and existing coverage, coherence and model cutoffs.
- Normalize availability and queued-request paraphrases while preserving substantive query subjects and credential/token uses of “get.”
- Omit duplicate plain file identities already present in current reads; preserve assertions, undisplayed identities, warnings and hashes.
- Explicitly classify evidence-only derivation as memory reuse even for a new question; prohibit pushing or rereading solely to push it.
- Compact duplicated MCP session policy. Reuse valid v7 indexes: passage inputs match main exactly after the experimental positional-query mask was reverted; experimental v8/v9 stores still rebuild by version mismatch.

The positional-query masking experiment caused research misses and was reverted. Procedure focusing also selected unrelated provisioning prose; full procedure paragraphs are restored. Those failed outcomes are retained rather than hidden by the later fixes. The separate source-cache prototype is untouched.

## Pause checkpoint

The in-flight trial, manual source review, analyzer run, public replay and artifact export are complete. Full verification was run on the measured source; only reports and retained evidence were added afterward. The branch is pushed and all eight commit checks retain the strict comparison's outcomes. Merge review cannot approve while the three live gates fail.

On resume, diagnose uncached input and avoidable research round trips from the retained events before making a concrete next revision. The new guidance reduced `scope-2` research relative to trial 7, but `serve-3` still read README after its defining CLI parser, and `scope-3` performed an extra source read despite sufficient returned facts. Provider timing/cache outliers remain material; repeating identical code to select a favorable trial is not a fix. Preserve the frozen benchmark contract and every failed outcome. Do not merge PR #3 or begin another feature.

## Replay and limits

Each complete attempt contains local rows, workload, source/import proofs, live rows, reviewer labels, verification, checksums and `live-native.zip` with every native event, answer, prompt, stderr, metadata and implementation proof. Private databases, credentials, caches and issuer keys are excluded. Only proof host prefixes become runtime-relative; measured values, revisions and source hashes are unchanged.

```powershell
Expand-Archive docs/results/benchmark-remediation/attempt-8/live-native.zip <fresh-output-dir>
python scripts/analyze_lookup_live.py --results <fresh-output-dir> --local docs/results/benchmark-remediation/attempt-8/lookup-summary.json
```

Expected exit for completed attempts 1–8: **1**. Source commits must be available in Git. Checksum manifests have been checked against committed Git bytes. `attempt-1/prepare-review.py` prepares shuffled answers and citation checks; it does not assign fact correctness. Named Markdown anchors are manually verified against actual headings because the helper only recognizes numeric fragments.

All gates are mandatory, including positive lower paired-family interval bounds, full feature fact/citation correctness and both arms' tool compliance. No gate is advisory. These repeatedly exposed public development tasks, descriptive intervals and primary-assistant grades do not establish held-out production savings or independent approval.
