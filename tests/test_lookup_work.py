"""Check inference work counts as well as retrieval behavior."""

import pytest

import colony.store
from colony.store import Store


@pytest.mark.parametrize("namespaces, expected", [([], 0), (["team"], 1)])
def test_miss_embeds_query_once_across_answer_and_evidence(team, monkeypatch, namespaces, expected):
    app, _, _, _, _ = team
    calls = []
    original = colony.store.embed

    def counted(text, *, query=False):
        calls.append((text, query))
        return original(text, query=query)

    monkeypatch.setattr(colony.store, "embed", counted)
    assert not app.state.store.ask("Where are the backups?", namespaces, 604800, 0.60)["hit"]
    assert calls == [("Where are the backups?", True)] * expected


def test_direct_lookup_helpers_still_embed_their_own_query(team, monkeypatch):
    app, _, _, _, _ = team
    calls = []
    original = colony.store.embed

    def counted(text, *, query=False):
        calls.append((text, query))
        return original(text, query=query)

    monkeypatch.setattr(colony.store, "embed", counted)
    store = app.state.store
    assert not store.find_answer("Backups", ["team"], 604800, 0.60)["hit"]
    assert store.find_evidence("Backups", ["team"], 604800) is None
    assert calls == [("Backups", True)] * 2


def test_fallback_receives_same_request_vector(monkeypatch):
    store = Store.__new__(Store)
    vector = b"request vector"
    seen = []
    monkeypatch.setattr(colony.store, "embed", lambda *args, **kwargs: vector)

    def answer(*args, query_vector, **kwargs):
        seen.append(query_vector)
        return {"hit": False}

    def evidence(*args, query_vector, **kwargs):
        seen.append(query_vector)
        return {"hit": True, "status": "validated"}

    monkeypatch.setattr(store, "find_answer", answer)
    monkeypatch.setattr(store, "find_evidence", evidence)
    assert store.ask("Backups", ["team"], 604800, 0.60)["status"] == "validated"
    assert seen == [vector, vector]
