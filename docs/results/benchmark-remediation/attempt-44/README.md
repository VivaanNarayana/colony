# Checkpoint 44: semantic paraphrase repair, tests only; paused

The user requested a generalizable paraphrase repair, tests, retention of failures, an update to draft/unmerged PR #3, and a pause without another benchmark. Tested source: `5b7ee4838618610f836e1de15b2edbc6d905b5a5`, following trial 43's evidence publication `5a8e725ad1cadbbcf827ef5fbc3d483dafba3d28` on the same branch. This checkpoint contains functional tests, not a new benchmark result.

## Change

The descriptive saved-answer literal-term check no longer vetoes every semantic paraphrase. When a related descriptive answer lacks local term support, the existing cross-encoder now scores both the actual saved answer and its saved question alone. The answer must have a positive raw score and strictly outrank that question-only topic control. Ties, a stronger question-only score, and nonpositive answer scores fail this semantic fallback even with high dense similarity. Existing fused relevance and question-similarity requirements still apply.

This rule uses no subject-name dictionary, benchmark question/path selector, archive-specific exception, synthetic answer or new model. Exact-question reuse, procedural paraphrases and locally supported descriptions retain their existing paths. Namespace filtering, age handling, source assertions/current hashes, source-evidence fallback, excerpt selection and model/index versions are unchanged. The research/save protocol guidance from checkpoint 42 is unchanged.

The controls address the ambiguity behind the regression: literal word overlap can miss a valid paraphrase, while topic similarity alone does not establish an answer. Cross-encoder scores remain heuristics, not truth certificates. The fallback adds at most one question-only reranking input per eligible candidate, within the existing 12-candidate bound, and restores answer-ranking work that the old lexical veto skipped. Latency, inference cost, broad retrieval quality and live protocol adherence are unmeasured for this revision.

## Test evidence, including failures

Three distinct test phases are retained:

| Phase | Runtime | Passed | Failed | Skipped | JUnit seconds |
| --- | --- | ---: | ---: | ---: | ---: |
| New regression reproduction | Before the repair | 1 | 3 | 0 | 2.064 |
| Focused retrieval tests | After the repair | 34 | 0 | 0 | 7.827 |
| Full suite, one invocation | Tested source above | 277 | 0 | 1 | 87.714 |

The full pytest summary reports **277 passed, one existing Windows symlink skip, 87.73s**. Ruff and whitespace checks pass. The existing dependency deprecation warning remains in the logs. The red phase intentionally reproduces the bug before the runtime change; it is not hidden by the later passing results. No failing command was repeated without a change or represented as a benchmark retry.

Four real-model cases exercise archive retention, session expiry, request timeout and attachment size. Retention, timeout and size fail before the repair; the same four test bodies pass afterward. Each pairs the paraphrase with a different-attribute negative, denied namespace and changed-source check. Five additional score-boundary cases reject ties, topic-dominated scores and zero/negative answer scores, and accept a discriminating positive answer. All nine cases pass in the full suite.

The original three relay/registry/broker unrelated-topic assertions remain unchanged and pass. The five existing forced-high-score cases retain every hit/miss expectation, including unrelated/scattered-fact rejection. Their work assertions now require actual-answer and question-only ranking for unsupported descriptions, replacing the obsolete early-skip assumption; the benchmark work counters are unchanged and will count the added inference. No benchmark/evaluator/proof/qualification expectation was relaxed or removed.

`red-tests.xml`/`red.stdout.txt`, focused and full pytest/JUnit output, all exit/wake records, lint output, regression details, source/test/harness/model hashes and the required-check snapshot are retained. The red phase ran the initial four-case module; the five boundary cases were added before the focused/full phases. All historical attempts/manifests, including trial 43's nine local failures and trial 41's live failures, remain byte-for-byte unchanged. All tests use the same provisioned offline model snapshots; the temporary wake requests released after their children exited, with no persistent power changes.

## Gates and pause

No local/native benchmark, model profile or analyzer replay ran. Tests/lint is the only check validated on this source. The other seven original required checks are **pending and blocking**, including local lookup, full loaded-implementation verification, live fact/citation quality, tool protocol and all three performance intervals. The tests do not re-grade trial 43, establish a current local benchmark pass or supply a new eight-gate pass count. Trial 43 remains the last local comparison (main 117/117, feature 108/117); trial 41 remains the last complete live comparison (3/8 on its own source).

All benchmark scripts, independent oracles, evaluator tests, canonical workload, prompts, quality rubric, budgets and gate definitions remain unchanged. Main ruleset 25440133 remains active with all eight contexts, strict up-to-date enforcement and no bypass actors. PR #3 stays draft, unmerged and without auto-merge. Pause after publishing this evidence; no further work or run is scheduled. `verification.json` records the full suite, `regression-results.json` retains all three phases, and `sha256.json` covers every other file here.
