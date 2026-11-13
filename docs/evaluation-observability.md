# Additive evaluation observability

Run `python scripts/token_observability.py --events saved.events.jsonl --output new.json`
to create a separate, exclusively created diagnostic artifact from a saved log.
It launches no workers, models, retrieval, tools, reviews or network requests.
Existing logs, grades, protocols and reports are never edited. Input is bounded
at 64 MiB and the output binds its original bytes by SHA-256.

Turn usage uses the unchanged strict shared reader and remains authoritative.
The optional `response.created` / `response.completed` diagnostic envelope carries
`response.id` and, on completion, `response.usage` with input/output/cache counters.
Cache may also be `input_tokens_details.cached_tokens`. The report retains partial
records as unknown, deduplicates identical response receipts, rejects conflicting
receipts and checks complete sums against authoritative turn totals. Response sums
are never added to turn totals. Started-but-unfinished response coverage is separate
from numerical reconciliation. Native clients without this metadata remain unknown;
this reader does not make the current client expose per-response usage or alter a
frozen collector's accepted format. Existing raw collectors already retain emitted
JSONL; this explicit separate report can interpret additional receipts when available.

Completed-tool-to-response grouping uses only explicit `response_id` fields, never event order.
It reports validated lookup/cue presence, publication attempts/errors/stored or
unknown outcomes, declared source counts and repeated observed excerpts. Attempts
include started-but-unfinished calls, reported separately from completed outcomes.
Declared source counts come only from completed calls with available arguments. An excerpt
is repeated only when a later fixture read matches the latest lookup's path, hash,
line coverage and exact text in the same turn. This may be intentional verification,
not wasted work. Misses, stale lookups and changed hashes are not reuse evidence.
Diagnostics do not export question/answer/source prose or file paths. ID bindings
are hashed. Source counts do not establish semantic coverage or missing dependencies;
that requires a separate evidence-backed assessment. Cue presence does not establish
agent compliance. This report changes neither grading nor acceptance.

All tests use fake event streams and disposable files. No new evaluations are
required or authorized by this change. Host integration remains separate.
