# Trial 45: local passes; live evidence rejected; paused

The user requested one benchmark against current main, retention of failures, an update to draft/unmerged PR #3, and a pause with no retries. Measured feature: `a4fcb7aaeb3f65b05cb5129d8942f105bb30fb6d`; freshly fetched main: `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`, rechecked unchanged at publication. One local comparison and all 48 scheduled live sessions completed. No application, harness, test, workload, prompt, rubric, model, threshold or budget changed during the run.

**The local paraphrase regression passes. The unchanged live analyzer rejects evidence integrity and exits 1. There is no complete eight-gate verdict for this trial.** No benchmark or subject session was restarted; a model-initiated second push within one session is retained, including the first failed attempt and every cost. No corrective implementation or benchmark rerun was performed.

## Local comparison

| Measurement | Main | Feature |
| --- | ---: | ---: |
| Source-backed oracle checks | 117/117 | 117/117 |
| Summed warm lookup seconds | 2.5506163998506963 | 1.9136535002035089 |
| Query embeddings | 108 | 90 |
| Reranker calls / passages | 99 / 99 | 27 / 27 |

Time is 24.972901% lower, with a 95% reduction interval of +15.910548% to +35.575260%. All 234 observations independently reconstruct exactly from the unchanged canonical workload. All three archive-retention paraphrase variants pass in all three scheduled repetitions; trial 43's nine failures remain unchanged in their own archive. This establishes the local regression result, not production generalization or a live performance win.

## Live rejection and observed failures

The official `comparison.json` reports `all_gates_passed:false`, blocking reason `evidence_integrity`, and:

```text
feature/scope-3.colony: push fingerprint does not match an actual source read
```

Feature `scope-3` (review R36) first submits a README fingerprint containing 63 characters instead of the actual 64-character SHA256 returned by its preceding read. MCP schema validation rejects that push. The model then submits a second push containing only the valid `server.py` source and receives a stored receipt. The frozen analyzer checks every attempt, so this later success does not erase the malformed first fingerprint. Both attempts, the validation error, native usage and final answer remain untouched. No hash was repaired in the evidence and no attempt was excluded.

Manual review also finds:

- Feature `mcp-2` (R21) does not name the required `ask`/`push` tools. It receives a validated but irrelevant README metadata excerpt at line 96 and stops with an explanation that the excerpt lacks the answer. It does no research or save despite that gap. Its core-fact label and relevance label are false. The relative citation supports its metadata statement, but does not make the missing requested answer correct.
- Main `tests-1` (R15) already receives a relevant validated README excerpt containing `python -m pytest -q`, but rereads README and pushes again, receiving a queued receipt. The unchanged reuse rule prohibits that unnecessary save. Main gets no protocol exemption.
- Main has five invalid absolute citation targets (R07, R27, R32, R35, R43); all remain invalid. Core facts are 24/24 main and 23/24 feature; citation labels are 19/24 main and 24/24 feature. Bare repository-relative references remain permitted by the existing rubric; R09/R33 are not new waivers.

Feature `outage-1` and `serve-3`, which omitted saves in trial 41, now each perform actual research and one successful save. That narrower improvement does not waive the new failures. All 48 answers/current packets, the one novel context, all 15 research-session traces and the unusable-hit session were reviewed by the primary assistant, not an independent blind reviewer. Labels were frozen before gate analysis. All actual read bytes/hashes match the frozen corpus; the malformed submitted push hash remains a failure.

## Descriptive accounting only

The following totals include all 48 sessions, all research, all push attempts, cache variation and all elapsed time. **They are not a valid performance-gate comparison.** The official analyzer stopped at integrity rejection; no confidence intervals or performance passes are inferred afterward.

| Native observation | Main | Feature |
| --- | ---: | ---: |
| Gross input + output | 1,209,784 | 1,292,976 |
| Cached input | 1,030,400 | 1,112,832 |
| Uncached input + output | 179,384 | 180,144 |
| Summed subject seconds | 262.308 | 245.993 |
| Tool calls / research calls / asks | 52 / 21 / 24 | 55 / 22 / 24 |
| Push attempts | 7 | 9 |
| Successful stored / queued receipts | 3 / 4 | 8 / 0 |
| Failed push attempts | 0 | 1 |
| Raw / relevant hits | 18 / 18 | 16 / 15 |
| Research-free correct answers | 17 | 15 |

Gross tokens increase 6.876599% and uncached tokens increase 0.423672%; time's point total is 6.219787% lower. These observations do not pass any live performance gate. Main's four queued receipts are actual retained outcomes under the unchanged stored/queued rule, not repaired or normalized saves.

## Required checks, tests and retained artifacts

Tests/lint passes using checkpoint 44's identical-code verification: 277 passed, one existing Windows symlink skip; Ruff/whitespace passed. No tests were rerun. `src`, `scripts` and `tests` are identical to tested source `5b7ee4838618610f836e1de15b2edbc6d905b5a5`; provenance and source hashes are explicit.

Published statuses preserve the independently verified test/local successes and known quality/protocol failures. The full loaded-implementation context and three performance contexts remain pending and blocking because the official full comparison was rejected. These statuses are **not an official eight-gate score**. All eight original contexts remain required under active main ruleset 25440133, with strict up-to-date enforcement and no bypass actors. No green result is substituted for the rejected analysis.

The complete local workload, runner metadata, 234 rows and audit; all 48 native traces/answers/packets; both implementations' source/import proofs; frozen reviews and actual save receipts; malformed hash/error; source/model/harness hashes; reused test record; analyzer rejection/stdout/stderr; operational logs and required-rule snapshot are retained. `live-native.zip` reproduces exactly the same rejection JSON and exit 1 offline, without starting new sessions. Only import-proof host prefixes are made runtime-relative for publication. Temporary wake requests released after child exit; clocks, persistent power settings and original budgets are unchanged.

All previous attempt directories/manifests and failures remain byte-for-byte unchanged. Repeatedly observed public development tasks and primary-assistant labels are not held-out production evidence. PR #3 stays draft, unmerged and without auto-merge. Pause after publishing this single failed comparison; no further work or run is scheduled. `comparison.json` is the official rejection, `descriptive-accounting.json` is explicitly nongating, `outcome.json` explains the partial statuses, and `sha256.json` covers every other file here.
