# Benchmark independence audit

The benchmark acceptance weaknesses and follow-up protections remain addressed. **The user's single authorized trial 41 is complete and paused: 3/8 gates pass.** The test, tool-protocol and all three live performance gates fail. Source `5687128` is compared with freshly fetched current main `6fa6826`; all eight gates remain strict and required, and PR #3 stays draft and unmerged.

The full suite reports 256 passed, 3 failed and one existing Windows symlink skip; Ruff/whitespace pass. Three adapted runtime cases fail unrelated-topic hit rejection. Local oracle checks pass 117/117 per arm, with all 234 raw rows audited and 27.62% lower time, interval +18.20% to +37.84%. All 48 native sessions completed once. Facts are 24/24 per arm and citations 18/24 main versus 24/24 feature. Two feature sessions omit required saves. Gross tokens increase 8.32%; uncached tokens and time have lower point totals but negative interval lower bounds. Every failure remains blocking and retained.

The six optional runtime groups and `research_paths` remain removed. No source, tests, harness, oracle, prompt, rubric, threshold or budget changed during this trial. The bounded-input/adjacent-qualification protections, strict proof-admission checks, successful-save verification and raw canonical reconstruction remain in place. Full native/manual evidence and an exact offline public archive replay (same JSON, exit 1, no new sessions) are retained in [trial 41](results/benchmark-remediation/attempt-41/README.md). The frozen verifier does not separately gate every instruction; the explicitly false taint argument in one feature push is also retained as an additional observation.

Main ruleset 25440133 remains active with eight required checks, strict up-to-date enforcement and no bypass actors. There were no retries, exclusions, cache/time normalizations or gate waivers. No fixes or further run are scheduled. Repeatedly observed public development tasks and primary-assistant labels do not establish held-out production savings or independent approval.

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


## PR #3 post-audit correction

The four final-diff findings are corrected with regressions: keyword hints no longer
backtrack exponentially on long punctuated imperatives; prose compaction preserves
adjacent qualifications; both arms must successfully push after researched misses;
and local summaries/intervals are reconstructed from the canonical workload and
all raw observations. These harden acceptance without changing numerical gates.
Main's active ruleset now requires all eight statuses with strict up-to-date enforcement.

The user subsequently authorized one test/benchmark trial after the optional-runtime removals. Trial 41 passes 3/8 with retained test, protocol and performance failures; see the [complete evidence](results/benchmark-remediation/attempt-41/README.md). Every gate remains blocking and all previous failures remain unchanged. No code or test changes, fixes or retries were made during the trial. PR #3 is draft/unmerged and paused afterward.
