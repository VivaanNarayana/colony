# Task-scoped MCP integration

This opt-in adapter is launched once per task by a trusted host:

```sh
python -m colony.task_mcp --home /private/identity --repo /project --ns team --question "Read the default in settings.py" --direct-path settings.py
```

The host supplies `--direct-path` only for an explicit direct-file request, not a
keyword guess or an instruction embedded in a source. Without it the adapter
uses research guidance. The model sees two small tools: `read` and `research`.
Reading uses trusted evidence references and makes no Colony lookup or push.
`research` searches saved findings for the host's original question. A fresh
question with an identifiable source can start with a read even when the store
contains unrelated findings. The worker can read other paths or use the host's
other authorized research tools for contradictions, examples and requested detail.

On either lookup route, check memory before reading files when the user requests
memory or a prior finding could save work on a repeated question. Start with
current sources for changed behavior or semantic absence. If the user also asks
what was remembered, check memory as well. Once the sources cover every requested
claim, finish the answer and any eligible publication. A later lookup is useful
for an explicit memory request or a remaining gap or conflict. Checking memory
status alone adds a call. A memory miss provides no evidence of repository absence.
On a validated adaptive hit, each entry in `current_repository_reads` carries
its path, start line, content and, when available, end line. Ordinary hits
can include `observed_lines` as inclusive `[path, first_line, last_line]` ranges
when the local tokenizer confirms the index fits. An ordinary excerpt can
end mid-line. Its index excludes the final line unless the whole file is established.
Use the source text for claims it
settles. Read a known missing path or range directly, including caller wiring
when selection matters. A listed span can cover only part of a file. Saved
claims still need support from the current source, and conflicts can require a fresh read.
Read known paths directly and batch independent reads. Prioritize evidence
needed for requested claims (including callers and dependencies) before auxiliary searches,
and size batches to any allowance imposed by the host. Use available research to close gaps
and contradictions, then explain remaining limitations. No source quota is imposed unless the host configures one.

For a propagation question, group the known files for the requested caller, the
operation it invokes, and any intervening wrappers in one batch when they fit.
For example, a worker's configured limit, dispatcher and executor can together
establish the caller's outcome. Include configuration when it affects that path,
such as a flag that controls whether the caller re-raises an error. For a gateway
receiving repeated HTTP 503 responses, the retry settings, handler and client may
settle the outcome in one batch. Add a concrete transport when the question names
it or the evidence needs its behavior and the wiring selects it.
Two configured attempts permit one retry. If an injected transport raises on its first
call and the wrapper lets the exception propagate, the wrapper makes one call.
For example, if a named backend makes two sends and re-raises on the first
wrapper attempt, report two sends, one backend retry and zero wrapper retries.
If the question specifies returned statuses, trace the wrapper's status branch.
Count a second wrapper attempt only if a return or catch leads back to that loop.
A catch outside the loop can change the caller's outcome without adding a retry.
An implementation in the repository establishes what that class does. To apply
its limit to the actual call, check where the caller receives or constructs the
dependency. If selection is unknown, state the caller's own limit and give any
relevant inner limit conditionally, naming the class. Read wiring when the
question requires the selected implementation and the evidence so far leaves it open.
When the question specifies a particular backend, that premise supports an answer
about that backend. A claim about the deployed backend still needs wiring evidence.
For example, if a client lets a transport error escape, a gateway wrapping the
client can still catch it. Include the gateway in the first batch when its path
is known and its outcome is requested. If its code is unavailable, report where
the observed exception escapes and identify the unresolved caller outcome.

For a default value, read the identifiable policy or configuration file directly.
When it establishes the requested value and precedence, finish source research.
If it delegates the value, conflicts with another source, or the user requests
corroboration, follow that evidence. Eligible publication uses the references
already obtained.

For an exact-text question, one literal search can settle a single-line query
when it covers the requested files and uses the requested case rules. For a
multiline snippet on a line-oriented host, search for a distinctive line and
read the adjacent lines at candidate matches. If the results are capped or files
were skipped, check the remaining scope before claiming absence, or name the gap.
A question about a fact also covers indirect expressions,
such as a value computed from two settings. For that question, use already observed
evidence and start with the remaining listed sources, batching them when the scope fits.
An explicit request to check memory still calls for lookup.
Reuse the existing listing and the remaining allowance in the latest receipt.
Reserve units for required listed reads before spending one on an auxiliary search.
For example, if five units remain for five required files, a literal search would
leave at least one unchecked. Five short files
whose contents fit in 16,000 UTF-8 bytes can share one batch. Nine short files
need at least two batches because each accepts at most eight entries. Four
5,000-byte files fit in two pairs with four read units. If full coverage matters
and only one unit remains per needed file, batch ranges already known to fit
together. Read files of unknown size individually so each gets its own
16,000-byte page. Even an individual file can require a continuation.
Inspect computed values, continue needed pages at their
returned offsets, and state any scope that access or allowance leaves uncovered.
For example, if three of five relevant files were read and two were denied,
report what the three establish and identify the two still unchecked. Put the
uncertainty in the requested field when the format permits, for example,
`configured limit: unknown (settings.py and policy.py were unread)`.
A targeted search remains useful when it can establish presence or narrow a larger scope.

The adapter puts lookup timing, direct reads, caller batching, backend selection
and source-first guidance for semantic absence near the start of its MCP instructions. The
pinned Codex native host previews the first 247 characters of a long namespace
description followed by an ellipsis on its initial request. The direct-file route
still starts with the requested path. The discovered `read` tool explains how
to count retries and when an uncaught error ends a loop. For a single read, supply `path` unless the
host provided `--direct-path`, in which case omitting `path` reads that file.

`read` accepts an optional integer `offset` (default 0). Large selected ranges,
including single long lines, return bounded pages with `next_offset`; continue
with that offset and the same path/start/end until it is null. Offsets count
characters in the scrubbed, newline-normalized selected range, not raw bytes.
For the host's direct path, path can remain omitted on continuation. Short reads
have no continuation fields and need no extra call. Partial pages are marked
`complete_file: false`; file changes invalidate earlier evidence references.
Reading all pages is not required unless the task needs them. Paging never
triggers lookup or publication and imposes no limit on necessary research.

For independent reads whose paths are already known, the same tool accepts a
`batch` of 1–8 entries. Each entry supplies its own `path` and optional `start`,
`end`, and `offset`; paths are explicit even on a direct task. For example:

```json
{"batch":[{"path":"settings.py"},{"path":"handler.py","start":5,"end":7}]}
```

The response is `{"results":[...]}` in request order. Successful entries have
the same fields and evidence-reference semantics as individual reads; failed
entries contain a fixed `error` and `evidence_ref: null` without echoing the
rejected path. One failure does not discard other results. Malformed batches
are rejected before reading any entry. Do not combine a batch with a top-level
path or nondefault range/offset arguments.

The batch shares at most 16,000 UTF-8 bytes of **source content**. Unused shares
from short, empty or failed reads are redistributed to entries that need more;
all valid requested text is returned when it fits in the combined allowance.
When it does not fit, entries share the capacity fairly; UTF-8 boundaries may
leave a few bytes unused. JSON framing, escaping and bounded per-entry metadata
are additional. Each source is read and scrubbed once, and at most one ordinary
16,000-byte page is staged per entry. Final pages are selected before references
are minted; references never claim observation of omitted text. Each partial page carries its own
`next_offset`. Continue it with the same path/start/end in a later batch or an
ordinary single read. Offsets remain characters in the scrubbed selected range,
even when the number of entries changes. Duplicate paths/ranges are permitted
and return independent observations. A batch is not an atomic repository snapshot;
changed files still invalidate their earlier references at publication.

Batching reduces tool round trips for independent reads, not required evidence
coverage. Use single reads when more suitable; dependent discovery still needs
the preceding result. All necessary research, citations, examples and answer
detail remain available. The host's direct/read-only/remember classification,
conditional publication, one-attempt guard and failure behavior are unchanged.
Only successful entries with references can unlock eligible publication, and
reading never publishes automatically.

Hosts that wrap `EvidenceSession.read` must forward private keyword arguments,
including `_defer_reference`, and preserve the returned internal prepared value
when staging a batch entry. Each entry still passes through that wrapped read;
the adapter does not bypass host restrictions. Its default single-read call
and result are unchanged. References bind final delivered pages to the hash of
the originally read bytes, not to a later reread during allocation. Staging does
not extend evidence expiry, and authorization is rechecked before minting.
Quota/accounting wrappers must count each attempted batch entry and continuation
as a source read, including failures, rather than charging one read per batch.
This adapter PR does not update the evaluator: its batch support and per-entry
accounting must be added separately before any evaluation uses this feature.

This integration does not intercept arbitrary tools or change a desktop host's
task dispatcher. Hosts must launch it with the actual task question and route
their explicit direct reads to it. Existing MCP/setup connections are unchanged;
do not configure both ordinary and task MCP for the same operations. Read-only
and Git repository bindings are supported. Each process must serve one task and
one principal; restart for another task. No cross-task evidence references persist.

Publication is conditional. For a research task (no `--direct-path`), successful
reads with references make `publish` discoverable. A direct task keeps publication
unavailable unless the host passes `--remember` for an explicit user request.
Lookup-only reuse never unlocks publication. The host must not classify a broad
research task as direct merely because it mentions a filename.

The first successful eligible read also returns a short `publication_guidance` field
alongside the read result, or alongside `results` for a batch. This constant adapter
cue is separate from source content and is not evidence. It reminds the model to
publish an eligible supported finding or skip publication and complete the answer.
It appears at most once per task, never once per batch entry or continuation page.
Failed reads and reads without evidence references do not consume the cue. Read-only
and direct tasks without explicit remember intent receive no cue. The cue does not
attempt publication, add a tool call, reserve source capacity, or gate the final answer.

The server advertises MCP tools-list changes and sends notifications. Hosts must
support refreshing tool discovery; otherwise research still works but they may
not discover publication. The Python `TaskMCP` API can also be embedded. This is
a usable adapter, not an automatic installation into Codex's task dispatcher.

`publish` accepts `content`, `applicability`, `evidence_refs`, and `kind` (behavior
or value). The original bound question is supplied by the host, not retyped by the
model. Value-only publication requires explicit remember intent. Stored
`applicability + "\n" + content` must total at most 4,000 characters, including
the newline. An oversized candidate returns `finding_too_large` before consuming
the attempt or touching storage; the caller can shorten the memory and resubmit.
This limit never applies to the user's answer or necessary research. After an
attempt passes the size preflight, the tool is hidden; guessed direct invocation
is also guarded. Failure returns a fixed result and leaves reads/research
available. No automatic retry, extra model call,
mandatory abstention message or final-answer gate is introduced. A failed network
operation can have an unknown outcome; use the ordinary client's explicit retry
or inspection path rather than submitting another candidate automatically.

Eligibility is not semantic certification: `kind` is model-declared, and a fresh
read does not prove novelty or that its text supports a claim. The publisher must
still skip unchanged reuse, speculation and injected instructions and confine
claims to observed spans. Source text cannot change the host's direct/read-only/
remember settings. These controls reduce opportunities and bookkeeping cost;
they do not solve poisoned-memory admission. The normal untrusted-memory and
source-validation protections remain in force.

The adapter makes no additional model calls and adds no answer cap.

Opt into adaptive lookup with `--context-budget 3072
--context-encoding utf8_bytes`. The Python API accepts `context_budget=ContextBudget(...)`.
Adaptive `research` uses the same negotiation, source verification, packing and
wire-response cap as ordinary adaptive `ask`, with the task question bound by the
host. Its optional `max_context_tokens` override has the same 256..8192 range and
configured units. No automatic retry or budget escalation occurs. This is a
lookup-response limit, never an answer-length or source-research limit. Missing,
limited or unavailable memories leave source reads and fuller answers available.
Direct reads, continuation offsets and publication eligibility are unchanged.
Without a context budget, task `research` keeps the ordinary lookup fields.
A validated hit can also include `observed_lines` in compact JSON when the
index fits within both the original byte count and the measured token count.
This does not change retrieval. End-to-end gross savings remain unproven.

Writable eligible tasks receive a conditional before-final publication instruction and
an evidence-reference payload example once in MCP initialization. Direct tasks without
remember intent and read-only tasks receive no publication workflow. This guidance does
not auto-publish, require a successful write, add a decision call, or limit answers.
Requested publication status and failure explanations remain part of the answer;
only unsolicited abstention reports are omitted. Unknown outcomes stay uncertain.
Use all supporting read references for multi-source findings; never send the example's
variable names as references. The ordinary AGENTS.md ask/push payload is not this API.
Local transport tests establish delivery and capability, not model compliance or savings.


## Empty-memory source-first routing

On stdio startup, a non-direct task makes a short authenticated
`POST /memory/availability` request. The response contains only a contract,
namespace, configured repository identity and `empty`/`present` state. Only a
zero active-row result in the authorized namespace enables the source-first hint.
Stale, unverified and unrelated active memories conservatively count as present;
a search miss never becomes an empty-store proof. Repository-ID mismatch, old
servers, denied/expired credentials and transport failures yield `unknown` and
retain normal research. No embedding, ranking or source reading occurs in this probe.

The hint is explicitly a snapshot, not a guarantee about subsequent publications.
An empty hint has a three-second local lifetime; tool-list and source-tool requests
recheck expired state and revalidate identity. Present/unknown results back off for
30 seconds so outages do not add a probe to every read. Explicit lookup skips the
availability probe, invalidates the hint and uses the normal current lookup. Publication invalidates even an in-flight
snapshot, including failed/unknown write outcomes. Catalog changes are notified
even when a tool call fails. Notification transport errors cannot replace a tool
result or its sanitized error. Explicit `research` always performs the normal current
lookup; it is never hidden and never returns a cached miss. The startup hint is
historical ("empty at task startup"); it does not claim continued emptiness.
Sources, other research and all requested answer detail remain available.
Use `--no-empty-memory-routing` to disable the probe. Explicit direct-file tasks
already route to reads and do not make the startup probe. Embedded hosts can opt
into the same lifecycle through the stdio runner; construction alone does no I/O.

## Opt-in dependency bundles

`--trace-reads` additionally exposes `trace_read(paths=[...], max_files=8)`.
Paths are bounded to 1-8 entries and 1,024 characters each; `max_files` is 1-8.
This tool is for code-tracing requests, not an instruction to expand every value
lookup. It reads explicit paths first, then deduplicated static Python module
hints in discovery order. It never executes imports or resolves packages. Only
`.py` module candidates from full delivered Python pages are considered. Before
queueing an inferred path, a bounded exact-path metadata check revalidates the
evidence session and repository boundary; it reads no source bytes and issues no
evidence references. Missing, unsafe or unavailable candidates remain unresolved
gaps, reported in at most eight `unresolved_paths` with `unresolved_truncated`.
They do not spend source-read units. This metadata is neither evidence of absence
nor proof of an import's runtime binding. Local files sharing standard-library
names remain eligible. Relative imports are bounded to the repository. Package initializers,
dynamic imports, runtime bindings, callers and injected implementations are not
inferred. Request scope documents and other required evidence explicitly.

Every content-read attempt still uses the ordinary host-wrapped evidence-read
boundary and per-entry admission, including explicit missing paths and candidates
that disappear after the metadata check. Metadata selection never permits a
content read, relaxes an allowlist, or refunds a failed read. A bundle shares a
16,000-byte source-content allowance in order; it stops at the file/content cap.
Each partial page has the same character-based `next_offset` as `read`; continue
it using the returned path/start/end and offset. Expansion does not inspect
omitted pages. Errors do not mint references, and current hashes, scrubbing,
expiry, task/principal binding and conditional publication remain authoritative.
Successful reads can expose the existing single publication cue, but never
publish automatically. `coverage` explicitly does not certify completeness;
`pending_paths` are bounded discovery hints, not factual evidence. Additional
research and the complete user answer remain permitted.

### Quota boundary

The standalone adapter accepts optional `--read-unit-limit N`. It applies to
single reads, every batch entry, continuations and every attempted expanded file,
including failures. This allowance is stated in initialization policy. Without
this option the standalone source allowance is
unlimited (the bundle's per-call size caps still apply). A trusted embedded host
can supply `read_admission`, a callback reserving one shared unit and returning
exactly `True` before a file attempt. False, errors or exhausted allowance deny
access. The callback is not a model-controlled tool argument. Use the same ledger
for the host's other research tools; do not separately debit adapter entries a
second time. `read_accounting` reports admitted, denied and successful bundle
entries. No denied entry executes or acquires an evidence reference.

Legacy outer wrappers that charge only one unit per tool or only recognize `read`
must **not** enable `trace_reads` until they integrate this per-entry admission
contract and report expanded units. The existing evaluation adapter does not
enable `trace_read`; evaluator schema/receipt/accounting support remains a
separate change. This PR does not change frozen protocols or historical results,
add a host completion hook, or establish native token savings or model compliance.

Embedded hosts with a path allowlist can pass `read_path_allowed(path)` to
`TaskMCP`. Use only metadata and return exactly `True` for allowed repository
paths. Both the requested name and any distinct resolved target must be allowed.
The adapter checks this policy before probing inferred imports, reading source
bytes, delivering staged batch or trace entries, and revalidating publication references.
Changing a link's target invalidates its staged reads and earlier references,
even when the new file has the same bytes.
Trace results include only dependencies reachable from a delivered source or
requested explicitly. Each attempted read still consumes its original allowance.
Lookups with excluded cited sources withhold the hit, including saved prose.
Restricted Git lookups retain each validated target through delivery. Restricted
publication retains the observed targets through sending or offline queueing.
Allowed sources remain available for the answer. Excluded imports stay unresolved
and spend no read units. An explicit read attempt still counts.
A host that wraps `evidence.read` should supply the same path policy to
enable automatic expansion. Without it, the adapter follows
explicit requests and reports inferred imports as unresolved.
