"""Duplicate acknowledgments remain durable while missing source indexes recover."""

import logging
import uuid
from contextlib import contextmanager

import httpx
import pytest
from cryptography.fernet import Fernet

import colony.context as context
import colony.store as storage
from colony.client import Client
from colony.context import source_chunks
from colony.models import Trail
from colony.retrieval import index_version
from colony.security import private_write
from colony.validation import snapshot

FACT = "The kiln cooling threshold is sixty degrees Celsius."


def seed(team, *, missing=(0,), count=1, data=None, source_order=None):
    app, _, repo, _, key = team
    sources, contents = [], []
    for index in range(count):
        content = data if data is not None else f"{FACT}\nKiln reference number {index}.\n".encode()
        path = repo / f"kiln-{index}.md"
        path.write_bytes(content)
        sources.append(snapshot(repo, path.name))
        contents.append(content)
        if index in missing:
            path.unlink()
    trail = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question="Where are kiln inspection logs?",
        content="Consult the inspection register.",
        sources=[sources[i] for i in source_order] if source_order is not None else sources,
    )
    app.state.store.push(trail, key.grant)
    return app.state.store, repo, key, trail, contents


def state(store, trail):
    with store.connect() as db:
        return {
            "trail": tuple(db.execute("SELECT * FROM trails WHERE id=?", (trail.id,)).fetchone()),
            "sources": [tuple(r) for r in db.execute("SELECT * FROM trail_sources")],
            "validations": [tuple(r) for r in db.execute("SELECT * FROM validations")],
            "chunks": [tuple(r) for r in db.execute("SELECT * FROM source_chunks ORDER BY id")],
            "vectors": [tuple(r) for r in db.execute("SELECT rowid,embedding FROM source_vectors ORDER BY rowid")],
            "completion": [tuple(r) for r in db.execute("SELECT * FROM source_index_state ORDER BY trail_id")],
        }


def restore(repo, contents, indexes):
    for index in indexes:
        (repo / f"kiln-{index}.md").write_bytes(contents[index])


def test_exact_duplicate_restores_discoverable_source_without_changing_trail(team):
    store, repo, key, trail, contents = seed(team)
    before = state(store, trail)
    assert before["chunks"] == []
    assert before["completion"] == []
    restore(repo, contents, [0])
    receipt = store.push(trail, key.grant)
    assert receipt == {"id": trail.id, "stored": True, "duplicate": True}
    after = state(store, trail)
    assert after["chunks"] and len(after["chunks"]) == len(after["vectors"])
    assert after["completion"] == [(trail.id, index_version())]
    for field in ["trail", "sources", "validations"]:
        assert after[field] == before[field]
    evidence = store.find_evidence("What is the kiln cooling threshold?", ["team"], 3600)
    assert evidence["status"] == "validated" and FACT in evidence["content"]


def test_partial_multisource_retry_preserves_whole_old_index_until_available(team):
    store, repo, key, trail, contents = seed(team, missing=(1, 2), count=3, source_order=[2, 0, 1])
    before = state(store, trail)
    assert {row[2] for row in before["chunks"]} == {"kiln-0.md"}
    restore(repo, contents, [1])
    assert store.push(trail, key.grant)["duplicate"]
    assert state(store, trail) == before
    restore(repo, contents, [2])
    assert store.push(trail, key.grant)["duplicate"]
    after = state(store, trail)
    assert [row[2] for row in after["chunks"]] == ["kiln-2.md", "kiln-0.md", "kiln-1.md"]
    assert after["trail"] == before["trail"]
    assert len(after["chunks"]) == len(source_chunks(repo, trail.sources))


@pytest.mark.parametrize("data", [FACT.encode(), b"", b"\xff\xfe"])
def test_noop_duplicate_does_not_embed_or_write_even_for_zero_chunks(team, monkeypatch, data):
    store, _, key, trail, _ = seed(team, missing=(), data=data)
    before = state(store, trail)
    calls, sql, reads = [], [], []
    original_read = context.read_source

    def counted(root, ref):
        reads.append(ref)
        return original_read(root, ref)

    monkeypatch.setattr(context, "read_source", counted)

    def unexpected(texts):
        calls.append(texts)
        raise AssertionError("No embedding is needed for an unchanged source index")

    monkeypatch.setattr(storage, "embed_passages", unexpected)
    original_connect = store.connect

    @contextmanager
    def traced():
        with original_connect() as db:
            db.set_trace_callback(sql.append)
            yield db

    monkeypatch.setattr(store, "connect", traced)
    assert store.push(trail, key.grant)["duplicate"]
    assert calls == [] and state(store, trail) == before
    assert reads == []
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in sql)


@pytest.mark.parametrize("failure", ["inference", "insert", "partial_insert", "marker"])
def test_optional_failure_keeps_ack_old_index_and_sanitized_diagnostic(
    team, monkeypatch, caplog, failure
):
    store, repo, key, trail, contents = seed(team, missing=(1,), count=2)
    before = state(store, trail)
    restore(repo, contents, [1])
    if failure == "inference":
        def fail(texts):
            raise RuntimeError("private material must not be logged")

        monkeypatch.setattr(storage, "embed_passages", fail)
    else:
        with store.connect() as db:
            condition = "WHEN NEW.ref='kiln-1.md' " if failure == "partial_insert" else ""
            table = "source_index_state" if failure == "marker" else "source_chunks"
            db.execute(
                f"CREATE TRIGGER deny_repair BEFORE INSERT ON {table} "
                + condition
                +
                "BEGIN SELECT RAISE(ABORT, 'private material must not be logged'); END"
            )
    with caplog.at_level(logging.WARNING, logger="colony.store"):
        receipt = store.push(trail, key.grant)
    assert receipt == {"id": trail.id, "stored": True, "duplicate": True}
    assert state(store, trail) == before
    assert "Source index refresh failed" in caplog.text
    assert "private material" not in caplog.text


@pytest.mark.parametrize("block", ["changed", "version", "payload", "producer"])
def test_unavailable_or_unauthorized_duplicate_never_repairs(team, monkeypatch, block):
    store, repo, key, trail, contents = seed(team)
    restore(repo, contents, [0])
    before = state(store, trail)
    calls = []

    def unexpected(texts):
        calls.append(texts)
        raise AssertionError("Blocked repair must not embed")

    monkeypatch.setattr(storage, "embed_passages", unexpected)
    if block == "changed":
        (repo / "kiln-0.md").write_text("Different source bytes.", encoding="utf-8")
    if block == "version":
        with store.connect() as db:
            db.execute("UPDATE settings SET value=? WHERE key='index_version'", (index_version() + ":other",))
    if block in {"payload", "producer"}:
        candidate = trail.model_copy(update={"content": "Changed"}) if block == "payload" else trail
        grant = key.grant.model_copy(update={"member_id": "another"}) if block == "producer" else key.grant
        with pytest.raises(ValueError, match="different payload"):
            store.push(candidate, grant)
    else:
        assert store.push(trail, key.grant)["duplicate"]
    assert calls == [] and state(store, trail) == before
    if block == "version":
        with store.connect() as db:
            version = db.execute("SELECT value FROM settings WHERE key='index_version'").fetchone()
            assert version[0] == index_version() + ":other"


def test_repair_preserves_source_bounds_age_and_scope(team):
    store, repo, key, trail, contents = seed(team, missing=tuple(range(9)), count=9)
    restore(repo, contents, range(9))
    with store.connect() as db:
        db.execute("UPDATE trails SET created_at=0 WHERE id=?", (trail.id,))
    before = state(store, trail)
    assert store.push(trail, key.grant)["duplicate"]
    after = state(store, trail)
    assert after["trail"] == before["trail"]
    assert len(after["chunks"]) <= 96
    assert {row[2] for row in after["chunks"]} == {f"kiln-{i}.md" for i in range(8)}
    assert store.find_evidence("What is the kiln cooling threshold?", ["team"], 3600) is None
    assert store.find_evidence("What is the kiln cooling threshold?", ["other"], 3600) is None


def test_repair_replaces_at_most_96_chunks_and_subsequent_retry_is_noop(team, monkeypatch):
    data = "\n\n".join(f"{FACT} Reference {i}." for i in range(110)).encode()
    store, repo, key, trail, contents = seed(team, data=data)
    restore(repo, contents, [0])
    original = storage.embed_passages
    counts = []

    def counted(texts):
        counts.append(len(texts))
        return original(texts)

    monkeypatch.setattr(storage, "embed_passages", counted)
    assert store.push(trail, key.grant)["duplicate"]
    repaired = state(store, trail)
    assert len(repaired["chunks"]) == len(repaired["vectors"]) == 96
    assert counts == [96]
    assert store.push(trail, key.grant)["duplicate"]
    assert state(store, trail) == repaired and counts == [96]


def test_encrypted_retry_removes_durable_duplicate_despite_optional_repair_failure(
    team, tmp_path, monkeypatch, caplog
):
    _, api, _, admin, _ = team
    store, repo, key, trail, contents = seed(team)
    home = tmp_path / "agent"
    private_write(home / "identity.json", key.model_dump_json().encode())
    private_write(home / "issuer.pub", (admin / "issuer.pub").read_bytes())
    private_write(home / "queue.key", Fernet.generate_key())
    client = Client(home)

    def offline(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "post", offline)
    assert client.push(trail)["queued"]
    queue = next((home / "queue").glob("*.enc"))
    assert trail.content.encode() not in queue.read_bytes()
    restore(repo, contents, [0])

    def fail(texts):
        raise RuntimeError("private material must not be logged")

    def online(url, **kwargs):
        kwargs.pop("timeout", None)
        return api.post(httpx.URL(url).path, **kwargs)

    monkeypatch.setattr(storage, "embed_passages", fail)
    monkeypatch.setattr(httpx, "post", online)
    with caplog.at_level(logging.WARNING, logger="colony.store"):
        assert client.retry() == {"sent": 1, "pending": False}
    assert not queue.exists()
    assert len(state(store, trail)["chunks"]) == 0
    assert "Source index refresh failed" in caplog.text and "private material" not in caplog.text


def test_completion_is_not_a_freshness_claim(team, monkeypatch):
    store, repo, key, trail, _ = seed(team, missing=())
    before = state(store, trail)
    (repo / "kiln-0.md").write_text("The kiln threshold has changed.", encoding="utf-8")
    with monkeypatch.context() as duplicate:
        def forbidden(*args, **kwargs):
            raise AssertionError("Completed duplicate must not read sources")

        duplicate.setattr(context, "read_source", forbidden)
        assert store.push(trail, key.grant)["duplicate"]
    assert state(store, trail) == before
    assert store.find_evidence("What is the kiln cooling threshold?", ["team"], 3600) is None


def test_legacy_unknown_completion_inspects_once_without_reembedding(team, monkeypatch):
    store, _, key, trail, _ = seed(team, missing=())
    with store.connect() as db:
        db.execute("DROP TABLE source_index_state")
    reopened = storage.Store(store.directory, store.repo)
    before = state(reopened, trail)
    assert before["completion"] == []
    reads = []
    original_read = context.read_source

    def counted(root, ref):
        reads.append(ref)
        return original_read(root, ref)

    def forbidden(texts):
        raise AssertionError("Matching descriptors must not reembed")

    monkeypatch.setattr(context, "read_source", counted)
    monkeypatch.setattr(storage, "embed_passages", forbidden)
    assert reopened.push(trail, key.grant)["duplicate"]
    assert reads == ["kiln-0.md"]
    assert reopened.push(trail, key.grant)["duplicate"]
    assert reads == ["kiln-0.md"]
    after = state(reopened, trail)
    assert after.pop("completion") == [(trail.id, index_version())]
    before.pop("completion")
    assert after == before


def test_migration_rebuilds_completion_only_for_available_sources(team):
    store, repo, key, available, _ = seed(team, missing=())
    other = repo / "retort.md"
    other.write_text("The retort is calibrated annually.", encoding="utf-8")
    unavailable = available.model_copy(update={"id": uuid.uuid4().hex, "sources": [snapshot(repo, other.name)]})
    store.push(unavailable, key.grant)
    with store.connect() as db:
        db.execute("UPDATE settings SET value='old-version' WHERE key='index_version'")
        db.execute("UPDATE source_index_state SET index_version='old-version'")
    other.unlink()
    storage.Store(store.directory, repo)
    with store.connect() as db:
        assert [tuple(row) for row in db.execute("SELECT * FROM source_index_state")] == [(available.id, index_version())]
        assert db.execute("SELECT value FROM settings WHERE key='index_version'").fetchone()[0] == index_version()
