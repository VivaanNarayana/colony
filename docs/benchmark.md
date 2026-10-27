# Evaluation protocol

See the [benchmark independence audit](benchmark-audit.md) before interpreting the
historical optimization results. Passing development checks does not establish
production savings or merge readiness.

The included task set targets this public Colony repository: eight families, three
overlapping phrasings each, 24 tasks. Pin a commit before running. Print JSONL with
`python scripts/benchmark.py --tasks`. Questions include expected evidence paths and
reviewer rubrics; withhold these answers/rubrics from the evaluated agents.

1. Choose one agent/model/version and fix its prompt, tool budget, temperature, and
   repo commit. Set a hard provider spending cap before any live agent trial. This
   live CLI runner uses an explicitly authorized existing login; its between-task
   token stop is not a hard provider-dollar or in-flight token cap.
2. Randomize task order once, reuse that order in both arms, and run each arm with
   fresh agent context per task. Baseline has no Colony access. The Colony arm starts
   with an empty server and can ask first, research on misses, then push learned trails.
   Keep memory only within the Colony arm. Record cold starts as well as warm hits.
3. Measure actual model input+output tokens (including Colony tool results), elapsed
   wall time, all tool calls, and Colony ask calls. Count *relevant* returned trails
   as matches after independent human review; similarity alone is not relevance.
   Record validated matches and incorrect reused trails. Grade both arms against the
   expected behavior and frozen repo; do not equate unchanged file hashes with correctness.
4. Write one JSONL row per task and mode. Required fields:

   ```json
   {"task_id":"install-1","mode":"baseline","tokens":1200,"seconds":12.5,"tool_calls":4,"ask_calls":0,"matches":0,"validated":0,"wrong_reused":0,"correct":true}
   ```

   This row illustrates the schema; it is not a measured result. Modes are `baseline`
   and `colony`; every task must have exactly one row per mode. Unknown denominators
   produce null rates, not zero. The reporter rejects missing pairs and invalid counts.
5. Run `python scripts/benchmark.py --results measurements.jsonl`. Report total and
   per-family results, with model version, budget, order, and pinned commit. Compare
   proposed gates: reuse >=30%, validated hits >=80%, tokens >=25% lower, lower time
   and tool calls, and incorrect reuse <=5%, without reduced overall answer correctness.
   These are proposed pilot gates, not achieved results.
6. Record a two-minute demo: A pushes a build/setup answer, B asks a related question,
   show evidence status/provenance and skipped research, change the referenced file,
   ask again to show `stale`, then demonstrate a forbidden namespace and a tainted capture.

The live CLI/MCP integration tests automate the functional core of this demo. A real
agent trial, savings chart, and recording remain evaluation work after implementation;
do not substitute synthetic token estimates for measured savings.

## Live runner

With an authorized Codex CLI ChatGPT login and Colony installed in the current Python
environment, run `python scripts/live_benchmark.py --work <new-private-runtime-dir>
--output <new-results-dir>`. The runner fixes GPT-6 Luna low, seed 20261019, and the
original 24 questions and outer prompts. It alternates baseline/Colony order, uses fresh
sessions, starts with empty memory, and disables unrelated apps, plugins, web, shell and
multi-agent tools. Both arms use the same read-only repository MCP adapter. Only the
Colony arm gets the actual ask/push tools. Native JSONL token usage and all results are
retained, including model failures. The repository source is frozen with git archive;
only README.md, pyproject.toml and src/colony files can be researched. Use `--ref` to pin
a commit. No model API key is provisioned. The limit is 48 sessions, 240 seconds each,
and an 8-million-token stop checked between sessions; it is not a hard in-flight or
provider dollar cap. The Python environment must have installed the exact pinned checkout.

`--limit` can stop at a completed task boundary. `--resume` requires identical metadata,
an intact private server database, and a clean completed-task boundary. If a task was
interrupted after a push, start a new run; replaying it could contaminate the experiment.
The public output directory contains no credentials; the separate private work directory
contains keyfiles, encryption keys, and server state and must never be committed.

Grade retrieval relevance and answer correctness against frozen evidence before feeding
rows to the reporter. The initial live runs used primary-assistant review, not independent
blind human review. Report gross input+output tokens and cached-input-subtracted tokens
separately; neither is a measured dollar charge. Report validation both among useful hits
and among all raw candidates, and distinguish relevant returns from research-free reuse.
Live measurements also provide `raw_returns`. Incorrect reuse is divided by all returned
candidates, including wrong-topic hits; such hits do not become useful matches merely
because an agent used them. Legacy reporter rows without raw counts fall back to matches.
This accounting correction changes no questions, prompts, native usage, relevance labels,
or correctness rubric.

## Comparing lookup implementations

`python scripts/benchmark_lookup_live.py --baseline-ref <main-commit> --work
<fresh-private-runtime-dir> --output <fresh-results-dir>` compares main and the
current committed feature directly. It runs the same original 24 questions and
prompts through both Colony implementations, alternating which arm goes first
on each question. Both stores start empty and retain their own learned history;
both research the identical frozen main corpus. The runner exports main's code
and launches both frozen implementations through explicit bootstrap scripts,
so `--baseline-ref` selects the implementation as well as the corpus. Each server
and subject MCP process records its actual imported module paths and source hashes;
the runner and analyzer compare these with the pinned revisions. This avoids
relying on `PYTHONPATH` surviving a child tool process's environment filtering.

All 48 completed sessions, native usage and answers are retained. Both arms' research
adapters enforce a shared six-operation budget across search/read; later attempts
return errors without repository data, and their time/tokens still count. Model
policy failures are recorded throughout the full trial instead of selecting runs
by restarting at the first mistake. Attempted-call compliance is a blocking gate
alongside latency, tokens and correctness. Extra research
attempts must return budget errors; repeated memory calls must return identical data,
so neither arm gains a larger evidence budget. Import-integrity
failures still stop the run. There is no best-of selection or automatic retry.
Results still require relevance, correctness, citation and protocol review.
This repeatedly observed suite is a regression check, and an observed wall-time
or token win does not by itself establish statistically significant production
savings. Use the separate provider-free `scripts/benchmark_lookup.py` for paired
local latency, inference-work counts, and independent API-contract checks.

Every protocol violation in either arm blocks the comparison, including rejected
over-budget attempts, duplicate asks, and answers over the 130-word limit. All
eight gates remain blocking. The analyzer no longer emits an automatic
`merge_review_ready` or a partial performance-only pass. Historical reports retain
their original fields; they are superseded by the audit, not silently rewritten.
The shared research adapter reports its remaining call budget in every successful
response, including empty searches, and explicitly directs the subject to finish
when none remain. This feedback is identical in both arms; it changes neither the
six-call limit nor the violation accounting, original outer prompts or task rubrics.
Reads also return an exact `source` object with the same file hash for reuse in
`push.sources`. The audit requires pushed hashes to match actual preceding reads
and the frozen corpus, hashes the returned source bytes independently, and
preserves exact question punctuation and namespaces in both lookup and push checks.
The source object follows the content and uses the push field names directly;
duplicate `path`/`sha256` aliases are omitted. No model-provided hash is repaired
or normalized, and incorrect attempts still fail the audit even after a retry.

The quality gate requires every feature answer to meet the core-fact and citation
rubrics, with no citation loss. Each gross-token, uncached-input-plus-output, and
wall-time gate also requires the lower bound of its paired family bootstrap
reduction interval to exceed zero. A point estimate alone cannot pass. These
intervals describe this public suite; they do not correct repeated selection,
make correlated paraphrases independent, or establish production generalization.
Fresh live runs freeze task identities, questions and family assignments in the
protocol before subject sessions. Integrity checks remain active under `python -O`;
malformed evidence produces a retained failure report and a nonzero exit.

## Isolated local contract benchmark

```powershell
python scripts/benchmark_lookup.py --baseline-ref <baseline-sha> --feature-ref <candidate-sha> --runtime <fresh-runtime> --output <results> --name lookup --seed 20261021 --variants 3 --repetitions 3
```

Both implementations are complete `git archive` exports loaded in separate Python
processes. Their full source hashes and actual module paths are checked; an
editable install or inherited `PYTHONPATH` cannot substitute current dependencies
into baseline. Both use the same installed third-party environment and model
configuration. Provision local models as described in the repository setup before
running. Only committed application code is measured.

The versioned workload generates source-backed facts and commands outside this
repository's subject matter. It includes paraphrases, evidence-only answers,
unrelated queries, empty memory, denied namespaces, absent scopes, changed/deleted
sources, expired memory, missing sources, and an overview that cannot answer a
procedure. The independent oracle checks required source facts, status, scope,
current source hashes and excerpt line locations. Neither baseline agreement nor
similarity scores establish correctness. Product scores and vectors are not mocked.

The workload, harness fingerprints, revisions, seed and repetition count are
written before importing application code. Model setup, pushing and warmup are
excluded from measured asks. Task order is randomized reproducibly; paired arm
order alternates, and workers do not perform timed work concurrently. Every
observation and oracle error is flushed to JSONL. Existing run names cannot be
overwritten. Initialization failures leave the plan and worker stderr for diagnosis.

Exit 0 requires both arms to pass every oracle check, distinct application source,
and a positive lower latency-interval bound. Exit 1 means a quality failure,
inconclusive performance, or an A/A control; inspect the saved summary. An A/A run
uses the same revision in both flags and must never qualify as an optimization.
Inference-work counters are diagnostic, not substitute quality or speed gates.

These public fixtures are development checks, not a hidden test set. They currently
use small single-trail stores and warmed CPU inference, and do not establish large
store scaling, concurrent-load behavior, cold-start cost or agent-level savings.
Before making a new general performance claim, fix the revisions, sample size,
run budget and stopping rule in advance; reserve externally authored repositories
and questions from implementation tuning; and grade shuffled, arm-blinded answers
and citations independently. Publish every scheduled trial and failed run. A
failure becomes development feedback; selecting a later green run on the same
questions is not independent confirmation.


## Scope and accounting limits of this change

This PR validates realistic measurement and honest failure reporting. Product
runtime optimization is separate. A valid benchmark may report that the tested
implementation fails quality or performance requirements.

No native runner is launched by evaluator self-tests. Provider-free validation uses
synthetic retained-event fixtures, the unchanged genuine trial-22 local fixture,
import-isolation probes and an A/A control. Every performance gate, workload,
confidence requirement, prompt, model and budget remains unchanged.

The local metric is warm lookup time, excluding setup, pushes, cold starts and
concurrency. Native per-task process time covers CLI/MCP startup, model and tool
work inside that process interval; shared server setup and orchestration outside
it are not silently allocated to one arm. Native turn usage does not certify all
account-billed activity or hidden startup requests, and exact request/tool costs
cannot be reconstructed without additional diagnostics. Report these boundaries
alongside results. The separate CLI/OTel investigations are not part of this PR.

An incomplete native trial establishes no passing verdict. Retain its prompts,
partial native events, errors and stderr. A failed session may lack a final usage
record or task row; report that accounting as unknown rather than zero, and do
not exclude the failure, resample a session or present a successful-only total.

`scripts/live_benchmark.py` is the retained legacy Colony/no-Colony runner; do not
assume it meets every updated strict auditing requirement. The strict
`benchmark_lookup_live.py`/`analyze_lookup_live.py` pair compares two Colony
implementations. Neither retrospective historical comparisons nor a standalone
baseline establish a concurrent matched product-savings result.

See [the independence audit](benchmark-audit.md) for the validity controls and
[the evidence record](benchmark-evidence.md) for unchanged archived outcomes.
