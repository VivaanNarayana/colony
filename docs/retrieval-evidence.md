# Diagnosing missed evidence reuse

**The retrieval fix is blocked.** This draft implements gate diagnostics and four
deliberately failing follow-up regressions. It does not change retrieval eligibility,
thresholds, models or response content. The failing tests are not skipped or marked
as expected failures; this branch is not merge-ready.

A stored finding can be present, active and backed by matching files yet fail
retrieval. Saved-answer relevance, source validity and answer sufficiency are
different questions. Matching hashes do not make saved prose true or relevant.

The retained follow-up regressions reproduce two rejection paths: a paraphrased
question fails the saved-question similarity gate, and individual source chunks
fail relevance scoring even though the requested behavior spans several files.
These cases are development regressions, not held-out evaluation evidence. The
original native answers, quality labels and token accounting remain unchanged.

## Rejected designs

Two fixed, provider-free feasibility probes were retained outside the repository.
A structural Python-source representation admitted an irrelevant function whose
name echoed the query. Removing declaration names and retaining operational syntax
rejected the fixed misleading examples, but also rejected both independent useful
examples and all four retained follow-ups. The second representation retained the
configuration, propagation and loop syntax; it was not a substitute test on simple
value transformations. Neither design was integrated into production retrieval.

The next design must establish useful source relevance independently of saved
claims or query echoes while supporting the actual multi-file control-flow cases.
Lowering a threshold, feeding the original publication question to the benchmark,
or shipping a route that only works on unrelated examples would not resolve these
failures. A passing local prototype would still require security, lifecycle,
transport and compatibility review before any separately authorized native run.

## Local gate diagnostics

Operators with access to a local store can capture the decisions from its actual
lookup path without adding diagnostics to HTTP or MCP responses:

```python
from colony.retrieval_diagnostics import trace_retrieval

with trace_retrieval() as trace:
    result = store.ask(question, allowed_namespaces, max_age=604800, threshold=0.60)

print(trace.events)
print(trace.dropped_events)
```

The caller supplies an existing authorized `Store` and permitted namespaces; this
developer interface is not an alternative authentication mechanism. The capture
is context-local and disabled by default. It includes candidate identifiers,
scores, counts and decisions, but excludes query text, saved content, paths,
source text and credentials. At most 256 events are retained. Additional events
are counted in `dropped_events`; a truncated trace is not a complete explanation.
Nested captures restore the outer capture, including after an exception.

`Store.ask` still performs its normal validation writes. When inspecting frozen
historical evidence, use a consistent disposable copy of the store and matching
source checkout; do not run it against the preserved original database. A local
replay can explain rejection, but cannot establish model behavior or token savings.

## Compatibility and acceptance

This branch starts from main, which does not contain PR15's adaptive context
budgets. No adaptive-budget implementation, evaluator changes or benchmark prompt
hints belong in this retrieval change. Its regressions must not lower answer
quality, source-validation, namespace, Git, age or lifecycle requirements.

Successful retrieval must return actual current source evidence with its original
coordinates. A stored question or summary can help locate a candidate, but cannot
certify its source relevance or support an answer by itself. Query echoes and
unused identifiers are important negative controls. Relevant contradictory source
evidence must remain available as evidence, without turning saved claims into truth.

Provider-free tests do not establish a gross-token improvement. Additional source
reads, local ranking and larger responses may cost more than they save. A later
matched evaluation requires separate authorization and must retain all learning,
publication, lookup, retry and failure costs with independent quality review.
