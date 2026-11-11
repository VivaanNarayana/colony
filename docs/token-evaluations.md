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
hits. Matching hashes and advisory review cannot establish semantic truth. Under
`/6`, unresolved correctness or grounding blocks a quality-qualified result.

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

New manifests use `colony.token-evaluation/6` and bind the shared instruction and
structural check through `output.contract`:

```json
{
  "contract": "colony.token-evaluation/6",
  "output": {
    "contract": "colony.token-evaluation-output/1",
    "answer_policy": "last_agent_message",
    "format": "json"
  }
}
```

This is an excerpt, not a complete executable manifest. `template` writes the
binding automatically, together with the frozen `quality_review` policy (GPT-6.1 Sol,
medium reasoning, exact instructions and their hash). Missing or unknown contract identifiers fail before
collection or scoring. Historical `/1`, `/2`, `/3`, `/4` and `/5` manifests are rejected by this collector
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
Correctness, grounding and citation obligations remain necessary for success,
alongside controlled-mechanism/tool-policy checks. Under `/6`, advisory findings
cannot pass those semantic obligations or override a structural failure.

For example, both `{"facts": [], "sources": []}` and
`{"facts": {"answer": "..."}, "sources": {"citation": "..."}}` have the required
structure. Neither example establishes factual quality or grounding. An object
such as `{"facts": [{"sources": []}]}` fails because it lacks top-level `sources`.
The `free_text` option retains advisory review and unresolved semantic obligations
without the JSON structural gate. Extraction remains frozen separately; no messages are rewritten or repaired
to make the selected answer comply. The output contract does not alter runtime behavior, task questions, source
snapshots, reuse horizon or accounting. Task evidence requirements are versioned
separately below.

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
their manifests as `/4` or `/5`. The same rule preserves `/4` campaigns under
their original pinned collector; they are not regraded with `/5`.

## Task-specific evidence contract (version /5)

Version `/5` fixes the evaluator's review and mechanism evidence rules. It keeps
the same fourteen task questions, learning/reuse order, source snapshots, typed
answers, model-facing prompts, research limits, output structure and token
accounting. It does not change Colony production behavior or establish a token
improvement. Both arms receive the same task-specific quality rules; oracle and
rubric metadata is reviewer-only and never becomes an agent hint.

Each task declares `oracle.evidence_policy.kind`. `support` requires meaningful current
source spans establishing the requested facts. A default-setting question needs
its definition, not unrelated propagation or retry analysis. A retry question
still needs its relevant precedence, propagation and termination evidence.
Learning quality does not require publication: that requirement belongs only to
Colony's explicitly controlled mechanism recipe, never to No-Colony or natural
adoption. Current evidence may arrive through repository tools or verified Colony
excerpts; the reviewer must judge support, not reward a particular tool sequence.

`scoped_absence` declares the frozen repository scope and has no positive-fact
citation list. The reviewer must see adequate observed current evidence supporting
the scoped absence claim. A cache miss, an irrelevant retry snippet, or an empty
literal search for `termA|termB` does not by itself establish that the individual
terms or requested information are absent. The search tool treats that entire
string literally. An observed measurement contradicts an absence answer; an
invented external measurement fails even when the answer otherwise correctly
states that it is not recorded here. No particular sequence of research calls is
mandatory. Complete verified current source evidence delivered by cache can
support the same claim as repository evidence.

Mechanism coverage for scoped absence checks the declared repository scope,
not inherited retry-code spans. Mechanical coverage is necessary for a reuse
proof, not sufficient for semantic correctness: a current full-source bundle
can still accompany an incorrect or unsupported answer. Independent quality and
all existing format, policy, accounting and controlled-use requirements remain
conjunctive. A mechanism miss never automatically passes grounding.

All tasks retain the rule that **every additional factual claim needs support**.
For example, an injected dependency cannot be assumed to be the repository's
concrete retry adapter. A wire-attempt ceiling derived from that adapter must
state the condition. Correct requested facts cannot excuse an unsupported extra
ceiling, stale citation or invented detail in either arm.

Review packets bind the exact question as well as the answer, current source
snapshot, oracle, rubric and output contract. Removing the source-evidence field
or altering any of those displayed inputs rejects binding; it cannot bypass
verification by retaining the old digest strings.

Regression tests exercise the evidence boundaries and symmetric gate enforcement;
source-execution counterexamples calibrate review. They do not implement a general
semantic grader or prove that a model or human reviewer will always follow the
rubric. Historical protocols, answers, grades and conclusions remain immutable.
These task-specific evidence obligations are retained by `/6`. Future comparisons
need a newly frozen `/6` protocol and separate authorization;
results from different evaluator contracts cannot establish a product-only effect.

## Separate diagnostics, unchanged acceptance

The report exposes the causes of failure without relaxing its final conjunction:

| Field | Meaning |
| --- | --- |
| `answer_quality_successful` (per attempt) | False while correctness or grounding is unresolved; no agent-provided Boolean is accepted |
| `quality_state` / `quality_obligations` | Deterministic format failure is `failed`; structurally valid free-form semantics remain `unresolved`, never implicitly passed |
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

The prospective `/6` workflow separates **advice** from **grading**. Neither a
review subagent nor the orchestrator decides semantic pass/fail. The two advisory
slots (`claims` and `counterexamples`) use **GPT-6.1 Sol (`gpt-6.1-sol`), medium
reasoning**, with the exact instructions frozen in `quality_review`. Each gets a
fresh context containing only the public packet and those instructions. Do not
mount the coordinator directory or give reviewers filesystem access to the
private map, traces with costs/identities, implementation discussion or the other
review. `quality-template` reads only `answers.json` and contains no private attempt IDs.
Model/session identity and context separation are declarations, not attestation.
Answer contents may reveal treatment, so masking is not guaranteed perfect blinding.

The public packet includes the frozen question, rubric, oracle, output contract,
current source snapshots and normalized **observed current evidence**. Verified
repository and cache excerpts use the same path/hash/line/content representation;
publication, hit status and research volume confer no quality credit. Evidence
returned after the last answer is excluded. The normalized excerpts are useful
review context, not proof that an answer's claims follow from them. For
`all_agent_messages`, this final-answer evidence set does not establish the
chronology of each earlier message. Unknown chronology stays unresolved.

Create the review template offline:

```powershell
python scripts/token_evaluation.py quality-template --packet D:/evaluations/review-001 --output D:/evaluations/advice-template.json
```

The template deliberately has empty session IDs and uncertainty fields; it cannot
be submitted as completed advice. Submit both distinct review slots, each bound
to the complete packet hash, exact model/effort/instruction hash and every random
case ID. Findings name a frozen `correct` or `grounded` rubric obligation, a kind
(`support`, `concern`, `omission`, `counterexample`), rationale, proposed
counterexample text and exact source references. Non-omission findings require
zero-based Unicode code-point answer offsets `[start,end)` and the exact text.
Omissions bind the whole answer hash, including empty answers. Source references
use one-based inclusive `start`/`end` lines, full source SHA-256 and exact text.
Every case must state uncertainty, including cases with no findings. Proposals
are unverified data and are never executed. Agreement is not proof.

For example, a finding within a submitted case has this shape (values are illustrative):

```json
{"obligation":"correct","kind":"concern",
 "anchor":{"start":0,"end":4,"text":"1500"},
 "sources":[],"rationale":"Check the qualification against current control flow.",
 "counterexample":"Describe a possible exception path; this proposal is unverified."}
```

To inspect an unreviewed collection, the explicit empty-advice artifact is
`{"contract":"colony.quality-advice/1","reviews":[]}`. It records missing advice;
it does not pass quality. There is no agent-Boolean or historical-label fallback.

```powershell
python scripts/token_evaluation.py bind-quality --packet D:/evaluations/review-001 --review D:/evaluations/advice.json --output D:/evaluations/quality.json
python scripts/token_evaluation.py report --run D:/evaluations/run-001 --quality D:/evaluations/quality.json --output D:/evaluations/report.json
python scripts/token_evaluation.py human-review --packet D:/evaluations/review-001 --review D:/evaluations/advice.json --output D:/evaluations/review.html
python scripts/token_evaluation.py human-template --packet D:/evaluations/review-001 --output D:/evaluations/notes-template.json
```

Open `review.html` locally when time permits. It shows mechanical state, exact
answers and frozen sources, observed excerpts, advisory findings and uncertainty.
It has no active scripts or external requests; all untrusted text is escaped.
Human notes contain an author declaration and entries `{case, answer_sha256, text}`.
Save edited notes to a new file, then validate and retain them separately:

```powershell
python scripts/token_evaluation.py bind-human-notes --packet D:/evaluations/review-001 --notes D:/evaluations/my-notes.json --output D:/evaluations/retained-notes.json
python scripts/token_evaluation.py human-review --packet D:/evaluations/review-001 --review D:/evaluations/advice.json --notes D:/evaluations/retained-notes.json --output D:/evaluations/review-with-notes.html
```

Notes are additive, bound to the entire packet and answer, and self-declared human
provenance is not identity verification. They cannot override any outcome. All
commands write new files exclusively and never launch model sessions. The public
HTML is an inspection artifact, not an accepted report: only `report --run` can
reconstruct subjects against the retained collection. It revalidates submitted
advice against those subjects, including evidence and reviewer instructions.

**Current limit:** no semantic proof checker is registered for these free-form
workloads. Structural format is checked deterministically, but correctness and
grounding remain `unresolved`, even for apparently good answers and unanimous
advice. Consequently `/6` cannot establish a quality-qualified token win yet.
A later, separately reviewed proof-checker contract would need sound, bounded
semantics and adversarial regression tests. Substring matches, citation presence,
oracle-value equality, agent votes and human notes cannot substitute for it.
This limitation is explicit rather than claiming automatic semantic certainty.

The report still rejects altered questions, answers, sources, protocol, duplicate
or missing tasks, invalid task order and unlisted events/invocations. All failed
and unresolved attempts retain their costs; no selective filtering, retries or
favorable reruns are introduced. Incomplete schedules cannot produce a matched
report: use `inventory` or the partial `collection.json` inventory to retain known
costs and failure details. Historical `/5` labels and reports retain their original
meaning and must be replayed using the original pinned collector, never relabeled.

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
extraction, advisory bindings and unresolved gates, complete raw usage inventories, failure/unknown
usage, interruption cleanup and native authorization gates. Output-contract controls
cover accepted JSON shapes, missing/nested-only keys, malformed/fenced output,
free text, unresolved semantic gates, prospective version rejection
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


### Advisory-quality regression coverage

Provider-free tests cover direct Boolean-grade rejection at the CLI and report,
review model/effort/instruction/session and complete-case inventory, exact Unicode
answer spans, omissions, stale/foreign source references, full-packet replay and
mutation, equivalent observed cache/repository excerpts, escaped human inspection,
nonbinding notes and original-artifact preservation. Synthetic evidence is clearly
marked and never reported as native token savings. Existing receipt, tool-policy,
source-integrity, schedule, usage and shared-prompt tests remain enforced.

Benchmark answering remains a separate role: use the user-required Luna 6 Light
for future authorized native sessions, after verifying and pinning the exact
supported client model identifier and light reasoning setting for all fresh arms.
Do not substitute the Sol reviewer model. The advisory commands neither select nor
launch benchmark models, and this change does not authorize a new evaluation.
