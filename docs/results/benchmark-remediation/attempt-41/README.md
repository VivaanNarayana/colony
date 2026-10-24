# Trial 41: completed once, 3/8 gates; paused

Trial 41 measures `568712878f810ae77be9930400b091dc7f810c3c` after the optional runtime removals against freshly fetched main `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`, still current when publishing. Exactly one test invocation, one local comparison and one fixed 48-session live comparison were run. No implementation, test or harness change occurred between stages, and no stage or task was retried.

**The unchanged analyzer reports 3/8 gates passed and exits 1.** Passes: loaded implementations, local lookup, and core-fact/citation correctness. Failures: tests/lint gate, tool-protocol gate, gross tokens, uncached input/output and live time. Every gate remains blocking; PR #3 remains draft and unmerged.

Tests: **256 passed, 3 failed, 1 existing Windows symlink skip**. Pytest reports 74.44s; the retained JUnit aggregate is 74.417s. Ruff and whitespace checks pass. The three adapted runtime cases (`relay/unavailable`, `registry/offline`, `broker/down`) fail the same negative assertion: a credential-scope question returns a hit after storing an availability/retry answer. Namespace denial assertions preceding that failure passed. No baseline test control or per-removal causal diagnosis was run; these failures are retained and not relaxed.

Local: 117/117 oracle checks per arm, 2.925747199857142s main versus 2.1176323997206055s feature, **27.620801% lower**, with 95% reduction interval **+18.199553% to +37.837853%**. All 234 raw observations independently reconstruct correctly. The local pass does not waive the full-suite failures.

| Live measurement | Main | Feature | Reduction | 95% reduction interval | Gate |
| --- | ---: | ---: | ---: | --- | --- |
| Gross native input + output | 1,154,983 | 1,251,082 | -8.320382% | -27.577490% to +5.623618% | **Fail** |
| Uncached input + output | 178,983 | 167,178 | 6.595598% | -26.593557% to +30.072728% | **Fail** |
| Summed subject wall seconds | 253.218 | 228.492 | 9.764709% | -3.065435% to +20.214073% | **Fail** |

Gross tokens increase 8.32%. Lower uncached and wall-time point totals cannot pass because their lower interval bounds are negative. All cache variation, empty searches, extra reads, attempted calls, saves, and durations remain counted. Main/feature tool calls are 50/55, research calls 20/26, asks 24/24, relevant hits and research-free correct answers 18/17. These combined implementation results do not isolate the effect of any individual removal.

All 48 requested core-fact answers are correct: 24/24 per arm. Citation targets pass 18/24 main and 24/24 feature; all six absolute main targets remain invalid. The unchanged citation rubric permits a bare repository-relative file reference, as in main `serve-3`; numeric citations were checked against the actual frozen source. Every answer/current packet, five novel contexts, actual research read and push receipt was reviewed by the primary assistant, not an independent blind reviewer. Source bytes/fingerprints match the frozen main corpus. No wrong reuse was observed.

The analyzer counts zero main and **two feature protocol violations**: `outage-1` researched six times, including two file reads, but omitted the required save; `serve-3` performed a search and omitted the required file-backed save. Both remain failures, with no budget or correct-answer exemption. All 11 actual pushes use hashes from preceding new reads and return successful stored/queued receipts. Main's two queued receipts are retained as actual queue outcomes under the unchanged rule.

An additional recorded observation is that feature `mcp-1` explicitly passed `tainted:false` for its repository-research push. The frozen analyzer does not separately check that argument; it is preserved in `manual-review.json`, `research-review.json` and the raw trace. No new mid-trial verifier rule is introduced, and the reported count of two is specifically the existing analyzer's count.

Complete evidence includes the full JUnit and pytest failure output, lint/whitespace output, canonical local workload/runner metadata/raw rows, independent raw audit, source/harness/test hashes, all native events/answers/source/import proofs, manual labels and research/save review. `live-native.zip` replays offline to exactly the same comparison JSON and exit 1; that verification starts no new native sessions. The reversible wake request released after child exit; clocks, persistent power configuration and original 240-second/8M-token budgets are unchanged. Main's active ruleset 25440133 still requires all eight checks, strict up-to-date enforcement and no bypass actors.

All earlier failures and removal decisions remain retained. Application, harness and test code are unchanged from the measured source; publication changes only documentation/evidence. These are repeatedly observed public development tasks and primary-assistant labels, not held-out production evidence or independent approval. Pause without fixes, another run, retries or merge; no further work is scheduled.

`comparison.json` is the authoritative eight-gate result. `sha256.json` covers every other file in this directory. Source/import proof host prefixes are runtime-relative for publication; measurements, source hashes and native observations are preserved.
