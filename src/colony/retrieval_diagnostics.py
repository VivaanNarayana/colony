"""Opt-in bounded local observations. Never answer evidence or a ranking input."""
import copy
from contextlib import suppress


class RetrievalDiagnostics:
    """Use a fresh buffer per lookup. Nothing is written to disk or returned to agents."""

    def __init__(self, limit=128):
        if type(limit) is not int or not 1 <= limit <= 512:
            raise ValueError("Diagnostic limit must be 1..512")
        self.limit = limit
        self._records = []
        self.dropped = 0

    def record(self, stage, **values):
        if len(self._records) >= self.limit:
            self.dropped += 1
            return
        self._records.append({"stage": stage, **values})

    def snapshot(self):
        return {"contract": "colony.retrieval-diagnostics/1", "records": copy.deepcopy(self._records),
                "dropped": self.dropped,
                "meaning": "Declared-source validation and retrieval gates, not semantic support."}


def emit(diagnostics, stage, **values):
    if diagnostics is not None:
        # Optional observation must not change retrieval or prevent an answer.
        with suppress(Exception):
            diagnostics.record(stage, **values)
