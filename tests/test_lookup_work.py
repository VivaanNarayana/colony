"""Check inference work counts as well as retrieval behavior."""

import pytest
import sqlite_vec

import colony.store
from colony.models import Trail
from colony.validation import snapshot


@pytest.mark.parametrize(
    "namespaces, populated, expected", [([], False, 0), (["team"], False, 0), (["team"], True, 1)]
)
def test_miss_embeds_query_once_across_answer_and_evidence(
    team, monkeypatch, namespaces, populated, expected
):
    app, _, repo, _, key = team
    if populated:
        app.state.store.push(
            Trail(
                id="7" * 32,
                namespace="team",
                question="Build timeout",
                content="TIMEOUT = 30",
                sources=[snapshot(repo, "build.py")],
            ),
            key.grant,
        )
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


def test_fallback_receives_same_request_vector(team, monkeypatch):
    app, _, repo, _, key = team
    store = app.state.store
    store.push(
        Trail(
            id="7" * 32,
            namespace="team",
            question="Build timeout",
            content="TIMEOUT = 30",
            sources=[snapshot(repo, "build.py")],
        ),
        key.grant,
    )
    seen = []
    original_answer, original_evidence = store.find_answer, store.find_evidence

    def answer(*args, query_vector, **kwargs):
        seen.append(query_vector)
        return original_answer(*args, query_vector=query_vector, **kwargs)

    def evidence(*args, query_vector, **kwargs):
        seen.append(query_vector)
        return original_evidence(*args, query_vector=query_vector, **kwargs)

    monkeypatch.setattr(store, "find_answer", answer)
    monkeypatch.setattr(store, "find_evidence", evidence)
    assert not store.ask("Backups", ["team"], 604800, 0.60)["hit"]
    assert len(seen) == 2 and seen[0] is seen[1]
    assert len(seen[0]) == 384 * 4


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
    # The ineligible row is rejected; a sole exact match needs no ranking decision.
    assert not reranked


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


@pytest.mark.parametrize(
    "question, text, coverage, expected",
    [
        ("What detects configuration changes?", "Configuration changes use digests.", 0.49, False),
        ("What detects configuration changes?", "Configuration changes use digests.", 0.5, True),
        ("What detects configuration changes?", "Unrelated audit retention details.", 0.9, False),
        ("Start the worker", "The worker executes background jobs.", 0.9, False),
        ("Start the worker", "Run `workerctl launch`.", 0.1, True),
    ],
)
def test_evidence_guards_skip_impossible_passages_before_reranking(
    team, monkeypatch, question, text, coverage, expected
):
    app, _, repo, _, key = team
    store = app.state.store
    (repo / "guide.md").write_text(text)
    store.push(
        Trail(
            id="3" * 32,
            namespace="team",
            question="Audit settings",
            content="Retain audit records.",
            sources=[snapshot(repo, "guide.md")],
        ),
        key.grant,
    )
    vector = sqlite_vec.serialize_float32([1.0] + [0.0] * 383)
    with store.connect() as db:
        db.execute("UPDATE source_vectors SET embedding=?", (vector,))
    monkeypatch.setattr(
        colony.store, "keyword_coverage", lambda q, passages: [coverage] * len(passages)
    )
    reranked = []

    def rank(question, passages):
        reranked.extend(passages)
        return [8.0] * len(passages)

    monkeypatch.setattr(colony.store, "rank", rank)
    result = store.find_evidence(question, ["team"], 604800, query_vector=vector)
    assert bool(result) == expected
    assert bool(reranked) == expected
    if expected:
        assert text in result["content"]
