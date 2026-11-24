# Task-scoped MCP integration

This opt-in adapter is launched once per task by a trusted host:

```sh
python -m colony.task_mcp --home /private/identity --repo /project --ns team --question "Read the default in settings.py" --direct-path settings.py
```

The host supplies `--direct-path` only for an explicit direct-file request, not a
keyword guess or an instruction embedded in a source. Without it the adapter
uses research guidance. The model sees two small tools: `read` and `research`.
Reading uses trusted evidence references and makes no Colony lookup or push.
`research` looks up the original bound task question. Other paths can always be
read; direct routing never prevents deeper research, contradictions, examples or
requested answer detail. The host's other authorized research tools remain usable.

When a question refers back to an earlier setup and scoped memory contains
saved work, the initial task instructions and lookup tool direct the worker to
call `research` before file discovery. A miss or missing evidence leads back to sources.
Ordinary and adaptive lookup share the same routing guidance. Use saved findings on
follow-ups when they can replace source discovery or fill an identified gap. Once
current sources answer the question, finish without a confirmation lookup. A lookup
is unnecessary before publication. Read known paths directly and batch independent reads. Prioritize evidence
needed for requested claims (including callers and dependencies) before auxiliary searches,
and size batches to any allowance imposed by the host. Use available research to close gaps
and contradictions, then explain remaining limitations. No source quota is imposed unless the host configures one.

For a task asking whether the repository contains something, a host with a fixed file
inventory can pass one `--scope-file PATH` per authorized file. These flags restrict
CLI source access to the listed paths. On a matching question, `source_files` lists the
available files for one read unit. If the complete inventory fits within eight entries,
16,000 source bytes and the known remaining units, the response includes the exact
arguments for a single ordinary `read` batch. The caller can still use a positive memory
hit or read an individual path. Embedded hosts can supply `scope_files`, `remaining_units`
and the name of their existing newline-delimited inventory tool via `scope_list_tool`.
Hosts with restricted reads should supply their metadata authorization predicate as
`read_path_allowed`. Every batch entry and continuation uses the ordinary authorization
and admission path.

After a matching listing, `listed_scope` reports coverage of the fixed inventory as
delivered, including inventories that require multiple batches. Full files and contiguous
pages from the beginning of the same version count, including delivered trace pages.
Coverage is tracked even when the publication reference cache is full.
Denied, redacted and incomplete files leave gaps. A refreshed listing can continue a
partially delivered redacted file. After its last page, the gap stays visible and the
planner skips further reads of that version. A detected target or file metadata
change invalidates earlier coverage. This receipt describes observations over the task,
not an atomic snapshot of a live repository. For a point-in-time determination, bind
the host inventory to an immutable checkout. Interpret the source text to answer
containment and qualify any unverified scope.

Answer guidance distinguishes concrete implementations from injected interfaces and total
attempts from retries. For example, a concrete adapter allowing two attempts does not prove
that an arbitrary injected transport has that limit; two total attempts permit one retry.
Preserve useful explanations by stating their verified conditions. Failed entries, partial
pages and literal search misses do not prove repository-wide absence. The read tool also
exposes the coverage warning; it adds no repeated warning to source responses. These are
evidence and precision requirements, not caps on examples, citations or answer detail.

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

The publisher can name observed files in `applicability` to declare the finding's
scope. Colony pins those files alongside the submitted evidence references. Use
exact whitespace-delimited repository-relative paths (including extensionless
files), or backticks for paths containing spaces or ambiguous punctuation. A
complete quoted item names the path inside its delimiters. Supply the returned
handle directly when the path itself could be mistaken for markup. Each added
source must have a delivered task read and pass the same authorization and version
checks as a submitted handle. A change to any pinned file invalidates the finding.
If a named delivered file has no live reference, publication requires fresh evidence.
Task-local path tracking is bounded. If it fills, an unaccounted named path also
requires fresh evidence.

The first successful eligible read also returns a short `publication_guidance` field
alongside the read result, or alongside `results` for a batch. This constant adapter
cue is separate from source content and is not evidence. It reminds the model to
publish an eligible supported finding or skip publication and complete the answer.
It appears at most once per task, never once per batch entry or continuation page.
Failed reads and reads without evidence references do not consume the cue. Read-only
and direct tasks without explicit remember intent receive no cue. The cue does not
attempt publication, add a tool call, reserve source capacity, or gate the final answer.
If that read delivers complete pages from multiple files and each has a reference,
`publish_args_if_supported.evidence_refs` collects the returned handles. The caller
supplies the finding and its conditions and may narrow the reference list to the
supporting files. A traced rule can be reusable even when its result is numeric.
An isolated value question still requires explicit remember intent to publish.
For a standalone question about one common configuration default, such as a
port, timeout or delay, the task adapter routes to the definition and omits
publication. A host-supplied remember request enables publication as usual.
Compound questions and explanations retain the behavior-learning workflow.
On a validated lookup, full cited files are current source reads that can be
cited directly. Investigate missing dependencies and conflicting evidence as needed.

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
host. When neither a saved answer nor a single passage matches, compact lookup
can use a related saved question to locate a small set of cited files whose
contents cover complementary query terms. It returns current source text after
validating all attached sources. The usual wire budget still applies.
Its optional `max_context_tokens` override has the same 256..8192 range and
configured units. No automatic retry or budget escalation occurs. This is a
lookup-response limit, never an answer-length or source-research limit. Missing,
limited or unavailable memories leave source reads and fuller answers available.
Direct reads, continuation offsets and publication eligibility are unchanged.
Without a budget, the legacy lookup interface and representation are unchanged.
This does not change retrieval. End-to-end gross savings remain unproven.

Writable eligible tasks receive a conditional before-final publication instruction
in MCP initialization. Direct tasks without
remember intent and read-only tasks receive no publication workflow. This guidance does
not auto-publish, require a successful write, add a decision call, or limit answers.
Requested publication status and failure explanations remain part of the answer;
only unsolicited abstention reports are omitted. Unknown outcomes stay uncertain.
Use the actual supporting read references for multi-source findings.
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
