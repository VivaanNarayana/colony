"""Check inference work counts as well as retrieval behavior."""

import sqlite3

import pytest
import sqlite_vec

import colony.store
from colony.models import Trail
from colony.store import Store
from colony.validation import snapshot


def empty_lookup_store(tmp_path, namespaces=()):
    """Use real SQLite/vec setup without constructing any model or encrypted trail."""
    store = Store.__new__(Store)
    store.directory = store.repo = tmp_path
    with store.connect() as db:
        db.executescript(colony.store.SCHEMA)
        db.executemany(
            "INSERT INTO trails VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (f"{i:032x}", namespace, "Seed question", "seed.enc", "agent", 1,
                 "unverified", 0.0, None, "a" * 64)
                for i, namespace in enumerate(namespaces)
            ],
        )
    return store


@pytest.mark.parametrize(
    "requested, saved, reason",
    [
        ([], ["team"], "no_pull_scope"),
        (["team"], [], "no_relevant_trail"),
        (["team"], ["private"], "no_relevant_trail"),
        (["missing"], ["other", "private"], "no_relevant_trail"),
    ],
)
def test_empty_permitted_scope_skips_inference(tmp_path, monkeypatch, requested, saved, reason):
    store = empty_lookup_store(tmp_path, saved)

    def unexpected(*args, **kwargs):
        pytest.fail("No permitted trail can reach either retrieval path or inference")

    monkeypatch.setattr(colony.store, "embed", unexpected)
    monkeypatch.setattr(colony.store, "rank", unexpected)
    monkeypatch.setattr(store, "find_answer", unexpected)
    monkeypatch.setattr(store, "find_evidence", unexpected)
    monkeypatch.setattr(store, "read_content", unexpected)
    if not requested:
        monkeypatch.setattr(store, "connect", unexpected)
    assert store.ask("Where are the backups?", requested, 604800, 0.60) == {
        "hit": False,
        "reason": reason,
    }


def test_populated_scope_does_not_hold_a_transaction_during_embedding(tmp_path, monkeypatch):
    store = empty_lookup_store(tmp_path, ["private", "team"])
    vector = b"request vector"
    opened = []
    original_connect = store.connect

    from contextlib import contextmanager

    @contextmanager
    def connection():
        with original_connect() as db:
            opened.append(db)
            yield db

    def embed(*args, **kwargs):
        assert len(opened) == 1 and not opened[0].in_transaction
        # A committed write after the guard remains visible at candidate lookup.
        with sqlite3.connect(tmp_path / "colony.db") as concurrent:
            concurrent.execute(
                "INSERT INTO trails VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("f" * 32, "team", "New question", "new.enc", "agent", 1,
                 "unverified", 0.0, None, "b" * 64),
            )
        return vector

    def answer(*args, query_vector, _db, **kwargs):
        assert query_vector is vector and _db is opened[0]
        assert _db.execute("SELECT count(*) FROM trails WHERE namespace='team'").fetchone()[0] == 2
        return {"hit": True, "status": "validated"}

    monkeypatch.setattr(store, "connect", connection)
    monkeypatch.setattr(colony.store, "embed", embed)
    monkeypatch.setattr(store, "find_answer", answer)
    assert store.ask("Backups", ["absent", "team"], 604800, 0.60)["hit"]
    assert len(opened) == 1


def test_borrowed_answer_connection_stays_owned_by_caller(tmp_path, monkeypatch):
    store = empty_lookup_store(tmp_path)
    original_connect = store.connect
    vector = sqlite_vec.serialize_float32([1.0] + [0.0] * 383)
    with original_connect() as db:
        db.execute("CREATE TABLE writes(value INTEGER)")
        db.execute("INSERT INTO writes VALUES (1)")
        assert db.in_transaction
        monkeypatch.setattr(
            store, "connect", lambda: pytest.fail("Borrowed lookup must not open a connection")
        )
        assert not store.find_answer("Backups", ["team"], 604800, 0.60, query_vector=vector, _db=db)[
            "hit"
        ]
        assert db.in_transaction  # The helper must neither commit nor close it.
        assert db.execute("SELECT value FROM writes").fetchone()[0] == 1
    with original_connect() as db:
        assert db.execute("SELECT value FROM writes").fetchone()[0] == 1


def test_answer_error_rolls_back_request_writes(tmp_path, monkeypatch):
    store = empty_lookup_store(tmp_path, ["team"])
    with store.connect() as db:
        db.execute("CREATE TABLE writes(value INTEGER)")
    monkeypatch.setattr(colony.store, "embed", lambda *a, **kw: b"vector")

    def failing_answer(*args, _db, **kwargs):
        _db.execute("INSERT INTO writes VALUES (1)")
        raise ValueError("Lookup failed")

    monkeypatch.setattr(store, "find_answer", failing_answer)
    with pytest.raises(ValueError, match="Lookup failed"):
        store.ask("Backups", ["team"], 604800, 0.60)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM writes").fetchone()[0] == 0


def test_empty_scope_miss_does_not_hide_a_later_push(team):
    app, _, repo, _, key = team
    store = app.state.store
    question = "Configure build timeout"
    assert store.ask(question, ["team"], 604800, 0.60) == {
        "hit": False,
        "reason": "no_relevant_trail",
    }
    store.push(
        Trail(
            id="8" * 32,
            namespace="team",
            question=question,
            content="TIMEOUT = 30",
            sources=[snapshot(repo, "build.py")],
        ),
        key.grant,
    )
    answer = store.ask(question, ["team"], 604800, 0.60)
    assert answer["hit"] and answer["status"] == "validated"


@pytest.mark.parametrize("namespaces, expected", [([], 0), (["team"], 1)])
def test_miss_embeds_query_once_across_answer_and_evidence(team, monkeypatch, namespaces, expected):
    app, _, repo, _, key = team
    app.state.store.push(
        Trail(
            id="9" * 32,
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


def test_fallback_receives_same_request_vector_after_answer_commit(tmp_path, monkeypatch):
    store = empty_lookup_store(tmp_path, ["team"])
    with store.connect() as db:
        db.execute("CREATE TABLE writes(value INTEGER)")
    vector = b"request vector"
    seen = []
    monkeypatch.setattr(colony.store, "embed", lambda *args, **kwargs: vector)

    borrowed = []

    def answer(*args, query_vector, _db, **kwargs):
        seen.append(query_vector)
        borrowed.append(_db)
        _db.execute("INSERT INTO writes VALUES (1)")
        return {"hit": False}

    def evidence(*args, query_vector, **kwargs):
        seen.append(query_vector)
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            borrowed[0].execute("SELECT 1")
        with store.connect() as db:
            assert db.execute("SELECT value FROM writes").fetchone()[0] == 1
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
