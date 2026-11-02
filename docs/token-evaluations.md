# Memory-use token evaluations

This developer-only harness asks whether actual shared memory helps a fresh agent
complete source-grounded work. It adds no Colony runtime behavior, dependencies,
defaults or CI campaign. Ordinary tests use synthetic events and local code, never
a provider. Passing them proves harness controls, not token savings. Historical
PR14/PR15 evidence and statuses are unchanged.

The entrypoint is `python scripts/token_evaluation.py --help`. Run it in a dev
environment with this repository installed. It supports local preparation and
preflight, separately authorized native collection, arm-masked review and scoring.
All commands preserve existing evidence: choose new output directories/files.
Windows generated data must be on D:. Keep private credentials, logs and answers
outside Git. Source fixtures may be read from the checkout.

## What is compared

Freeze one track per manifest; do not pool tracks or change them after seeing results.

| Track | Candidate behavior | What it answers |
| --- | --- | --- |
| `natural` | Installed guidance is available; no mandated ask/push | Does the agent choose to use memory, and what overhead/results follow? |
| `controlled` | Learning asks an empty store, researches and publishes; fresh reuse agents ask first, then research gaps | Can actual publication and validated reuse help when the mechanism is exercised? |

Both arms use the same model, effort, research-call limit, questions, selected
answer policy and source snapshots. No-Colony has only the same repository research
tools. Candidate additionally pays for Colony schemas, guidance, asks and pushes.
The client is ephemeral per task. Each candidate workload has a fresh authenticated
team: A can publish during learning; B is pull-only during reuse. Stores remain
alive across the declared learning/reuse horizon and never share across workloads.
Task order is fixed; arm order alternates. There are no retries.

Two authored public source lineages are included: a gateway configuration/retry
flow for development and a worker queue/retry flow for validation. Both are
synthetic and visible to developers, **not held out or representative traffic**.
Their independent typed facts, source coordinates and changed v1/v2 snapshots
exercise these cases:

| Case | Evidence needed beyond a plausible answer |
| --- | --- |
| Cold learning | Actual authenticated empty ask, source research and successful sourced publication; charge all of it |
| Useful hit | Prior actual learning/publication, current validated ask evidence, correct grounded answer, no redundant repository tool calls |
| Miss | Actual `no_relevant_trail` response and independently reviewed answer |
| Stale evidence | Declared changed source snapshot, actual stale/refusal or safe current evidence, correct current answer |
| Insufficient evidence | Actual insufficient/refusal response and source fallback; distinguish known wrapper behavior from unspecified injected backend behavior |
| Cheap direct read | Optional memory use; compare its overhead against reading a small source directly |

A scenario is an expectation, not a preseeded outcome. If the target returns a hit
for the miss question, or the learner never publishes, controlled coverage fails.
An uncalled integration in the natural track is an adoption result; it does not
measure useful-hit savings. Prompt-cache hits are reported separately from memory
hits. Quality labels still require independent review; matching hashes cannot
establish that a claim is true.

## Prepare and check locally

For Windows development, first use the workspace's existing D-storage environment
setup. Set temporary/cache paths on D: and point offline model variables at already
provisioned model snapshots; preflight never downloads models. Example commands
below have placeholders and do not authorize or start native sessions.

```powershell
python scripts/token_evaluation.py template --target-python D:/env/Scripts/python.exe --target-repo D:/source/colony --revision FULL_COMMIT_SHA --client C:/path/to/codex.exe --model EXPLICIT_MODEL --track controlled --workload gateway --output D:/evaluations/protocol.json
python scripts/token_evaluation.py prepare --manifest D:/evaluations/protocol.json --output D:/evaluations/run-001
python scripts/token_evaluation.py preflight --run D:/evaluations/run-001
```

`template` compares **every installed `colony/*.py` byte hash** to the Git blobs at
the declared revision, pins the native executable hash/version, and freezes all
collector Python hashes. A dirty or CRLF-translated target install that differs
from Git is rejected: use an isolated clean LF export/install. No branch switch is
needed. A future target branch can be exported read-only and installed separately;
never move implementation work to that target branch as part of this feature.
Collector and original/copied fixture hashes are rechecked before dispatch.

Select one workload for 12 sessions (six tasks times two arms); the default two
lineages require 24 sessions per track. The manifest records exact limits; these
counts are not permission to run them. Context flags must agree between explicit
MCP and setup arguments. For example, supply option/value separately as
`--mcp-arg=--context-budget --mcp-arg=adaptive` and matching setup args when the
selected target supports that mode. Unsupported target flags fail preflight.

Preflight invokes only local client help/version/features metadata, target onboarding/setup, an
owned offline HTTP server and actual authenticated stdio MCP role/empty-store
controls. It checks that B exposes only `ask`; it does not publish seed memories.
Execution repeats role/authentication/empty-store controls on its actual fresh team
before any native model session. It checks source identity before/after each task
and applies only frozen, declared snapshot transitions.

**Configuration fidelity limitation:** the adapter runs the target's real managed
setup and passes its generated guidance in the candidate prompt, with explicit MCP
configuration. Each native client runs in a separate empty task directory outside
the managed source project, so those generated AGENTS/config files are not also
loaded through its working directory. It uses `--ignore-user-config` and
`--ignore-rules` for isolation.
This is a manual delivery mode; it does not establish that the native client's
ordinary project-config/AGENTS loader reads those files. CLI flags and parsing of
the exact config/feature overrides are preflighted without models. Effective live
behavior still requires actual tool traces. A campaign must disclose this
limitation and inspect those traces.

## Native collection requires separate bounded authorization

Implementation/testing does not authorize a paid/model campaign. Only after an
owner approves the exact frozen protocol, create a private authorization JSON:

```json
{
  "protocol_sha256": "FROM_RUN_JSON",
  "revision": "EXACT_TARGET_REVISION",
  "model": "EXACT_MODEL",
  "limits": {"COPY": "the entire exact limits object from run.json"},
  "owner_intent": "The owner's explicit approval of this bounded run"
}
```

The limits object above is explanatory, not executable. Authorization must match
every actual limit, including zero retries, time, attempts, disk and gross tokens.
The file records approval; writing one does not grant yourself approval.

```powershell
python scripts/token_evaluation.py run --run D:/evaluations/run-001 --execute-native --authorization D:/evaluations/authorized.json --auth-home D:/private/existing-client-auth
```

Only an explicit existing `auth.json` and optional bounded `models_cache.json` are
copied into the private run client home; provider secrets/global settings are not
forwarded. No credentials are provisioned. All processes and servers are owned by
the run and stopped on completion, timeout or interruption. A started run cannot
be restarted or overwritten. Partial/orphan invocations/logs remain inventoried;
unknown usage stops further dispatch.

Gross-token, disk and free-space stops are sampled **between sessions**, so the
last session can exceed them. Session and wall-time limits constrain the running
native process; server initialization has its own local timeout. They are not a
provider-enforced token/dollar ceiling. Dollar charges are unknown. Keep the
owner's approved margin sufficient for one final session.

## Review before looking at performance

```powershell
python scripts/token_evaluation.py inventory --run D:/evaluations/run-001
python scripts/token_evaluation.py mask --run D:/evaluations/run-001 --output D:/evaluations/review-001
```

Give the independent reviewer only `answers.json`, the frozen source snapshots and
the frozen rubric, without costs, arm identities or `private-map.json`. Answer text
may incidentally reveal memory use; arm masking is not guaranteed perfect blinding.
The chosen extraction policy is frozen before collection. `last_agent_message`
selects the last completed agent message, and `all_agent_messages` joins all of
them. If the client omits phase metadata, final-phase provenance is not guaranteed.
All agent messages still incur usage, including narration outside the selected
answer. JSON compliance, when requested, is judged independently.

The reviewer writes an arm-blind file containing `reviewer`, `method`, and `labels`:

```json
{"reviewer":"independent reviewer", "method":"arm-masked source review",
 "labels":[{"case":"RANDOM_CASE_FROM_ANSWERS", "correct":true,
             "grounded":true, "format_compliant":true}]}
```

Every case needs explicit Boolean labels. The coordinator then binds random case
IDs back to attempt IDs and exact answer/current-source/oracle/rubric hashes:

```powershell
python scripts/token_evaluation.py bind-quality --packet D:/evaluations/review-001 --review D:/evaluations/blind-labels.json --output D:/evaluations/quality.json
python scripts/token_evaluation.py report --run D:/evaluations/run-001 --quality D:/evaluations/quality.json --output D:/evaluations/report.json
```

`quality-template` also produces a private coordinator template with null labels;
null is never a pass. The report rejects altered answers, sources, protocol, labels,
duplicate/missing tasks, invalid task order and unlisted events/invocations. Incomplete
schedules cannot produce a matched report: use `inventory` or the partial
`collection.json` inventory to retain known costs and failure details. Do not throw
those costs away or silently rerun them.

The report sums every completed native turn, learning and reuse, failures and all
output. It separates input, cached input, output, gross and uncached-plus-output
tokens; missing usage is unknown, never zero. It reports observed adoption, useful
reuse, mechanism expectations, quality, per-phase costs, session-time sums and
cost per successful workflow. With no successes that cost is undefined. Session
sums are not end-to-end wall time. A complete qualifying native run may show
descriptive paired reductions; it never creates a confidence-supported product win,
dollar savings or automatic merge approval. Native receipts are auditable local
records, not attestation against a malicious collector.

## Provider-free verification

```powershell
python -m pytest tests/token_evaluations -q
python -m ruff check scripts/token_evaluation.py scripts/token_evaluation tests/token_evaluations
```

Tests exercise source-derived facts in both lineages, snapshot/citation corruption,
bounded research (including invalid calls), role/permission controls, answer
extraction, independent labels, complete raw usage inventories, failure/unknown
usage, interruption cleanup and native authorization gates. Mock native receipts
and fake events are labelled synthetic; they establish no measured token benefit.
Any later savings claim requires separately authorized native runs, reviewed raw
traces and independent correctness/grounding evidence at the fixed reuse horizon.
