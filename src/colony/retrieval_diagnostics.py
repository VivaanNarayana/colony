"""Opt-in, context-local retrieval diagnostics; never part of an ask response."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

_FIELDS = frozenset({
    "candidate_id", "reason", "accepted", "dense", "question_similarity", "relevance",
    "rerank", "coverage", "threshold", "count", "limit", "validated", "age_valid",
    "lifecycle_active", "git_compatible", "selected", "source_count", "returned_count",
    "exact_question", "question_threshold", "procedure_required", "question_pass",
    "relevance_pass", "procedure_pass",
})
_MAX_EVENTS = 256


@dataclass
class RetrievalTrace:
    events: list[dict] = field(default_factory=list)
    dropped_events: int = 0


_ACTIVE: ContextVar[RetrievalTrace | None] = ContextVar("colony_retrieval_trace", default=None)


@contextmanager
def trace_retrieval():
    """Capture gate metadata for a local Store call, without logging source text.

    This is a developer diagnostic, not a remote tool or an authorization boundary.
    Use only with stores/namespaces the caller is entitled to inspect. Collection
    is disabled by default, bounded, and restored when a nested capture exits.
    """
    trace = RetrievalTrace()
    token = _ACTIVE.set(trace)
    try:
        yield trace
    finally:
        _ACTIVE.reset(token)


def record_retrieval(stage: str, **metrics):
    trace = _ACTIVE.get()
    if trace is None:
        return
    if len(trace.events) >= _MAX_EVENTS:
        trace.dropped_events += 1
        return
    # Deliberately exclude queries, saved prose, paths, source text and credentials.
    safe = {
        key: value for key, value in metrics.items()
        if key in _FIELDS and type(value) in (bool, int, float, str, type(None))
    }
    trace.events.append({"stage": stage, **safe})
