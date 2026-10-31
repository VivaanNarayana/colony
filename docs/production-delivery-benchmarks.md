# Lightweight feature delivery and product-value evidence

This benchmark-only addition runs over the restored **PR1 MVP**
`202745a92f6c6c59e4c990321be9c5112697594e`. Application files under `src/colony/`
and `pyproject.toml` remain identical to that revision. The rollback itself is
separate, on `revert/restore-pr1-mvp`; this branch is stacked on it.

The contract identifier is `colony.production-delivery/1`. It does **not** replace,
publish, waive or reinterpret the eight historical required checks. GitHub rules
are unchanged. Neither draft has an eight-gate acceptance or merge-readiness claim.
An owner must separately review any future change to delivery policy.

## Keep ordinary feature work small

For routine features, run the product tests and lint, add a focused check for the
changed behavior, and describe the intended result and any compatibility risk in
the PR. Existing tests cover permissions, stale evidence, scrubbing, CLI/HTTP/MCP
and offline retry. Use build/install or the local diagnostic below when the change
affects those paths. Do not launch a live campaign for every edit.

Token savings remain a primary product objective. At a release or when claiming
better reuse, review a small matched **Colony versus no Colony** scorecard:

| Core question | Data to retain |
|---|---|
| Does memory reduce model work? | Native input, cached input, output, gross and uncached tokens, including learning/push/retry/failure costs |
| Are answers still useful and safe? | Independently reviewed correct and grounded tasks/workflows, failed attempts and unknown labels |
| Does it save user time? | Recorded session durations; separately measured end-to-end time when available |

A candidate-versus-MVP comparison answers a different question. Neither comparison
requires pretending every feature strictly improves every metric. Correctness and
the claimed behavior matter for delivery; token savings need real product evidence.
These are proposed review practices, **not changed merge rules**. The existing eight
checks remain required; this draft cannot unblock merges by bypassing them.

## Small offline product-value scorecard

`scripts/product_value.py` is a stdlib report reader, not another trial runner.
It launches no agents, servers, providers or telemetry. Use it on separately
authorized, retained trial evidence; tests use fake native events only.

```sh
python scripts/product_value.py --manifest <frozen-evidence.json> --logs <native-log-directory> --output <new-scorecard.json>
```

The version is `colony.product-value/1`. The one JSON manifest contains:

* `contract`: that version, and `protocol`: `comparison` = `colony_vs_no_colony`,
  exact `implementation`/`corpus` revisions, `model`, `client`, `effort`,
  `instructions_sha256`, research `budget`, the intended `memory_difference`,
  `rubric_sha256`, `reviewer` and `review_method` (disclose nonblind review).
* `protocol.workflows`: unique `id` and ordered `tasks`, each with `id`, `question`
  and `phase` (`learn` or `reuse`). Both arms complete the same scheduled tasks.
  Include actual learning and a finite observed reuse horizon; do not amortize
  costs over hypothetical future users. Fresh task contexts, identical research
  budgets and explicit access to Colony are part of the collection design.
  Learning precedes reuse; each arm follows the declared task order, with retries
  contiguous within that task. A late retry cannot retroactively repair prior reuse.
* `attempts`: every attempt in execution order, including learning, retries and
  failures. Each has unique `id`, `workflow`, `task`, `mode` (`no_colony` or
  `colony`), relative `events` path ending `.events.jsonl`, `events_sha256`,
  `state` (`completed`/`failed`/`timeout`), measured `seconds` or null, externally
  reviewed `correct`/`grounded` Booleans or null, and `answer_sha256`.

Freeze the protocol before collection; freeze the complete inventory and quality
reviews before scoring. The scorecard hashes this manifest and every native log.
The fake-event fixture in `tests/production_delivery/test_product_value.py` gives
a complete minimal example. Keep native logs and private answers outside Git.

Supported native JSONL uses `turn.started`, `item.completed` agent messages and
`turn.completed` with per-turn `input_tokens`, `cached_input_tokens`, `output_tokens`.
Sum all completed turns, not just the last. Gross = input + output; uncached =
input - cached input + output. Auxiliary usage is retained separately and never
added twice. The answer digest is SHA-256 of completed agent-message texts joined
with a single newline, UTF-8 encoded, with no trailing newline.

All `.events.jsonl` files in the selected directory must be inventoried exactly
once. Missing scheduled tasks, duplicate attempts/turn completions, changed hashes,
invalid counts or review-answer mismatches reject the report. A failed/interrupted
attempt or missing usage leaves complete totals and savings unknown, while known
subtotals remain visible, including reported usage on failed turns. A partial usage
object is incomplete; a malformed supplied count is rejected. Known derived token
subtotals include only turns with all components needed for that formula, so they
may not equal arithmetic on partial component subtotals. Missing quality is not
success. The final attempt for
each task determines its outcome; every earlier cost stays counted. Costs per
grounded successful workflow include unsuccessful-attempt costs and are undefined
with no successes or incomplete usage. Session sums are not workflow wall time.

This deliberately small reader accepts at most 128 attempts, a 1 MiB manifest and
64 MiB native logs, writes only a new report and never deletes input. It checks
inventory/accounting consistency; it cannot attest collection honesty, source
identity, externally supplied quality or historical model configuration. Retain
the original runner's source/prompt/process evidence and review it with the report.
Point reductions are descriptive: no confidence-supported product win, dollar
savings, automatic readiness or legacy-gate verdict is produced. Cheap incorrect
answers cannot establish product benefit. Historical MVP/feature trials cannot
be relabeled Colony/no-Colony. No new live trial is authorized by adding this reader.

## Implemented coverage and boundaries

| Implemented component | What it establishes | What it does not establish |
|---|---|---|
| Six tasks across three synthetic configuration repositories | New source-derived development facts, separate from the old Colony setup questions | Real traffic distribution, held-out repositories or natural-language correctness |
| Frozen Git source exports and fresh offline workers | Actual implementation hashes/module paths; independent disposable arm histories | Provider prompts or unobservable client billing |
| Empty miss, sourced push, repeated hit, namespace isolation, changed-file fallback | Direct Store contracts against an independent JSON source oracle | Agent tool obedience or HTTP authentication (covered separately by product tests) |
| Restart and SQLite backup API plus encrypted payload/key restoration | Recovery on disposable fixture state | Permission to open/migrate a user's real store |
| 0/1/4/12 reranked passages, 64/512/4000-character inputs, duplicates/mixed lengths | Explicit passage counts and lengths, original order and finite score observations | Real usage frequencies, score equivalence or threshold quality from timing alone |
| Fresh-process model loading, Store initialization, push/index, warm calls, 1/2/4 local clients | Local operation/process times, CPU and process lifetime peak RSS | Agent workflow time, network provisioning time or production capacity |
| Raw observations, configuration hashes, complete-case replay and corruption tests | Missing/duplicate/altered evidence and inconsistent labels are rejected | Cryptographic attestation against a malicious host or reviewer |
| Source/citation utilities accepting real Markdown heading anchors | Actual in-repository paths, hashes, supported line/heading coordinates | Proof that arbitrary prose is true merely because a citation exists |

No result has been collected by introducing these commands. Provider-free tests
exercise controls and fake observations; their durations are not benchmark data.
The existing MVP suite separately covers real CLI, HTTP and MCP, signed permissions,
secret scrubbing/capture, changed evidence and offline retry. Fresh build/install
checks are reported in the PR; historical results are never inherited.

The synthetic task oracle evaluates **structured JSON fixture facts** retrieved
from authored trails. It checks the source independently, the exact field/type/value,
fingerprints, taint and untrusted provenance. It cannot grade arbitrary model prose.
Future live workflow evaluation needs independent fact/citation review, frozen before
performance analysis. These public fixtures must not be called held-out production data.

## Freeze and collect (not automatic checks)

Only start collection when a bounded local experiment is explicitly intended.
The default comparison is fixed PR1 versus PR1, an A/A diagnostic. A candidate
comparison uses the same fixed baseline and identical cases. **Colony versus no
Colony is a separate study**; the offline scorecard above implements its accounting,
but these local operations cannot answer whether enabling Colony helps an agent.

Commit the harness and set the existing provisioned model directories; there is
no download fallback. Freeze pins all files in both model directories, implementation
exports, fixtures, scripts, Python/platform/package metadata and case order:

Only the harness repository is supported; alternate `--repo` clones are rejected
before freezing or collection. Both pinned revisions must exist in its Git history.
The collector and workers verify the frozen Python/platform/package environment;
workers verify pinned model files before and after their operations.

```sh
python scripts/production_delivery.py freeze --candidate <commit> --output <new-plan.json>
python scripts/production_delivery.py run-local --plan <new-plan.json> --output <new-private-output>
```

Required environment variables: `COLONY_EMBED_MODEL_DIR` and `COLONY_RANK_MODEL_DIR`,
each pointing to an existing local ONNX snapshot. The worker sets Hugging Face and
Transformers offline modes and blocks socket connections. It never invokes an agent,
provider SDK, Codex executable, browser or billing endpoint.

The output must be a **new** directory. There is no resume, overwrite, selective
task retry or automatic cleanup. `PRIVATE-runtime/` contains disposable stores and
encryption keys: do not publish or commit it. Source/fixture copies never point
at a production database. The source archive and artifact traversal reject links.

Limits are 600 seconds for the whole collection, 1 GiB of new artifacts/private
runtime, at least 5 GiB remaining free space and at most four local clients.
The parent monitors the worker and stops on a ceiling; partial observations,
stdout/stderr and the incomplete execution record remain. A ceiling is an
operational safeguard, not a latency SLO or proof of adequate statistical power.
No other project, global temp folder or existing trial is pruned to make room.

The `report` command audits an existing complete execution and refuses partial
collections, modified source/fixture/harness bytes, duplicate/missing cases,
changed questions/namespaces, invalid timings and labels inconsistent with raw
source evidence. Existing summaries are never overwritten; an error is nonzero,
not permission to reuse an earlier summary as a new verdict.

```sh
python scripts/production_delivery.py report --plan <plan.json> --output <private-output> --report-file <new-report.json>
```

Reported warm percentiles describe the **equal-weight synthetic task mixture**,
not production traffic. Per-task warm distributions and each rank stratum are
also retained. p95 is unavailable below 20 samples in a group and p99 below 100.
Model loading, push/indexing, per-operation timing and complete worker
wall time have separate boundaries. Peak RSS is the process lifetime high-water
mark (or unavailable), not memory allocated by each individual request. Observer
and resource-check overhead is present; this is a diagnostic, not a maximum
throughput study. No confidence interval, optimization claim or automatic
production-readiness verdict is emitted, even when one arm looks faster.
This initial descriptive diagnostic always runs MVP before candidate. OS file-cache
effects are uncontrolled; a future confirmatory comparison needs a frozen
counterbalanced design before collecting observations.

## Provider-free check commands

```sh
python -m pytest -q tests/production_delivery --confcutdir=tests/production_delivery
python -m pytest -q
ruff check .
```

The focused controls use fake source/observation/model data, not model-provider
requests or measured performance samples. Full product checks may use pinned local
ONNX models and loopback servers; no external model service is needed. Run tests
with distinct disposable temporary directories and retain every failed attempt.

## Prospective delivery proposal (not adopted)

Preserve immutable legacy reports and source identities. A future owner-reviewed
policy should separate:

* Product delivery: correctness, security/scope/privacy, functional recovery,
  build/install, reproducible evidence and agreed regression budgets.
* An optimization claim: a prespecified primary user-relevant endpoint, meaningful
  effect, representative workload and uncertainty analysis. A local latency
  change need not reduce unrelated model token totals to be useful.
* Evaluator delivery: independent oracles/corruption controls and A/A behavior.
  Correctly reporting no supported win is a valid evaluator result.
* Rollback: exact PR1 tree plus applicable functional/safety checks, openly
  acknowledging loss of PR2's measured warm lookup optimization.

This document configures no required status, threshold, rule or exception.
Do not fabricate an old passing check or bypass protection to merge these drafts.
If the existing approved publication route cannot represent a proposed future
contract, report that dependency; do not build another policy or publisher service.

## Release experiments still pending

1. Representative public/licensed repositories, development versus repository-level
   held-out task lineages, independently authored source rubrics and valid citations.
2. Actual Agent A learning and independent Agent B reuse; misses, partial/irrelevant/
   contradictory evidence, code changes and denied namespaces. Correct useful task
   completion is essential; a fast refusal is not a successful task.
3. Colony versus no Colony for product value; a separate fixed-MVP/candidate study
   for later optimization. Identical model/version/effort, allowed research and
   explicit memory-access differences; independent stores/contexts and counterbalanced
   order, all failed/invalid/inconclusive attempts retained. The offline accounting
   reader is implemented; fresh collection and product benefit remain unestablished.
4. Adequate repeats/scales and representative concurrency; percentile/uncertainty
   methods and meaningful regression margins fixed before confirmatory observations.
   Pilot tuning is not held-out confirmation; no unchanged retries until green.
5. Model provisioning/download time, larger corpus scaling, real CLI/HTTP/MCP
   workflow performance and supported OS/Python combinations. Untested platforms
   remain unverified. No production telemetry is implemented or collected.
6. Fresh explicit authorization for any live campaign, with revisions, client/model,
   workload and enforceable session/token/time/disk/spending limits. Prior PR3-limit
   exceptions are consumed. Between-session token stops are not hard dollar caps.
   If complete usage or billing cannot be observed, report it unknown; gross and
   uncached tokens are separate cost proxies, not measured dollar charges.

PR4 remains paused, draft and unmerged. None of this resumes its implementation,
client recovery, telemetry proposal or any hosting/merge-policy infrastructure.
