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
