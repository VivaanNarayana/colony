"""Check inference work counts as well as retrieval behavior."""

import pytest
import sqlite_vec

import colony.store
from colony.models import Trail
from colony.store import Store
from colony.validation import snapshot


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


@pytest.mark.parametrize("exact", [False, True])
def test_question_guard_skips_decryption_and_reranking_but_preserves_exact_records(
    team, monkeypatch, exact
):
    app, _, repo, _, key = team
    store = app.state.store
    store.push(
        Trail(
            id="1" * 32,
            namespace="team",
            question="Start service" if exact else "Audit rotation",
            content="A diagnostic record.",
            sources=[snapshot(repo, "build.py")],
        ),
        key.grant,
    )
    vector = sqlite_vec.serialize_float32([1.0] + [0.0] * 383)
    unrelated = sqlite_vec.serialize_float32([0.0, 1.0] + [0.0] * 382)
    with store.connect() as db:
        db.execute("UPDATE trail_vectors SET embedding=?", (vector,))
        db.execute("UPDATE trail_questions SET embedding=?", (unrelated,))
    reads, reranked = [], []
    original = store.read_content

    def read(ref):
        reads.append(ref)
        return original(ref)

    def rank(question, passages):
        reranked.extend(passages)
        return [5.0] * len(passages)

    monkeypatch.setattr(store, "read_content", read)
    monkeypatch.setattr(colony.store, "rank", rank)
    result = store.find_answer("Start service", ["team"], 604800, 0.60, query_vector=vector)
    assert result["hit"] == exact
    assert len(reads) == int(exact)
    assert len(reranked) == int(exact)


def test_procedure_guard_skips_reranking_ineligible_overview(team, monkeypatch):
    app, _, repo, _, key = team
    store = app.state.store
    store.push(
        Trail(
            id="2" * 32,
            namespace="team",
            question="Start local service",
            content="The local service handles background jobs.",
            sources=[snapshot(repo, "build.py")],
        ),
        key.grant,
    )
    vector = sqlite_vec.serialize_float32([1.0] + [0.0] * 383)
    with store.connect() as db:
        db.execute("UPDATE trail_vectors SET embedding=?", (vector,))
        db.execute("UPDATE trail_questions SET embedding=?", (vector,))
    reranked = []

    def rank(question, passages):
        reranked.extend(passages)
        return [5.0] * len(passages)

    monkeypatch.setattr(colony.store, "rank", rank)
    assert not store.find_answer("Start service", ["team"], 604800, 0.60, query_vector=vector)[
        "hit"
    ]
    assert not reranked
