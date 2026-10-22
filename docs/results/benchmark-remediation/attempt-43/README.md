# Trial 43: local quality failure; live stage not started; paused

The user authorized one benchmark against current main, retention of all failures, an update to draft/unmerged PR #3 and a pause afterward, with no retries. Measured feature: `8fb06eed9524919bf86511d950cc03c10cd4ae0f`; freshly fetched main: `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`. No application, test or harness change occurred during this request.

## Result

**The single local comparison failed quality and exited 1.** Under the predeclared existing stopping rule, the live stage starts only if the local comparison qualifies. It did not qualify, so zero native sessions were started. No retry, fix, profile or live-analyzer invocation was performed. There is no new complete eight-gate comparison or live protocol/performance result.

| Local measurement | Main | Feature |
| --- | ---: | ---: |
| Scheduled observations | 117 | 117 |
| Independent oracle passes | 117 | 108 |
| Failed observations | 0 | 9 |
| Summed lookup seconds | 2.849069200048689 | 2.0737383997184224 |
| Query embeddings | 108 | 90 |
| Reranker calls / passages | 99 / 99 | 9 / 9 |

The feature's observed time is 27.213477% lower, with a 95% reduction interval of +17.693594% to +37.804338%. **This cannot pass the local gate because correctness failed.** Skipping valid answers and their inference is not a qualifying speedup. All 234 rows, every error, all durations and work counters are retained. The unchanged independent raw audit reconstructs the complete workload, observation order, oracle decisions, totals and interval exactly.

## What regressed

All nine failures are `paraphrase-0`, `paraphrase-1` and `paraphrase-2`, each failing in all three scheduled repetitions. Each asks **"How long do archived records stay available?"** and should reuse an attached current source fact, **"Archive retention interval is N days."** The frozen variants use 81, 13 and 93 days. Main returns the required validated fact; the feature returns `hit:false`, `reason:no_relevant_trail`.

The new descriptive saved-answer `local_term_support` guard is too restrictive for this semantic paraphrase. Its query terms are `archiv`, `availa`, `long`, `record`, `stay`; the saved fact shares only `archiv`, so it cannot meet the majority-term requirement. The new eligibility check rejects it before reranking, and the existing source fallback does not recover it. All nine raw feature observations record zero reranker calls. `local-failures.json` retains the exact cases, expected/actual results, static term explanation and main results. This diagnosis performs no additional lookup or model inference.

The passing tests at checkpoint 42 established that the original three unrelated-topic failures were repaired, but did not establish broader descriptive paraphrase recall. This benchmark exposes that missing coverage. The guard and the MCP save guidance remain unchanged during this single-run request; no benchmark-specific exception or oracle adjustment was added. Live research/save adherence has not been checked for this source because the prerequisite failed.

## Reused tests and unchanged contract

No tests were rerun. Source, scripts and tests are identical to tested source `f7066168c8023113291f8aac464286711ff69bf6`: **268 passed, zero failed, one existing Windows symlink skip**, Ruff and whitespace checks passed. `verification.json` is the unchanged checkpoint 42 record, and `tests-reuse.json` makes its provenance explicit. The full original pytest/JUnit logs remain in [checkpoint 42](../attempt-42/README.md).

Both local workers loaded complete frozen implementations and recorded actual module paths/source hashes. Local workload seed 20261021, three variants, three repetitions and all 39 cases are unchanged; canonical workload SHA256 is `f4fe06b2d9dc8b9e71b276d13b808aab7da0e60856a276f2b9af2b8b59357325`. All ten harness hashes and provisioned model files match prior evidence. Neither arm uses the earlier hybrid benchmark implementation. No oracle, prompt, rubric, threshold, model, budget or gate was weakened. These public development observations are not held-out production evidence.

## Required checks and pause

Tests/lint is green using the identical-code verification. **Local lookup is failed and blocking.** The other six required contexts are pending and blocking: loaded implementations for the full comparison, live fact/citation correctness, tool protocol and all three live performance gates. No historical green is carried to an unmeasured stage, and no current eight-gate pass count is asserted. Trial 41 remains the last full live comparison, 3/8 on its own recorded source.

All historical attempt directories/manifests and their failures remain byte-for-byte unchanged. This directory retains the complete raw local evidence, independent audit, nine failures, worker stderr, source/import/harness/model proof metadata, reused test provenance, exit information, wake log and required-check snapshot. Only import-proof host prefixes are normalized to runtime-relative paths for publication; measured values and source hashes are unchanged. The temporary wake request released after child exit and persistent power settings were not changed.

All eight original checks remain required under active main ruleset 25440133, with strict up-to-date enforcement and no bypass actors. PR #3 stays draft, unmerged and without auto-merge. Pause after publishing this failed result; no further work or run is scheduled. `lookup-summary.json` is the local result; `outcome.json` records which stages/checks were actually completed. `sha256.json` covers every other file here.
