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

Ordinary and adaptive lookup share the same routing guidance: look up saved findings
when useful, read known paths directly, and batch independent reads. Prioritize evidence
needed for requested claims (including callers and dependencies) before auxiliary searches,
and size batches to any allowance imposed by the host. Use available research to close gaps
and contradictions, then explain remaining limitations. This adapter adds no research quota.

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
Without a budget, the legacy lookup interface and representation are unchanged.
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
