# Single benchmark trial 47 — 5/8 gates pass; paused

Measured feature `1bcb38ebeeaecb29a548ecd02017804043d8a789` against freshly fetched main `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`, rechecked unchanged after the run. No runtime, test or harness change occurred during or after measurement. PR #3 stays draft, unmerged and without auto-merge.

**The unchanged analyzer reports five passing gates and three blocking performance failures; exit 1.** Passes: loaded implementations, tests/lint, independent local lookup, core-fact/citation correctness and the frozen tool-protocol gate. Gross tokens, uncached input/output and live time fail. There was one local invocation and one 48-session live invocation, with no benchmark/session restart or retry. All costs and outcomes remain retained.

## Fix checks and quality

The integrity audit accepts every attempted file proof: all 13 pushes use hashes from actual preceding new file reads, matching frozen source bytes. There are no malformed or mismatched push attempts. Feature `scope-3` (R36) makes one successful server.py-backed save. Feature `mcp-2` (R21) correctly names both `ask` and `push` from a relevant current packet and makes no duplicate research/save. Main `tests-1` (R15) now reuses its relevant hit with one ask and no research or push. These are observations in this single run, not causal isolation of individual changes or a guarantee of future adherence.

Core facts: **24/24 in both arms**. Citation labels: **18/24 main, 24/24 feature**. The six invalid main absolute targets (R10, R11, R35, R41, R43, R47) remain invalid. R07's bare repository-relative source reference remains allowed by the unchanged rubric. Current text and cited source facts were reviewed; citation anchors can identify the beginning of the displayed supporting excerpt. No rubric or label was changed after gate analysis.

All 48 shuffled answers/current packets and all 13 research/save traces were reviewed by the primary assistant, not an independent blind reviewer. There are no novel current read texts relative to previously reviewed frozen bytes; all actual search lines, full read bytes and hashes were checked again against the same frozen corpus. All miss/unusable-hit research/save obligations and sufficient reuse outcomes satisfy the frozen protocol rule. There are six main pushes (three stored, three queued) and seven feature pushes (all stored). Queued receipts are actual retained outcomes under the unchanged stored-or-queued success rule.

**Additional instruction deviation retained:** feature `scope-2` (R38) explicitly passes `tainted:false` for newly researched content. This conflicts with the external-content taint instruction. The frozen analyzer does not check that argument, so its reported zero protocol violations refers specifically to its existing rules. This observation remains in raw traces, `manual-review.json`, `research-review.json` and `outcome.json`; no mid-run gate change or hidden waiver was introduced.

## Local result and reused tests

Both arms pass **117/117** independent local oracle checks, including all archive-retention paraphrase variants in all three scheduled repetitions. All 234 observations reconstruct exactly. Main takes 4.916482400323730s and feature 3.674549199698959s: **25.260605% lower**, 95% reduction interval +15.696777% to +36.513030%. Reranker calls/passages are 99 main versus 27 feature; query embeddings 108 versus 90.

No tests were rerun. The complete `src`, `scripts` and `tests` match checkpoint 46's tested source `c181654bcf3422075310386b85b78c9ae80babbd`: **285 passed, one existing Windows symlink skip**, Ruff/whitespace pass. Canonical hashes and explicit identical-code reuse provenance are retained. Its full JUnit, raw phase archive and development failures remain unchanged in checkpoint 46.

## Performance failures

| Measurement | Main | Feature | Reduction | 95% reduction interval | Gate |
| --- | ---: | ---: | ---: | --- | --- |
| Gross native input + output | 1,138,615 | 1,231,629 | -8.169047% | -16.103423% to -2.603626% | **Fail** |
| Uncached input + output | 144,823 | 195,853 | -35.236116% | -57.368621% to -13.060073% | **Fail** |
| Summed subject seconds | 306.117 | 290.257 | +5.181026% | -1.699157% to +11.077753% | **Fail** |

Gross tokens increase **8.169047%** and uncached tokens increase **35.236116%**; both entire reduction intervals are negative. Time's point total is **5.181026% lower**, but its lower interval bound is negative. None of these three performance gates passes. All cache variation, empty searches, extra reads, saves and elapsed time remain counted.

Tool calls are 48 main versus 51 feature; research calls 18 versus 20; asks 24 each. Raw/relevant hits and research-free correct answers are 18 main versus 17 feature. Cached input totals are 993,792 main versus 1,035,776 feature. The benchmark prompts, corpus and research adapter are shared; Colony MCP tool descriptions/schema differ as part of the measured feature (`mcp_surface_identical:false`), and their full token cost remains counted. The combined implementation comparison does not isolate the cost of any one runtime change, and repeatedly observed public tasks do not establish held-out production savings.

## Retention and pause

All eight original contexts remain required under active main ruleset 25440133, with strict up-to-date enforcement and no bypass actors. The five successes and three failures are published on the evidence-only head. No threshold, workload, prompt, oracle, rubric, model snapshot, original 240-second per-session limit or 8M aggregate-token budget changed. The official analyzer verifies both arms and all budgets; there is no baseline exemption.

The complete local workload/plan/raw rows/audit, all native events/answers/packets/prompts, source/import proofs, frozen labels, actual save receipts, source/model/harness hashes, server/worker/operational logs and required-rule snapshot are retained. `live-native.zip` reproduces **exactly the same comparison JSON and exit 1 offline**, without new subject sessions or provider calls; only import-proof host prefixes become runtime-relative. `sha256.json` covers every other file using exported bytes. All prior attempt directories and manifests remain untouched, including trial 45's rejected fingerprint and checkpoint 46's failed experiments.

Temporary wake requests released automatically; persistent power settings and clocks were unchanged. Pause after updating PR #3; no fixes, retries, additional benchmark or further work is scheduled.
