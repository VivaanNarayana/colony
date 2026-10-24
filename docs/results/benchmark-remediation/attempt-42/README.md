# Checkpoint 42: correctness fixes, tests only; paused

At the user's request, fix the test and tool-protocol failures with generalizable changes, run the tests, retain failures, update draft PR #3 and pause without another benchmark. Tested source: `f7066168c8023113291f8aac464286711ff69bf6`, following trial 41's evidence publication `c69643bdf29eaeadbc5323c43cac56d0f43e0248` on the same branch. This is a tests-only checkpoint, not a new benchmark result.

## Changes

Related descriptive saved answers now need the same local term-support check already required of source evidence: most query terms must occur within two adjacent sentences of the answer. This rejects a shared subject as sufficient support for a different requested fact, even when dense similarity and reranker scores are high. It runs before reranking. Exact-question reuse, procedural semantic paraphrases, numerical ranking thresholds, source-evidence fallback, namespace filtering, freshness and file-proof checks retain their existing rules. The original relay/registry/broker tests and their unrelated-topic assertions are unchanged.

MCP session instructions, ask/push descriptions and lookup response guidance now explicitly require the save after needed external research: obtain a supporting file's actual fingerprint during that research, push the supported answer with the original question/namespace before answering, and check for a stored/queued receipt or report failure. Search matches alone do not provide a file fingerprint; push calls are separate from research calls. Sufficient reuse still requires neither research nor push, and ask's reads/hashes never qualify as new research. No automatic push, invented proof, extra research for memory alone, keyword/path hint, search heuristic, dependency-expansion rule or narration policy was added. Adding miss guidance now copies the client receipt rather than mutating it.

These repairs address correctness and the missing positive save obligation. They do not establish that removing a particular earlier heuristic caused trial 41's failures. The narrower descriptive eligibility can increase misses, and explicit instructions add text; their performance impact is unmeasured. Procedural paraphrases remain covered by the existing semantic retrieval test; broader descriptive paraphrase recall is not established by this check.

## Tests and retained evidence

One full suite invocation: **268 passed, 0 failed, 1 existing Windows symlink skip**. Pytest summary: 71.13s; JUnit aggregate: 71.111s. Full Ruff and whitespace checks pass. The same provisioned embedding/reranker snapshots were hash-verified and used offline with the existing model/thread/batch settings. The temporary wake request released after the child exited, without persistent power changes.

All three previously failing cases pass unchanged. Nine new regression cases cover four unrelated/scattered-fact negatives and one supported positive under deliberately strong semantic scores, plus required research/save guidance on miss, stale, unverified and validated response paths. The latter also preserve no-save reuse and client-receipt immutability. These are instruction/contract tests, not evidence that a live model now complies. No tests or assertions were removed, relaxed, skipped or selected around the failures.

Full pytest/JUnit output, lint/whitespace output, regression results, source/test/harness/model hashes, exit information and the required-check snapshot are retained here. All previous attempt directories and their manifests, including trial 41's three failed tests, two protocol failures and three failed performance gates, remain byte-for-byte unchanged. No local/native benchmark, model profile, analyzer replay or task retry was run for this checkpoint.

## Required gates and pause

Only `benchmarks/tests_and_lint_pass` is validated for this source. The other seven required checks are **pending and blocking**, including live tool protocol, loaded implementations, local lookup, answer/citation quality and all three performance intervals. No historical green check is carried to the changed runtime, and trial 41 remains the last complete comparison: **3/8 on its recorded source**. There is no new eight-gate pass count or protocol/performance improvement claim.

Main ruleset 25440133 remains active with all eight original contexts, strict up-to-date enforcement and no bypass actors. Benchmark scripts, independent oracles, evaluator tests, workload, prompts, rubric, budgets and gate definitions are unchanged. PR #3 stays draft, unmerged and without auto-merge. Pause after publishing this evidence; no further work or run is scheduled.

`verification.json` records the actual test results; `outcome.json` distinguishes them from pending benchmark checks. `sha256.json` covers every other file in this directory.
