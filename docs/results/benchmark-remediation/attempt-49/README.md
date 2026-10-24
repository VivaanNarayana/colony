# Single benchmark trial 49 — 4/8 gates pass; paused

Measured feature `e631f5b3d001e6ddf53286b1c60ff78af743c955` against freshly fetched main `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`, rechecked unchanged after the run. No runtime, test or harness change occurred during or after measurement. PR #3 stays draft, unmerged and without auto-merge.

**The unchanged analyzer reports 4/8 gates passing; exit 1.** Loaded implementations, tests/lint, local lookup and correctness pass. Tool protocol and all three performance gates fail and remain blocking. There was exactly one local invocation and one 48-session native invocation, with no benchmark/session restart or retry. All attempts, costs and outcomes are retained.

## Quality, protocol and taint

Core facts are **24/24 in both arms**; citations are **14/24 main and 24/24 feature** under the unchanged rubric. Ten invalid main absolute/file targets stay invalid. Bare repository-relative source references remain accepted by the original rubric. All 48 shuffled answers and current packets, three novel current excerpts and 15 research/save traces were reviewed by the primary assistant, not an independent blind reviewer. Actual search lines, read bytes, hashes and scrubbed current-source excerpts match the frozen corpus.

**Feature `mcp-2` (R21) violates the save protocol.** Its validated README excerpt begins at line 96 and discusses receipt metadata; it does not supply the requested `ask`/`push` signatures. That packet is labeled **irrelevant**, not upgraded merely because it is validated. The subject makes four new research calls and obtains the missing facts, then answers correctly but makes no push. The frozen gate consequently records one feature protocol violation and zero main violations. The missing save is not waived, even though the final answer is correct. All other researched-answer saves use actual successful stored/queued receipts.

All **14 attempted push fingerprints** match actual preceding new file reads. Main has six pushes: two stored receipts and four queued receipts. Feature has eight pushes, all stored. The supplemental storage/queue audit finds six main rows and four main encrypted queue files; queued replies are retained as queued and are not promoted to stored successes. No malformed or mismatched proof was retried or discarded.

All eight actual feature stored trails have taint enabled. No feature caller explicitly supplied false, and no feature offline queue was exercised in this run, so this run does not independently exercise those two enforcement paths; checkpoint 48's regression tests cover both. Main `serve-2` (R37) explicitly supplies `tainted:false`, retained as false in its stored row and encrypted queue. This is an additional instruction deviation outside the frozen taint-agnostic analyzer, not an exemption or a mid-run rubric change. See `tool-audit.json` and `manual-review.json` for exact scope and limits; private databases and queue credentials are not published.

## Local result and reused tests

Both arms pass **117/117** local oracle checks, including every archive-retention paraphrase variant across all three scheduled repetitions. All 234 raw observations reconstruct exactly. Main takes 2.852487800002564s and feature 2.138003999716602s: **25.047743% lower**, with 95% reduction interval +16.206957% to +35.437965%. Reranker calls/passages are 99 main versus 27 feature; query embeddings are 108 versus 90.

No tests were rerun. All `src`, `scripts` and `tests` match checkpoint 48's tested source `2261d26fa76ef0e0a5e71356490a6233d537fdc0`: **300 passed, one existing Windows symlink skip**, Ruff/whitespace pass. Its full JUnit, exact raw test-phase archive and development failures remain unchanged. This explicit identical-code test reuse is distinct from a benchmark retry.

## Performance failures

| Measurement | Main | Feature | Reduction | 95% reduction interval | Gate |
| --- | ---: | ---: | ---: | --- | --- |
| Gross native input + output | 1,127,063.000 | 1,429,053.000 | -26.794421% | -44.929844% to -9.536998% | **Fail** |
| Uncached input + output | 145,815.000 | 235,453.000 | -61.473785% | -119.949509% to -23.792566% | **Fail** |
| Summed subject seconds | 245.936 | 246.336 | -0.162644% | -17.398182% to +12.603186% | **Fail** |

Gross native tokens increase **26.794421%** and uncached input/output increase **61.473785%**; both reduction intervals are entirely negative. Time increases **0.162644%** at the point total and its interval crosses zero. None of the three performance gates passes. The new optimizations have no demonstrated benefit on these full native gates in this comparison.

Feature makes **31 research calls versus main's 16**, with 63 versus 46 total tool calls. Relevant hits and research-free correct answers are **15 feature versus 18 main**. Feature has additional misses on `scope-3` and `stale-2`, and the insufficient `mcp-2` hit requires research. These observed costs are retained; the combined trial does not isolate which individual runtime change caused them. Cache variation, empty searches, extra reads, saves and all elapsed time remain counted. Both arms run the same new benchmark harness, research adapter, prompts, order, corpus and model configuration. Their Colony MCP surfaces differ as part of the measured implementation, and the full cost is counted.

## Retention, verification and pause

All eight original checks remain strictly required under active main ruleset 25440133 with no bypass. Four successes and four failures are published on the evidence-only head. No oracle, relevance rule, prompt, workload, rubric, cutoff, confidence requirement, model snapshot, 240-second per-session limit or 8M aggregate-token budget changed. The official analyzer validates both arms without a main exemption.

The complete local plan/workload/raw rows/audit, all native events/answers/prompts/packets, source/import proofs, frozen labels, research/save traces, receipts, source/model/harness hashes and operational logs are retained. `live-native.zip` reproduces **exactly the same comparison JSON and exit 1 offline**, with no new subject session or provider call; only import-proof host prefixes become runtime-relative. `sha256.json` covers every other public file using its exported bytes. All prior attempts and their manifests remain byte-unchanged, including trial 47's failures and checkpoint 48's test failures.

A new supplementary audit-helper assertion initially compared scrubbed README excerpts to raw text and failed on the existing `[REDACTED]` replacement for the prose `bearer credentials`. The helper was corrected to compare the same scrubbed source while still checking original full-file hashes. That diagnostic is retained in `audit-helper-failure.json`; no raw measurement, product code, benchmark, rubric or quality label changed to resolve it. The official analyzer ran once after labels were frozen, plus one offline archive reproducibility audit.

The repeated self-repository tasks are public development evidence, not held-out production evidence. Sampling and cache variation remain, and descriptive intervals do not correct for repeated selection. Temporary wake requests released automatically; persistent power settings and clocks were unchanged. Pause after updating PR #3; no fixes, retries, additional benchmark or further work is scheduled.
