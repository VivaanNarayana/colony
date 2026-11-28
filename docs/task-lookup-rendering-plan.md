# Render task lookup results once

Implementation plan for task-scoped MCP, written against `main` at `f027c9d`.
PR #48 implements the response contract below. See [the task integration guide](task-mcp.md)
for the consumer-facing fields. The replay figures in step 4 describe the original baseline experiment.

## Where to make the cut

Without a context budget, [TaskMCP research](../src/colony/task_mcp.py) returns the result of `Client.ask` directly. On a validated evidence-only hit, [the store](../src/colony/store.py) includes both structured excerpts in `evidence` and a top-level `content` string assembled from the same excerpts. [Git verification](../src/colony/git_client.py) also builds both representations when returning current local evidence. On a stale or unverified answer, the task can receive the entire saved body, up to 100,000 characters.

Make the cut at the model-facing task boundary, immediately after the existing client lookup. At that point the client has applied the configured namespace, Git and path checks. Keep the existing `evidence` entries and top-level `sources` table as the citation format. The [evaluation reader](../scripts/token_evaluation/task_evidence.py) already joins them by path and derives the source hash and coordinates without reading a prose copy.

## Response contract

| Lookup outcome | What the task receives |
| --- | --- |
| Validated evidence-only hit | Each selected span appears once in `evidence`. Keep its original text, path, start line, complete-file flag and associated source hash. Omit the generated `content` and any obsolete `content_truncated` flag. Keep separate spans even when two files contain identical text. |
| Validated saved-answer hit | Deliver the authored answer once in `content`, alongside its supporting evidence. The answer can contain interpretation that an excerpt alone cannot supply. Use the match kind to distinguish the two. Preserve the current handling of long validated answers. |
| Stale or unverified hit | Give the original status, identity, source bindings, checks, lifecycle, taint, provenance, Git information and research recommendation when present. Withhold saved `content` and its truncation flag. If a body was withheld, set `content_withheld` to the status, as the [adaptive renderer](../src/colony/compact.py) does. |
| Miss, denial or unavailable lookup | Preserve the existing miss or sanitized error and the ability to continue research. If an evidence-only hit has only a prose summary and lacks usable structured evidence or its source binding, return the existing recoverable `lookup_unavailable` shape. Never manufacture a citation from a summary. |
| Budgeted task lookup | Continue through the ordinary adaptive MCP delegate. Its negotiated profile, fitted text block and budget receipt already cover this case. |

## Implementation sequence

1. **Capture the behavior at the tool boundary.** Add focused regressions using `TaskMCP.call_tool("research", {})`. Put a unique marker in each of two excerpts and include the store-generated prose copy. Add separate stale and unverified fixtures with a 100,000-character saved body, and a validated answer with distinct prose. Assert against the JSON inside the returned MCP text block.
2. **Project the unbudgeted result.** Apply the table above using the result of the single existing `Client.ask` call. Copy the top-level mapping before dropping fields. Preserve other metadata and the original evidence entries. Keep server provenance for server-only validation and caller provenance for locally verified Git results. The adaptive packer stamps a negotiated profile and rewrites the evidence shape, so use only the field-handling convention here. Let the current MCP registration serialize the result once.
3. **Verify callers and real transport.** Exercise a real stdio client. Expect one JSON text block and no duplicate structured payload. Compare the parsed citations before and after with the existing evaluator reader. Cover a Git mismatch that yields usable local evidence, a revoked path that yields a miss, complete and partial files, two spans in one file, Unicode and redacted excerpts. Check that a lookup alone cannot mint publication references or unlock publication. Document the field change and migration from `content` to `evidence` in [the task integration guide](task-mcp.md).
4. **Measure the cut.** Replay all 11 evidence-only hits from the [archived v16 measurements](results/luna-low/colony-live-benchmark-v16/raw-measurements.jsonl) through the base and candidate task renderers, using the same reconstructed store-shaped responses. Verify equivalent source identities and spans first. Then record UTF-8 bytes and `o200k_base` counts for the actual returned text. The initial field-removal replay saved 223–502 tokens per response, median 291. Recompute from the implemented renderer and include every case.

## Acceptance

- Every evidence-only fixture delivers every selected span and binding in a single representation. The stale and unverified body markers appear zero times. The validated answer retains its authored text and supporting citations.
- Each explicit task lookup makes one client lookup. Rendering adds no request, retry, file read or publication. Protected-path denial and Git mismatch retain their respective outcomes.
- Run the task routing, source-access, stdio, adaptive-lookup and evaluator evidence tests, followed by the full suite and Ruff. Compare ordinary MCP, raw client/API responses and configured adaptive receipts with the base revision. These interfaces sit outside the proposed change.
- Report the replay as a change in model-facing response size. A claim about gross worker tokens needs a separate matched evaluation that includes cached input, output, learning, reuse, failures and independently graded answer quality.
