# Prospective task-adapter evaluation

Contract `colony.token-evaluation/12` requires an explicit `--adapter ordinary|task`
and, for a three-arm campaign, `--main-adapter ordinary|task`. The choice, target
revision, options, collector, client and workload bytes are frozen together. Old
contracts require their original pinned collectors; do not upgrade or regrade
historical runs with this implementation.

Task mode launches the installed target's `TaskMCP` through a small evaluator
fixture boundary. It does not launch ordinary Colony MCP alongside it. The same
process supplies source listing/search and task reads, with one shared research
allowance. Invalid reads, failed attempts and continuation pages each consume a
call. Memory lookup and publication do not consume that allowance, but every
operation and all model usage remain charged to gross tokens. The wrapper only
restricts access to the frozen fixture corpus; product routing, evidence references,
adaptive lookup and conditional publication come from the installed target.

The task question, direct path and explicit remember intent are trusted host
inputs. Direct paths must appear in the public question and frozen source list.
No route is inferred from an oracle, source instructions, scenario label or a
keyword classifier. When task mode is selected, the default prospective catalog
is `tests/token_evaluations/fixtures/task-adapter-catalog.json`. It makes the
existing direct-read path hints explicit in those questions for **all** arms.
The original catalog is unchanged. A custom `--catalog` remains possible.

Task mode currently supports the natural track only. Publication is optional;
there is no successful-publication gate, forced abstention, answer cap, or grading
reward for publishing. Missing evidence handles and publication failures never
change answer obligations. Source research, contradiction checks and all requested
deliverables remain available within the same frozen research allowance.

On a PR15 runtime with adaptive task support, pass the same `--context-budget` and
`--context-encoding` pairs in `--mcp-arg` and `--setup-arg` as the ordinary comparison.
The latter remain matching protocol declarations; task mode does not install
ordinary setup guidance. Main can use task mode without these options. Unsupported
runtime options fail the real-transport preflight rather than falling back.

## Discovery and launch checks

Local preflight checks the actual selected target over MCP stdio: authentication,
initial tool catalog, successful referenced reads, conditional publication,
direct/read-only exclusions, and tools-list-change notification plus SDK refresh.
It performs no publication and must leave the learning store empty.

An SDK refresh is not evidence that the native worker host refreshes tools. Before
a task-mode native launch, `--host-discovery PATH` must supply retained evidence
from that native client's discovery trace. The protocol embeds the evidence and
binds it to the executable SHA-256 and version. The required shape is:

```json
{
  "contract": "colony.native-host-discovery/2",
  "client_sha256": "<actual pinned executable SHA-256>",
  "client_version": "<actual pinned version>",
  "observer": "native_client",
  "client_features": {"mcp_tool_list_changed": true},
  "events": [
    {"connection": "<same task connection>", "type": "tools/list", "tools": ["read", "research"]},
    {"connection": "<same task connection>", "type": "notifications/tools/list_changed"},
    {"connection": "<same task connection>", "type": "tools/list", "tools": ["read", "research", "publish"]}
  ]
}
```

Extra fixture tools may appear in both catalogs. These must be observations of
the native client receiving the notification and refreshing its catalog, not
server-side capability flags, SDK test results or a hand-written assertion that
refresh is supported. Like other native launch receipts, the retained declaration
is auditable evidence, not cryptographic process attestation. Producing native-host
observability/integration is separate work; this evaluator consumes the evidence
and does not fabricate it. Missing or mismatched evidence blocks task-mode native
collection before a worker starts. Ordinary-mode collection is unaffected.

The pinned native client ships `mcp_tool_list_changed` disabled by default.
For a campaign containing either task-adapter arm, the launcher explicitly enables
this feature for every arm, including No-Colony, to hold host configuration constant.
Local preflight passes the same overrides to `features list` and rejects missing or
disabled required features before launching workers. Feature metadata alone is not
proof: the native same-connection discovery trace is still required and now records
the feature configuration. The original `/11` collectors and results stay frozen;
replay them with their original code. No product publication rules, evidence checks,
answer obligations, prompts, or grading thresholds change in this host fix.

## Evidence and interpretation

Reports distinguish selected adapters from observed task calls, referenced read
results and publication attempts. An installed module alone is not adoption.
Task-read bytes, scrubbed representations, line ranges and continuation offsets
are checked against frozen sources. Fully observed lines assembled across pages
earn evidence credit; partial and redacted lines do not. References bind only
earlier verified reads in the same attempt. Lookup-provided saved answers remain
explicitly untrusted, separate from verified current source excerpts. Completion,
correctness and grounding remain separate, with the original strict checks.

Both current main and PR15 contain PR25–27. Task versus task compares their
combined configurations; it does not isolate the package's incremental effect.
An adapter-on/off experiment must be declared separately and must use the same
public questions and limits. The new explicit-path catalog is a prospective
comparison, not a relabeling of earlier results. These regression tests establish
mechanics, not native token savings, native-host support or answer-quality results.
