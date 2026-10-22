# Benchmark independence audit

The benchmarks had implementation-dependent and incomplete acceptance criteria.
This establishes weaknesses in the evidence, not intent to manipulate results.
The initial audit commit (`e2b865a`) left the production package unchanged.
The subsequent [gate-remediation attempt](results/benchmark-remediation/README.md)
adds runtime and citation fixes on the same branch. **Completed trial 22 passes all
eight unchanged blocking gates** against freshly fetched main `6fa6826` with source
`b6691a8`. Gross tokens fall 9.883710% (95% interval +1.219991% to +19.790718%),
uncached tokens 13.880644% (+5.614520% to +20.838154%), and live time 11.485126%
(+4.427843% to +17.692149%). All 48 answers/source packets and research traces were
reviewed: feature facts/citations 24/24, main facts 24/24 and citations 16/24, zero
protocol violations in either arm. Full suite: 184 pass, one existing skip;
Ruff/whitespace clean. Independent local oracle: 117/117 each; 23.62% lower time
with positive interval. Exported native evidence replays to identical JSON and exit 0.
Trial 21 remains retained with its two failed gates and exact replay exit 1; the
trial-19 pause and attempt-20 diagnostics remain historical evidence. New hints
have costs too: broad file search and an additional permission read remain counted.
PR #3 stays draft and unmerged. No gate was weakened or made advisory, no identical
source revision was retried for sampling, and no new branch/feature was started.
Repeated public development tasks and implementing-assistant labels are not
held-out production evidence or independent approval.

## Post-completion audit fixes in progress

A follow-up audit found exponential regex backtracking, omission of adjacent source
qualifications, missing required-push enforcement, and reliance on local summary
measurements. Trial 23 fixes all four and adds regression tests: 207 pass, one
existing skip. Its new source must clear a fresh local/live comparison. The existing
numerical gates remain unchanged; the analyzer now applies stronger evidence and
protocol checks. The original trial-22 rows and report remain untouched and also
reproduce exactly with the hardened verifier. Main's active ruleset now requires
all eight benchmark statuses with strict up-to-date enforcement, preserving prior
rules and bypass settings. PR #3 remains draft and unmerged.

## Findings

1. **The local baseline was a hybrid.** The previous `benchmark_lookup.py` executed
   baseline `store.py` and `context.py` while their `colony.retrieval`, models,
   security and validation imports came from the current checkout. Changes in
   those dependencies could therefore affect both arms. Complete frozen source
   exports now run in separate processes, with source and import proofs.
2. **Equality could reward two wrong answers.** Eight authored trails and 24 known
   self-repository phrasings were checked for response equality after removing
   `created_at` and `checks`. Some seeded setup commands were attributed to
   `pyproject.toml`, which did not contain those commands. Agreement alone did not
   verify an answer against its citation. The replacement checks independent
   source-backed expectations, negative cases, scope and freshness without
   consulting baseline output or production scoring helpers.
3. **The live quality gate ignored citations and absolute quality.** The retained
   trial had 24/24 feature core-fact labels versus 23/24 baseline, but only 16/24
   valid citation targets versus 19/24. It passed because only relative core-fact
   counts were gated. Even zero correct answers on both sides could pass that
   criterion. Every feature answer must now pass both requested rubrics.
4. **Point wins passed despite uncertainty.** The analyzer reported descriptive
   intervals for gross tokens and time but did not use them as gates; uncached
   input plus output had no interval. All three now require a positive lower
   paired-family interval bound, in addition to a lower observed total.
5. **Evidence checks were bypassable or incomplete.** Python `assert` statements
   disappeared under `-O`; push question/namespace, returned content hashes, and
   the word limit were not all independently checked. Explicit validation now
   rejects corrupt evidence, keeps failed reports, checks both arms' compliance,
   and verifies frozen task specifications for new trials.
6. **The public suite has been repeatedly exposed.** Retaining failed trials is
   good accounting, but iterating until this known suite turns green cannot make
   the final result held out. Primary-assistant boolean grading is not blinded
   independent review. The analyzer labels its output development-only and no
   longer issues an automatic merge-readiness conclusion or partial pass.

The inference-count unit tests remain useful checks of bounded work and API
behavior. Their exact call-count assertions do not establish general retrieval
quality or real agent savings. They are retained; the new evaluator tests exercise
false-success controls and corrupt evidence instead of relaxing product expectations.

## Reassessment of PR #2 evidence

The original measurements and failed attempts remain intact. Running the new
analyzer on a separate copy of the retained 48 native sessions reproduced the
original totals and found **five of eight blocking gates fail**. This is a
retrospective reassessment under stronger criteria, not a newly predeclared trial.

| Metric | Main | Feature | Reduction interval (95%, descriptive) |
| --- | ---: | ---: | ---: |
| Gross native input + output | 1,173,949 | 1,154,871 | -4.75% to +8.48% |
| Uncached input + output | 234,941 | 209,591 | -6.16% to +26.47% |
| Subject wall time | 317.301s | 291.185s | -4.40% to +21.45% |
| Correct core facts | 23/24 | 24/24 | primary-assistant labels |
| Valid citation targets | 19/24 | 16/24 | primary-assistant labels |

Import verification, the recorded tests/lint, and protocol compliance still pass.
The legacy hybrid local run cannot satisfy the independent local gate. Citation
quality and all three performance intervals fail. This does not prove the runtime
optimization is harmful; it means these measurements do not support the earlier
readiness conclusion. PR #2 was already merged before this audit; this branch
initially changed benchmark machinery and documentation only. Follow-up runtime
fixes and their retained failed gates are reported separately in the remediation report.

See the retained [reassessment](results/benchmark-audit/pr2-reassessment.json).

## Evaluator validation

Local verification: **125 tests passed, one existing Windows symlink skip**;
Ruff and whitespace checks pass. No live provider sessions were launched for this audit.

Adversarial controls reject an always-miss responder, an always-validated responder,
shared wrong evidence, forged hashes/line numbers, scope escapes, malformed packets,
stale evidence, zero-quality ties, citation loss, missing intervals and invalid
measurements. Separate-process tests put conflicting packages on `PYTHONPATH` and
verify every selected retrieval dependency. Complete synthetic native sessions
exercise corrupt returned bytes, changed push arguments, invalid token accounting,
duplicate task identities, changed family assignments, overlong answers and failure
retention under optimized Python. No provider calls are made by these tests.

Two A/A runs used main `6fa6826aa4c835001c0d2bc8f3bec838499e5b5d` in both
processes. The initial smoke run passed 52/52 oracle checks and observed a 1.56%
timing reduction, but correctly rejected improvement. The expanded run used 39
cases across 13 contract families and three repetitions per arm: **234/234 oracle
checks passed**, with identical inference counts. Its observed reduction was
-0.55%, with an interval of -3.11% to +2.11%; it also correctly exited 1 with
`same_implementation=true`, `quality_passed=true`, `lookup_improves=false`.
Both attempts are retained in [results](results/benchmark-audit/).

These A/A runs validate evaluator behavior. They are not estimates of a new
application improvement. The new contract fixture remains public, small and
development-only; follow the [evaluation protocol](benchmark.md) for independent
confirmation before making general performance claims.
