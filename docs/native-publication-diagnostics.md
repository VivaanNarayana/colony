# Native publication diagnostics

`scripts/native_host_trace.py --output PATH [--eligible] -- CHILD ARGS...` is an
opt-in transparent stdio wrapper. The trusted host supplies eligibility and a
unique output path per task. It emits no model-visible instructions and never
calls a tool or a model. stdout contains only the child's original protocol bytes.

The sidecar retains only allowlisted metadata: referenced reads, tool-list-change
notifications, catalog presence of publish, publication attempts, and receipt
booleans. It never logs source text, question, arguments, evidence handles, auth,
or arbitrary errors. It caps events and pending correlations and reports dropped
metadata. Existing files are never replaced. Missing/unwritable diagnostics do
not block the task; the caller must report absent diagnostics as unavailable.

`refreshed_with_publish_no_attempt` is a protocol observation, not a statement of
model awareness or deliberate abstention. Model-visible catalog and abstention
reason stay `unobserved`: this native stdio surface exposes neither. A started
publication without a correlated response is `attempted_outcome_unobserved`.
Receipts establish only reported outcomes, never semantic truth or safe content.

This is separate host infrastructure. Connecting it to an evaluator requires
prospectively pinning its bytes and exact launch command. It must not modify old
traces, grades, protocols, or worker prompts, and is not a completion hook.
