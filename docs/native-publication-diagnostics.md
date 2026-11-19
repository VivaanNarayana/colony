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
File open, write, flush and close run in one daemon writer, outside the transport
lock. Its nonblocking queue holds at most 256 allowlisted events plus one summary;
raw protocol payloads never enter it. Shutdown waits at most 200 ms for logging.
A stalled writer may leave an absent or incomplete sidecar. A missing final summary
never establishes that no publication occurred. The prospective `/17` evaluator can
recover strictly validated, hash-bound event observations from a sidecar without a
summary; it labels these `partial_metadata`, not a complete task outcome. It reports
only observed read/refresh flags and attempt/outcome counts. `no_attempt_observed`
means no attempt appears in the captured events, not deliberate abstention or proof
that no later attempt occurred. Eligibility, pending requests, dropped metadata and
model decisions cannot be inferred from this prefix. Malformed, unknown, oversized
or inconsistently ordered records remain unavailable; arbitrary fields are never
forwarded. Earlier collectors and saved reports retain their original behavior.

`refreshed_with_publish_no_attempt` is a protocol observation, not a statement of
model awareness or deliberate abstention. Model-visible catalog and abstention
reason stay `unobserved`: this native stdio surface exposes neither. A started
publication without a correlated response is `attempted_outcome_unobserved`.
Receipts establish only reported outcomes, never semantic truth or safe content.
Refresh requires a successful catalog containing publish from a request issued
after the latest notification. A request already in flight when a notification
arrives cannot confirm that refresh. New notifications invalidate the previous
refresh observation; catalog delivery alone does not prove model awareness.

This is separate host infrastructure. Connecting it to an evaluator requires
prospectively pinning its bytes and exact launch command. It must not modify old
traces, grades, protocols, or worker prompts, and is not a completion hook.
