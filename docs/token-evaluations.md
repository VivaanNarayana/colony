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
| `controlled` | Required learning/reuse cases exercise the declared mechanism; optional cases follow installed guidance | Does the declared mechanism work, and what does this controlled recipe cost? |

Both arms use the same model, effort, research-call limit, questions, selected
answer policy and source snapshots. No-Colony has only the same repository research
tools. Candidate additionally pays for Colony schemas, guidance, asks and pushes.
The client is ephemeral per task. Each candidate workload has a fresh authenticated
team: A can publish during learning; B is pull-only during reuse. Stores remain
alive across the declared learning/reuse horizon and never share across workloads.
Task order is fixed; arm order alternates. There are no retries.

Controlled learning names the same relevant source paths to both arms. Only Colony
receives the publication instruction; its publication and lookup costs remain
charged. Optional direct-read tasks supply the same known file path to both arms
through a frozen `research_hints` list, so the baseline can read it directly.
These hints contain paths, not answers or source spans, and must name distinct
files in that task's current snapshot. They are allowed only on optional tasks.
Optional tasks do not force a Colony lookup or a particular memory query, even
inside a controlled run. Natural runs never receive the controlled learning
recipe or its publication-source hints; optional direct-read hints still apply
equally to both arms.

Research tools return one text representation through the actual MCP transport,
with no duplicated structured result. Paths, source text, coordinates, hashes
and the charge for every attempted call are preserved.
This removes a shared harness artifact; it does not establish how a provider
serialized or charged duplicate representations in older runs.

The shared research contract describes the tools available to both arms:
`fixture_files` discovers allowed relative paths; `fixture_search` matches a
nonempty literal string in file contents, not regex, semantic queries or filenames;
`fixture_read` returns source text with its hash and line coordinates. Omitting
read bounds uses the first 120 lines, ending at EOF for shorter files. Explicit
line counts must be integers from 1 through 120; invalid requests are rejected,
not silently expanded or retried.

The frozen research allowance counts repository tool calls only. Each individual
call in a batch and each failed attempt consumes a call. Colony lookups and
publications do not consume that repository allowance, but all their token usage
remains charged in the comparison. There is no additional call allowance,
automatic retry or exemption for failed research. The same descriptions and
accounting explanation are supplied to both arms; they contain no fixture answers
or task-specific source hints.

Two authored public source lineages are included: a gateway configuration/retry
flow for development and a worker queue/retry flow for validation. Both are
synthetic and visible to developers, **not held out or representative traffic**.
Their independent typed facts, source coordinates and changed v1/v2 snapshots
exercise these cases:

| Case | Evidence needed beyond a plausible answer |
| --- | --- |
| Cold learning | Actual authenticated empty ask, successful source reads covering the learning oracle, and a persisted sourced publication; charge all of it |
| Useful hit | The exact earlier published memory, matching saved content/source identities, byte-verified current excerpts covering the task oracle, correct grounded answer, no redundant repository tool calls |
| Miss | Actual `no_relevant_trail` response and independently reviewed answer |
| Stale evidence | Same previously retrieved memory and lookup question, a changed published source hash, actual stale response followed by successful current research (or verified current evidence from that memory), correct current answer |
| Insufficient evidence | A linked relevant partial hit with verified source spans, a demonstrably missing required span, and successful later source reads filling the gap |
| Cheap direct read | Optional memory use; compare its overhead against reading a small source directly |

A scenario is an expectation, not a preseeded outcome. If the target returns a hit
for the miss question, or the learner never publishes, controlled coverage fails.
An uncalled integration in the natural track is an adoption result; it does not
measure useful-hit savings. Prompt-cache hits are reported separately from memory
hits. Quality labels still require independent review; matching hashes cannot
establish that a claim is true.

Each lineage declares two learning steps: the first publishes the multi-file
configuration/retry finding; the second publishes a narrow default-setting finding.
The partial-evidence task retrieves that second memory and needs additional wrapper
and concrete transport/backend sources to establish the actual wire-attempt count.
Its extra learning session and publication are included in the schedule and token
accounting. A generic miss does not prove either stale or partial handling.
Controlled lookup questions and source scopes are frozen in the manifest.
Natural runs do not force these queries or publications.
Controlled learning still requires research and publication even when earlier
memory already answers a later learning question. This is an intentional mechanism
test, not the ordinary selective workflow. Use a separately frozen natural track
to assess that workflow; do not remove these costs from controlled results or
pool the tracks. Equal source hints and optional lookups do not guarantee that
Colony will save tokens.
Natural publication questions may be concise, their verified source sets may be
wider or narrower than the controlled learning recipe, and later tasks may reuse
any eligible earlier learning publication in that workload. The actual publication
identity, successfully researched source hashes, returned content and current
task's required evidence remain verified. Controlled question/source/parent
bindings remain strict.

The literal post-learning miss uses a source-unrelated astronomy question. During
development the earlier bank-settlement retry question produced an irrelevant
semantic hit in the worker lineage; that behavior was recorded, not counted as a
successful miss or fixed in the product. These authored capability cases cannot
estimate real-world false-positive rates.
The integration retains that original question as a separate read-only hard
negative after the declared journey. It checks source absence across the frozen
fixture and denies unsupported reuse credit for either a semantic hit or a miss;
it does not require the false positive to persist or add it to the native schedule.

## Prospective shared output contract

New manifests use `colony.token-evaluation/4` and bind the shared instruction and
structural check through `output.contract`:

```json
{
  "contract": "colony.token-evaluation/4",
  "output": {
    "contract": "colony.token-evaluation-output/1",
    "answer_policy": "last_agent_message",
    "format": "json"
  }
}
```

This is an excerpt, not a complete executable manifest. `template` writes the
binding automatically. Missing or unknown output-contract identifiers fail before
collection or scoring. Historical `/1`, `/2` and `/3` manifests are rejected by this collector
with instructions to use their original pinned collector for historical replay.
Do not add the new version/field to an old protocol or relabel old answers, labels,
reports, failures or statuses under these rules. A new candidate comparison needs
a new frozen protocol and separate native authorization.

For JSON output, the same shared final-answer instruction is supplied to every
task in both arms, in both tracks. The selected answer must parse as one JSON
object with both `facts` and `sources` at its top level. Nested-only sources,
Markdown fences, prose around the object, multiple objects, arrays, scalars and
malformed JSON do not satisfy this structure. The check requires key presence;
it introduces no value-type, citation-format or additional-key restriction.
Structurally accepted output can still be incorrect, unsupported or insufficient.
The existing independent correctness, grounding, citation and format review and
controlled-mechanism/tool-policy checks remain necessary for success. A reviewer
format pass cannot override a structural failure.

For example, both `{"facts": [], "sources": []}` and
`{"facts": {"answer": "..."}, "sources": {"citation": "..."}}` have the required
structure. Neither example establishes factual quality or grounding. An object
such as `{"facts": [{"sources": []}]}` fails because it lacks top-level `sources`.
The `free_text` option retains independent review without the JSON structural
gate. Extraction remains frozen separately; no messages are rewritten or repaired
to make the selected answer comply. These evaluator changes do not alter runtime
behavior, task questions, source snapshots, oracles, reuse horizon or accounting.

Version `/3` introduced prompt parity, optional lookup behavior and text-only MCP
research results. Version `/4` retains those rules and clarifies the research-tool
descriptions and repository-only allowance. It also advertises the already
required nonempty search query in the input schema. Six-call or other frozen
limits, read bounds, source access, tasks, oracles, quality gates and token
accounting are unchanged. Better tool descriptions do not prove that a model
will use them correctly or that accuracy or gross tokens will improve.

Results from different protocol versions cannot be pooled or attributed solely
to a product revision across an evaluator change. Old collectors, protocols,
answers, labels, reports and failed outcomes stay attached to their original
revisions. No historical campaign is repaired or regraded. In particular, `/3`
results remain replayable with their original pinned collector; do not relabel
their manifests as `/4`.

## Separate diagnostics, unchanged acceptance

The report exposes the causes of failure without relaxing its final conjunction:

| Field | Meaning |
| --- | --- |
| `answer_quality_successful` (per attempt) | Independent correctness and grounding, independent AND structural format, completed session and finished native turn |
| `answer_quality_complete` | Every scheduled attempt satisfies that answer-quality condition |
| `mechanism_complete` | Every controlled expected-use requirement is verified; `null` in a natural run, where no controlled recipe is required |
| `workflow_success` | Every scheduled attempt passes answer quality and its applicable expected-use requirements |
| `quality_complete` | Retained compatibility name for the same composite as `workflow_success`; not a pure answer-quality field |

Successful accounting, tool-policy compliance and adoption remain additional
conditions for an observed reduction. A required mechanism failure still prevents
success and leaves reduction unqualified even when every answer is correct.
An answer failure still prevents success when every mechanism is verified. All
learning, publication, research, failures and cached input remain in gross usage.
Neither passing local harness tests nor these diagnostic fields certifies product
savings; `certifying` remains false and `supported_product_win` remains
`not_established`. The three delivery-check names and requirements are unchanged.

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
Live preflight and execution require the exact complete active collector file/hash
binding, including when called through their direct entrypoints. Missing, empty or
mismatched bindings fail before local team provisioning, credential copying or
model dispatch. This includes the shared output-contract implementation: changing
it requires a fresh protocol rather than silently changing a frozen campaign.

Select one workload for 14 sessions (two learning and five reuse tasks times two
arms); the default two lineages require 28 sessions per track. The manifest records exact limits; these
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

Native attempt filenames use a bounded digest of the canonical
`[workload, task, arm]` tuple. The complete schedule is checked for unique,
reader-compatible identifiers before provisioning or dispatch, so hyphens and
maximum-length display names cannot create overlapping artifacts. Human-readable
workload/task/arm fields remain in each invocation and result. Existing retained
IDs remain readable; historical evidence is never renamed.

Inventory matches complete run-relative log paths. A basename references only
`logs/<basename>`; an explicit `logs/subdirectory/<basename>` reference names only
that nested file. This diagnostic support does not change the report's flat
native-log layout. A nested same-basename file cannot substitute for a missing
root log or hide as a referenced log. Invalid or duplicate references and every
orphan keep reconciliation incomplete, while all observed known token costs are
retained. Unreconciled evidence stops the next native dispatch.

Gross-token, disk and free-space stops are sampled **between sessions**, so the
last session can exceed them. Session and wall-time limits constrain the running
native process; server initialization has its own local timeout. They are not a
provider-enforced token/dollar ceiling. Dollar charges are unknown. Keep the
owner's approved margin sufficient for one final session.
Wall time is rechecked after local preparation immediately before client launch,
then again before sending the prompt. An exhausted budget prevents launch; if
process startup consumes the remaining time, the owned child is stopped without
receiving the prompt. Small positive remainders are not inflated to a minimum
timeout. Partial invocations/logs and previously observed usage remain retained.

## Review before looking at performance

```powershell
python scripts/token_evaluation.py inventory --run D:/evaluations/run-001
python scripts/token_evaluation.py mask --run D:/evaluations/run-001 --output D:/evaluations/review-001
```

Give the independent reviewer only `answers.json`, the frozen source snapshots and
the frozen rubric, without costs, arm identities or `private-map.json`. Answer text
may incidentally reveal memory use; arm masking is not guaranteed perfect blinding.
This packet supports source-truth grading, not proof that the agent actually read
a source. If the frozen rubric requires observed reads or other process evidence,
also supply chronological tool arguments/results with costs and coordinator IDs
removed, and retain that review evidence. Tool names may reveal the arm; disclose
that limitation rather than treating access-dependent grounding as proven by the
source packet alone.
The chosen extraction policy is frozen before collection. `last_agent_message`
selects the last completed agent message, and `all_agent_messages` joins all of
them. If the client omits phase metadata, final-phase provenance is not guaranteed.
All agent messages still incur usage, including narration outside the selected
answer. The masked packet carries the frozen output contract and extraction policy
so independent review uses the same requirement as collection. JSON compliance is
both structurally checked under that contract and independently reviewed; the
structural check does not verify the truth of any fact or citation. The packet's
output specification and exact instruction are hash-bound through quality binding
and reporting; changing the reviewer's contract or extraction policy is rejected.

The reviewer writes an arm-blind file containing `reviewer`, `method`, and `labels`:

```json
{"reviewer":"independent reviewer", "method":"arm-masked source review",
 "labels":[{"case":"RANDOM_CASE_FROM_ANSWERS", "correct":true,
             "grounded":true, "format_compliant":true}]}
```

Every case needs explicit Boolean labels. The coordinator then binds random case
IDs back to attempt IDs and exact answer/current-source/oracle/rubric/output hashes:

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

For current native runs, the report also checks retained prompt bytes, invocation
settings against the frozen client/research contract, and the complete alternating
arm schedule. These checks establish consistency of retained records, not external
process attestation. Duplicate or invalid attempt IDs make the inventory incomplete
and prevent another native dispatch. Tool-call identities and completion events
are validated equally for repository and Colony calls; malformed lifecycle records
cannot silently collapse multiple calls into one.
Native preflight and collection reject a missing, stale or noncanonical recorded
launch directory before native setup or dispatch. Prepare a fresh run at its
intended location instead of relocating an uncollected run. Completed runs may
still be copied for read-only replay using their retained original launch paths.

The report accounts for every retained native turn, learning and reuse, failures and all
output. It separates input, cached input, output, gross and uncached-plus-output
tokens; missing usage is unknown, never zero. It reports observed adoption, useful
reuse, mechanism expectations, quality, per-phase costs, session-time sums and
cost per successful workflow. Partial usage retains the sum of every validated
known input and output component as known gross, including cached input. A missing
component still makes total usage unknown and prevents qualification; a known
subtotal is never a complete total. Auxiliary counters are not added again.
With no successes that cost is undefined. Session
sums are not end-to-end wall time. A complete qualifying native run may show
descriptive paired reductions; it never creates a confidence-supported product win,
dollar savings or automatic merge approval. Native receipts are auditable local
records, not attestation against a malicious collector.

Raw activity counters labelled `claimed_*` describe response markers only. The
separate `mechanism_proof` verifies chronology, successful research results, the
publication ID/content/source binding, current excerpt hashes/text/line ranges,
and required oracle coverage. Failed calls still consume the research allowance
but cannot establish successful learning or fallback. Unknown, unrelated or forged
evidence receives no useful-reuse or controlled-mechanism credit.
The no-extra-research reuse flag counts unique repository attempts across started,
updated and completed MCP events, including failures. Repeated lifecycle events
for one call count once. An unfinished MCP call also withholds that flag; a valid
hit can remain visible separately. The proof records `research_attempts` and
`unresolved_mcp_calls` so missing completions cannot appear as zero extra work.
Those two diagnostics remain null for arms the mechanism checker does not inspect;
their ordinary tool activity is still inventoried separately.

## Provider-free verification

```powershell
python -m pytest tests/token_evaluations -q
python -m ruff check scripts/token_evaluation.py scripts/token_evaluation tests/token_evaluations
```

Tests exercise source-derived facts in both lineages, snapshot/citation corruption,
bounded research (including invalid calls), role/permission controls, answer
extraction, independent labels, complete raw usage inventories, failure/unknown
usage, interruption cleanup and native authorization gates. Output-contract controls
cover accepted JSON shapes, missing/nested-only keys, malformed/fenced output,
free text, unchanged independent quality failures, prospective version rejection
and collector-pin rejection before live boundaries. Mock native receipts
and fake events are labelled synthetic; they establish no measured token benefit.
Any later savings claim requires separately authorized native runs, reviewed raw
traces and independent correctness/grounding evidence at the fixed reuse horizon.

`test_product_integration.py` also runs both declared lineages through actual MCP,
Client, authenticated HTTP routes, Store, SQLite vector indexes and pinned offline
ONNX models. Only the HTTP transport is adapted to a local ASGI test client. The
positive journeys do not replace publication/retrieval methods or fabricate their
responses; the strict scorer consumes their captured real MCP results. Per-task
research MCP sessions enforce the same six-call allowance. Independent execution
of the fixture source derives typed answers and concrete wire retry counts.
Additional runtime mutation tests deliberately make Store publication a no-op or
retrieval always miss, and require the same journey to fail its specific product
contract. Missing offline model snapshots skip this coverage explicitly; a skipped
integration is unavailable coverage, not a product pass. These tests involve no
native agent sessions and establish no model-token savings.
