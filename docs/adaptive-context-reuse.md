# Adaptive lookup reuse and publication coverage

A validated adaptive lookup can carry a short `next_action` cue after evidence
packing and citation bounds. The cue tells the agent to use current excerpts for
covered claims and research gaps or conflicts. It explicitly distinguishes cited
files from whole-repository coverage and notes that saved prose may be withheld.

The cue uses only spare response budget. It never repacks, drops, clips or replaces
source excerpts, identities, line bounds, trust markers or lifecycle state. Its
bytes/tokens are included in the final receipt. If it does not fit, the original
fitted evidence is returned. Misses, stale/unverified results and responses without
current source reads never receive a cue to reuse them. The text is generated
locally; source-supplied `next_action` text is discarded.

The adaptive ask tool description requests the focused task question without
appending retrieval instructions. This does not rewrite the user's question or
change ranking thresholds. The installed publication policy asks for all observed
source ref/hash pairs supporting each claim, or narrower claims, and discourages
restatements. For example, a configuration value propagated by a caller into a
retry loop needs evidence from the config, caller and loop. Use the existing
payload template with all those actual observed source pairs. Do not reread or
research only to publish. Source identity validation cannot certify semantic
coverage, and this policy is not an automatic semantic admission check.

Answer detail, necessary research, user-requested deliverables and output format
remain governed by the user's task. Publication is conditional; abstention and
publication failures must not block the answer. The user chose to keep host
completion integration separate. These changes do not install a host hook or
force publication. Broader retrieval diagnostics and evaluator request-level
usage instrumentation remain separately scoped follow-ups; no ranking change is
justified by the retained trace alone.

Offline regressions cover actual storage/authenticated HTTP/client/MCP delivery,
complete and missing dependency coverage, evidence-only responses, both supported
budget units, tight budgets, untrusted injected guidance, and setup/stdio policy
delivery. They establish interface behavior, not that a model will obey the cue or
that it saves tokens. Historical campaign protocols, logs, reviews and outcomes
remain frozen. A new native evaluation requires separate authorization.
