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

The server advertises MCP tools-list changes and sends notifications. Hosts must
support refreshing tool discovery; otherwise research still works but they may
not discover publication. The Python `TaskMCP` API can also be embedded. This is
a usable adapter, not an automatic installation into Codex's task dispatcher.

`publish` accepts `content`, `applicability`, `evidence_refs`, and `kind` (behavior
or value). The original bound question is supplied by the host, not retyped by the
model. Value-only publication requires explicit remember intent. After one attempt
the tool is hidden; guessed direct invocation is also guarded. Failure returns a
fixed result and leaves reads/research available. No retry, extra model call,
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
Legacy lookup representation is retained on main; this adapter does not copy
PR15's adaptive packing or change retrieval. End-to-end gross savings are unproven.
