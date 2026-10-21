# Strict gate remediation against main

**Eight-gate clearance is incomplete. The latest complete live trial is rejected by the unchanged evidence-integrity check. PR #3 remains a draft and must not merge.** Work is checkpointed at the user's request to pause; no further trial or feature was started.

Main remains `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`. Latest measured candidate: `8e9ff1b2018bc5faa006fba2591450adbb7378de`, on `feat/benchmark-independent-evaluation`. Workloads, live prompts, quality rubrics, confidence requirements and all eight mandatory gates are unchanged throughout these attempts.

## Latest verification and rejection

**135 tests passed**, one existing Windows symlink skip; Ruff and whitespace checks pass. The isolated local benchmark passes **117/117 independent source-fact checks per arm**. Lookup time: main 2.237s, candidate 1.893s, a **15.4% reduction**, descriptive family-bootstrap interval **+6.7% to +23.8%**.

All **48 live sessions** completed without retries or omitted outcomes. The analyzer exits 1 with:

> feature/outage-3.colony: push fingerprint does not match an actual source read

The agent derived its retry answer from `ask.current_repository_reads`, then pushed without external repository research. The fingerprint matches the frozen README, but it does not satisfy the new-research protocol. That push also changes later memory state. This trial cannot clear any live performance gate. The public archive replay reproduces the exact rejection; no check was waived.

Manual labels find 24/24 candidate final answers correct with valid citation targets. They do not waive the integrity failure. The `tests-1` lookup returns offline model-provisioning prose instead of the test command: its relevance label is false, and the correct final answer required new research. Grading is by the implementing assistant after shuffling, not independent blinded approval.

Raw diagnostics, **not qualifying performance evidence**:

| Measurement | Main | Candidate |
| --- | ---: | ---: |
| Native input + output | 1,186,837 | 1,197,054 |
| Uncached input + output | 180,629 | 195,582 |
| Subject wall time | 264.558s | 251.206s |
| Correct final answers | 24/24 | 24/24 |
| Valid citation targets | 18/24 | 24/24 |

Uncached cost is still higher. A lower raw time does not establish the required interval-supported improvement. Tests/local checks can be green while every live clearance remains blocked by rejected evidence.

## Retained trials

| Attempt | Measured candidate | Outcome |
| --- | --- | --- |
| 1 | `b2fb0b7` | 6/8; uncached cost and live-time intervals fail |
| 2 | `0f841fa` | 4/8; two incorrect command answers and all three live performance gates fail |
| 3 | `035b996` | 5/8; quality restored, all three live performance gates fail |
| 4 | `8e9ff1b` | Live evidence rejected; eight-gate comparison cannot qualify |

Each has a committed pre-measurement plan, fresh independent stores over the same frozen main corpus, alternating first arms, and one 48-session trial per concrete revision. No identical-code retry was chosen for better sampling/cache results. Earlier totals and intervals remain in their original comparisons. Attempt 4 retains the rejection and explicitly nonqualifying observations.

## Runtime work retained

- Skip neural ranking for sole eligible candidates whose selection is invariant; preserve source, assertion, scope and age checks.
- Supply copy-ready relative citations and bounded original source text.
- Preserve command fences and adjacent recipe prerequisites, without pulling unrelated prose into earlier recipes.
- Avoid source embedding padding waste through individual inference; real-model checks preserve vector values and order. Profiles are retained.
- Focus text-document reranking on coherent neighboring sentences; retain original paragraph coverage/coherence guards and model cutoffs.
- Omit duplicate plain file identities when the same path/hash is already in current reads; retain assertions, undisplayed identities and warnings.

The v8 masking of positional ask examples increased research misses. It was reverted in v9; examples are navigation clues with recipe context, never answer proof. The regression checks actual procedure facts rather than asserting one masking implementation.

## Resume checkpoint

Fix the observed push-after-memory-reuse protocol failure. Inspect the `tests-1` procedural regression caused by prose focusing before measuring another candidate. Then run full verification, a new isolated local comparison, and a complete predeclared live trial against current main. Retain failures and keep all gates mandatory. Do not merge PR #3 or start another feature without the user's next instruction. The separate source-cache prototype is untouched.

This remains a repeatedly exposed public development suite, not held-out production confirmation or proof of general savings.

## Replay

Every attempt contains complete local rows/workloads/proofs, live rows and reviewer labels, verification, and `live-native.zip` with every native event, answer, prompt, stderr, metadata and implementation proof. Private databases, credentials, model caches and issuer keys are excluded. Only import-proof host prefixes are made runtime-relative; measurements, revisions and hashes remain unchanged.

```powershell
Expand-Archive docs/results/benchmark-remediation/attempt-4/live-native.zip <fresh-output-dir>
python scripts/analyze_lookup_live.py --results <fresh-output-dir> --local docs/results/benchmark-remediation/attempt-4/lookup-summary.json
```

Expected exit: **1**, with the integrity rejection. Source commits must be available in Git. Attempts 1-3 also exit 1 with retained gate failures. SHA-256 manifests cover every attempt. `attempt-1/prepare-review.py` documents shuffling/citation preparation, not correctness grading. Named Markdown anchors were manually checked against actual headings because that helper recognizes only numeric fragments.
