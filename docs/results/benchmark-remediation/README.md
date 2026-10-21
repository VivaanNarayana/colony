# Strict gate remediation against main

**Six of eight blocking gates pass; uncached input plus output and live time still
fail. This is not a completed gate clearance or merge approval.** PR #3 remains
open and unmerged. No additional feature branch was started.

Baseline is current main `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d`;
measured candidate is `b2fb0b792d4ba1ac2f6c8766b8c381dbf033b697` on the existing
benchmark branch. The independent workload, live questions, confidence criteria,
quality rubric, and all eight mandatory gates are unchanged from the audit.

## Changes and verification

- Skip neural reranking for a sole exact-question candidate, which already bypasses
  the score threshold and has no competing order. Still check source hashes,
  assertions, age and fallback status. Its diagnostic relevance uses a neutral
  reranker contribution; it is not a calibrated confidence score.
- Skip neural reranking when only one excerpt survives eligibility bounds: its
  selection is invariant to the model score. Multiple candidates still use the
  original ranking and tie order.
- Include copy-ready, repository-relative Markdown citations with current read
  objects and explicit relative-path guidance in MCP initialization. Source
  fingerprints, read bytes, validation receipts and untrusted-data warnings stay.

Full local verification: **126 passed, one existing Windows symlink skip**, Ruff
and whitespace checks pass. The plan was committed before measurement: one local
trial, then one complete 48-session live trial if local qualified, with no retries
or omitted outcomes. No further trial was selected from identical-code reruns.

## Retained outcome

| Measurement | Main | Candidate | Gate |
| --- | ---: | ---: | --- |
| Local asks, all independent oracle checks | 117/117 | 117/117 | pass |
| Local lookup time | 2.548s | 2.257s | pass: 11.4% reduction, interval +1.0% to +22.2% |
| Local reranked passages | 99 | 18 | diagnostic only |
| Correct live answers | 24/24 | 24/24 | pass |
| Valid citation targets | 17/24 | 24/24 | pass |
| Protocol violations | 0 | 0 | pass |
| Gross native input + output | 1,213,371 | 1,093,852 | pass: 9.85% reduction, interval +3.91% to +15.76% |
| Uncached input + output | 177,467 | 179,036 | **fail**: 0.88% increase, reduction interval -24.87% to +16.37% |
| Live subject wall time | 253.960s | 243.481s | **fail**: observed 4.13% reduction, interval -4.32% to +14.42% |
| Research calls | 21 | 15 | diagnostic only |

Import verification and full tests/lint also pass. The original five failures are
reduced to two. A cheaper gross token total does not offset the uncached regression;
a faster point estimate does not offset an interval that crosses zero.

Gross tokens fell by 119,519 but cached input fell by 121,088, leaving 1,569 more
uncached input/output tokens. The largest per-task uncached increases were on
`serve-1` (+7,130), `stale-2` (+6,210), and `mcp-3` (+5,236), each with one ask and
no research in either arm. A second measurement without a causal implementation
change would risk choosing favorable cache/sampling variation. Cold-process
profiling measured MCP imports around 1.25–1.34s; importing the SDK's lower-level
server still took 1.18–1.19s. That inspection does not establish a fix for these
two remaining gates. Neither gate is advisory or marked passed.

This remains the public development suite, not held-out production confirmation.
Core facts were reviewed by the implementing assistant after shuffling answers;
this is not independent blinded approval. Citation targets were checked against
the actual frozen runtime; a missing repository root was rejected. Every returned
hit supplied relevant saved facts/current reads; misses were labeled nonrelevant.
The answer labels, individual notes and original native traces are retained.

## Reproduce the evidence audit

`attempt-1/` contains the pre-measurement plan, complete local workload and rows,
local summary, live arm rows/reviews, verification and strict comparison. The ZIP
contains all 48 public native sessions, prompts, stderr, metadata and import proofs.
Private runtime databases, model caches, keyfiles and issuer keys are excluded.
Only public import-proof host prefixes are replaced with paths relative to the
private runtime; raw measurements, answers and hashes are unchanged.

```powershell
Expand-Archive docs/results/benchmark-remediation/attempt-1/live-native.zip <fresh-output-dir>
python scripts/analyze_lookup_live.py --results <fresh-output-dir> --local docs/results/benchmark-remediation/attempt-1/lookup-summary.json
```

Expected exit is **1**, retaining the two blocking failures in `comparison.json`.
The original source commits must be present in Git. `prepare-review.py` records how
answers were shuffled and citation paths checked; correctness/relevance labels
were assigned by review, not by that script. Historical absolute citation targets
refer to the original runtime and need that path mapping for a new manual review.
