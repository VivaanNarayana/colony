# PR15 gross-token follow-up

This follow-up keeps PR15 draft. It changes no historical native result, evaluator
contract, acceptance gate, default budget or permission. No new native sessions are
authorized or dispatched by this document. Offline representation counts establish
only a local reduction; they cannot establish complete-workflow token savings.

## Audited same-commit variance

The two 28-session comparisons at
`9afb776e9647bbf22f56ae37ef808719607ae300` used identical frozen protocol and explicit
prompt bytes, with matching runtime/client/collector pins and normalized arguments.
Their complete fixed horizons produced:

| Gross tokens | First run | Second run |
| --- | ---: | ---: |
| No-Colony | 773,244 | 699,775 |
| Colony | 742,423 | 753,908 |
| Colony minus No-Colony | -30,821 (-3.99%) | +54,133 (+7.74%) |
| Learning gap | +66,235 | +65,864 |
| Reuse gap | -97,056 | -11,731 |

The gap moved 84,954 tokens: baseline usage fell 73,469 while Colony usage rose
11,485. The baseline accounts arithmetically for 86.5% of that swing. This is a
decomposition, not a causal estimate. Input contributes 84,943 tokens of the swing;
output contributes 11. Cached input remains part of gross usage.

Out-of-repository misses account for 67.6% of the gap swing and designated broad
hits for 30.9%. Both broad Colony hits still delivered all three published files,
at 2,013/2,042 UTF-8 bytes, without subsequent research in both runs. Their combined
gross usage changed by only 78 tokens. The learning penalty remained about 66K.
Neither result establishes a stable gross-token win. The first run's baseline
correctness/grounding failure and the second run's baseline format failure remain
failed under the unchanged strict quality gate; no case is removed or regraded.

The logs contain aggregate turn usage and individual MCP calls, with no provider
request, attempt, host exchange or CodeMode batch accounting. One baseline case
returned the same six-call/result multiset but differed by 14,229 input tokens.
Visible tool count, result bytes and read order cannot determine request count or
repeated fixed-context cost. Model/host execution variation is supported; exact
attribution to extra exchanges, discovery, batching or retries remains unresolved.

The read-only review artifacts are under:

* `D:/Codex/colony/pr15-variance-analysis-1793764109/`: `findings.md`,
  `findings.json`, `paired-cases.json`, `comparability-and-payloads.json`.
* `D:/Codex/colony/pr15-gross-followup-review-1793767000/`:
  `audit.py` and `arithmetic-audit.json` independently recompute whole-horizon and
  phase arithmetic from the original reports, binding them by SHA-256.
* Original reports/raw evidence remain in
  `D:/Codex/colony/pr15-checks-eval-1793760007/` and
  `D:/Codex/colony/pr15-repeat28-1793762104/`.

## Scoped implementation and verification

The implementation removed redundant adaptive MCP tool descriptions and duplicate
Git/read-only instruction clauses. The adaptive policy remains self-contained in each independently loaded
surface. Do not remove it from server instructions merely because the controlled
evaluator also places policy in its prompt: ordinary client loading is different.
Keep lookup, evidence validation, source identity, taint, citations, mutation replay,
authorization, Git mismatch, read-only and lifecycle semantics intact. No classifier,
automatic publication, negative cache, research interception or benchmark-specific
search rule is introduced. No-budget MCP behavior remains unchanged.

Complete initialized server instructions and tool catalogs were compared before and
after the change. Local representation measurements were:

| Adaptive configuration | UTF-8 bytes before/after | `o200k_base` tokens before/after |
| --- | ---: | ---: |
| Manual writable | 3,694 / 3,498 | 749 / 713 |
| Bound writable | 3,741 / 3,521 | 766 / 724 |
| Bound read-only | 2,016 / 1,874 | 389 / 364 |
| Git-aware writable | 3,888 / 3,568 | 793 / 733 |
| Git-aware read-only | 2,163 / 1,921 | 416 / 373 |

Exact metadata snapshots and the measurement script are retained at
`D:/Codex/colony/pr15-fixed-context-followup-1793765690/` as
`discovery-before.json`, `discovery-after.json`, `discovery-comparison.json` and
`measure_discovery.py`. All five no-budget metadata packets are identical. Adaptive
tool schemas, annotations and every tool field except descriptions are unchanged.
An independent actual MCP `initialize`/`list_tools` ClientSession round trip over
in-memory protocol streams confirmed these invariants for all ten configurations;
its script, snapshots and audit are in
`D:/Codex/colony/pr15-gross-followup-review-1793767000/` as
`discovery_transport.py`, `transport-discovery-snapshots.json` and
`transport-discovery-audit.json`. No network/provider request was made.

Independent adaptive MCP, workflow, Git-client and Git-integration guards passed
86 tests, with `guard-tests.xml` and `guard-tests.log` in the same review directory.
These local measurements are representation counts: do not multiply them by an
unobserved provider request count or label them native gross-token reductions.

Miss fallback and learning exchanges remain the main measured targets, but the
existing trace cannot show how much of their usage this instruction reduction could
recover. The controlled recipe forces an initial ask and research/publication in
declared learning slots; selective ordinary-workflow guidance is not evaluated by
that recipe. Changing routing or publication policy needs its own scoped design,
quality controls and prospective evaluation, while retaining this controlled test.

## Prospective evaluation decision and limits

No native campaign is proposed now for a small metadata reduction. Two same-commit
runs on two public synthetic lineages cannot justify a powered sample size,
reliability claim, arbitrary repetition count or a repeat-until-green rule. Local
metadata savings alone do not establish a material full-workflow effect against the
observed 84,954-token gap swing. Stop after offline verification and report that
limit honestly if the scoped change cannot plausibly address the dominant costs.

If a later independently reviewed change provides a material reason to measure a
new gross outcome, freeze a fresh exact protocol and obtain separate authorization
before dispatch. A bounded descriptive pilot may use at most one 28-session
controlled campaign, preserving the 14 matched task/phase cases and actual finite
reuse horizon. This is one observation of the new candidate, not a reliability
sample or a causal test. Do not pool it with old candidates or run a replacement
after an unfavorable, failed or incomplete outcome.

The proposed upper bounds retain the previously used limits:

| Limit | Bound |
| --- | ---: |
| Total attempted native sessions, across both arms | 28 |
| Retries or replacement attempts | 0 |
| Per-session elapsed time | 120 seconds |
| Total campaign wall time | 3,600 seconds |
| Observed aggregate gross-token stop | 2,300,000 tokens |
| Generated campaign files | 1 GiB |
| Minimum free D: storage | 5 GiB |

The gross stop is an observed-usage stop, not a provider-enforced spend ceiling;
in-flight or unreported usage can exceed it. Count every attempt and retain all
failure, timeout, learning, publication and retry/unknown-cost records. Stop before
the next dispatch when any bound is reached; retain the interrupted result rather
than restarting or dropping its costs. A bound that the launch tooling cannot
enforce must fail preflight, not be described as enforced.

The frozen manifest must bind the exact product/client revisions, installed runtime,
collector and overlay/catalog hashes, prompt/source bytes, model/effort, tools,
explicit budget value and encoding, task order, fresh independent stores, reviewer
rubric, output format, fixed horizon, limits and zero-retry policy. A future launch
must bind its final revision and frozen manifest; this document is not a launch
protocol. The existing collector stays frozen in this change. Its missing
request-level accounting must be disclosed and rules out causal request-cost claims;
instrumentation would require a separately reviewed and versioned collector change.

Freeze independent cost-blind correctness, grounding, citation, format, controlled
journey and tool-policy labels before opening new token totals. Preserve the strict
all-case gate; a quality failure cannot be offset by gross savings. Report gross
as input plus output, cached input separately, and billing as unknown unless
measured. Any unknown usage prevents a complete savings claim. A lower complete
total with matched success is a descriptive candidate result only. Unchanged or
higher gross, failed quality, missing accounting or exhausted limits ends the pilot
without relaxing checks or authorizing another run. Required token evidence remains
unsatisfied until its existing acceptance criteria actually pass; passing offline
checks does not authorize merge or automerge.
