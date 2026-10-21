# Evaluation protocol

The included task set targets this public Colony repository: eight families, three
overlapping phrasings each, 24 tasks. Pin a commit before running. Print JSONL with
`python scripts/benchmark.py --tasks`. Questions include expected evidence paths and
reviewer rubrics; withhold these answers/rubrics from the evaluated agents.

1. Choose one agent/model/version and fix its prompt, tool budget, temperature, and
   repo commit. Set a hard provider spending cap before any live agent trial. This
   repository neither launches paid models nor asks for their credentials.
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
local latency and inference-work measurements on identical seeded memory.

Every feature-side protocol violation blocks merge-review readiness, including
rejected over-budget attempts and duplicate asks. `merge_review_ready` requires
every gate to pass. An earlier advisory interpretation was rejected by the user;
historical artifacts retain that mistake, and it is superseded by this policy.
The shared research adapter reports its remaining call budget in every successful
response, including empty searches, and explicitly directs the subject to finish
when none remain. This feedback is identical in both arms; it changes neither the
six-call limit nor the violation accounting, original outer prompts or task rubrics.
Reads also return an exact `source` object with the same file hash for reuse in
`push.sources`. The audit requires pushed hashes to match actual preceding reads
and the frozen corpus, and preserves exact question punctuation in lookup checks.
