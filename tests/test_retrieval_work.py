"""Exact-work elimination must preserve scores and avoid a persistent text cache."""

from types import SimpleNamespace

import colony.retrieval


def test_duplicate_passages_are_scored_once_and_scattered_in_original_order(monkeypatch):
    calls = []
    values = {"Seventeen days.": 3.25, "Unrelated.": -4.0, "Unlimited for legal holds.": 1.5}

    def rerank(question, passages, *, batch_size):
        assert batch_size == 1
        calls.append((question, list(passages)))
        return [values[p] for p in passages]

    monkeypatch.setattr(colony.retrieval, "models", lambda: (None, SimpleNamespace(rerank=rerank)))
    texts = ["Seventeen days.", "Unrelated.", "Seventeen days.", "Unlimited for legal holds."]
    assert colony.retrieval.rank("Retention?", texts) == [3.25, -4.0, 3.25, 1.5]
    assert calls == [
        ("Retention?", ["Seventeen days.", "Unrelated.", "Unlimited for legal holds."])
    ]
    # Another invocation computes again, including when the question changes.
    assert colony.retrieval.rank("Other question?", [texts[0]]) == [3.25]
    assert calls[-1] == ("Other question?", [texts[0]]) and len(calls) == 2
