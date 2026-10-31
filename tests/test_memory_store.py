import json
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import sqlite_vec

import colony.context
import colony.store
from colony.client import Client
from colony.memory import (
    MemoryConflict,
    MemoryForbidden,
    MemoryInvalid,
    MemoryNotFound,
    sanitize_memory,
)
from colony.models import (
    Grant,
    MemoryChange,
    MemoryCorrection,
    MemoryFeedback,
    Scope,
    Source,
    Trail,
)
from colony.store import Store
from colony.validation import snapshot


def new_id():
    return uuid.uuid4().hex


def member(name="author", *, pull=True, push=True, namespace="team"):
    return Grant(
        member_id=name,
        name=name,
        server="http://localhost:7331",
        expires_at=int(time.time()) + 3600,
        capture="none",
        scopes=[Scope(namespace=namespace, pull=pull, push=push)],
    )


@pytest.fixture
def memory_team(tmp_path, monkeypatch):
    # Deterministic vectors exercise real SQLite/vec0 candidate cutoffs without a model
    # download or a brittle assertion about semantic rank. Real ONNX/HTTP/MCP is covered
    # by the integration suite.
    vector = sqlite_vec.serialize_float32([1.0] + [0.0] * 383)
    monkeypatch.setattr(colony.store, "models", lambda: None)
    monkeypatch.setattr(colony.store, "index_version", lambda: "memory-test-index-v1")
    monkeypatch.setattr(colony.store, "embed", lambda *a, **kw: vector)
    monkeypatch.setattr(colony.store, "embed_passages", lambda texts: [vector] * len(texts))
    monkeypatch.setattr(colony.store, "rank", lambda q, texts: [5.0] * len(texts))
    monkeypatch.setattr(colony.context, "rank", lambda q, texts: [5.0] * len(texts))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "build.py").write_text("TIMEOUT = 30\n")
    store, owner = Store(tmp_path / "data", repo), member()
    trail = Trail(
        id=new_id(),
        namespace="team",
        question="Timeout setting",
        content="Set TIMEOUT = 30",
        sources=[snapshot(repo, "build.py")],
    )
    store.push(trail, owner)
    return store, repo, owner, trail


def change(trail, action, revision=0, **updates):
    return MemoryChange(
        namespace=trail.namespace,
        trail_id=trail.id,
        operation_id=new_id(),
        action=action,
        expected_revision=revision,
        **updates,
    )


def correction(trail, repo, **updates):
    data = dict(
        namespace=trail.namespace,
        trail_id=trail.id,
        operation_id=new_id(),
        expected_revision=0,
        question=trail.question,
        content="Current TIMEOUT = 30",
        sources=[snapshot(repo, "build.py")],
    )
    data.update(updates)
    return MemoryCorrection(**data)


def feedback(trail, verdict="incorrect", **updates):
    return MemoryFeedback(
        namespace=trail.namespace,
        trail_id=trail.id,
        operation_id=new_id(),
        verdict=verdict,
        **updates,
    )


def metadata(store, trail, owner):
    return store.inspect_memory(trail.id, trail.namespace, owner)["memory"]


def counts(store):
    with store.connect() as db:
        return tuple(
            db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("trails", "memory_state_v1", "memory_operations_v1", "memory_history_v1")
        )


def test_access_before_operation_results_and_owner_controls(memory_team):
    store, repo, owner, trail = memory_team
    contributor = member("colleague")
    operation = feedback(trail, "helpful")
    assert store.feedback_memory(operation, contributor)["memory"]["feedback"]["helpful"] == 1
    assert store.inspect_memory(trail.id, "team", contributor)["producer_id"] == owner.member_id
    for actor in (member(pull=False), member(namespace="private")):
        for target in (trail.id, "0" * 32, "invalid"):
            with pytest.raises(MemoryNotFound):
                store.inspect_memory(target, "team", actor)
        with pytest.raises(MemoryNotFound):
            store.feedback_memory(operation, actor)
    readonly = member("reader", push=False)
    for request, method in (
        (operation, store.feedback_memory),
        (correction(trail, repo), store.correct_memory),
        (change(trail, "retract"), store.change_memory),
    ):
        with pytest.raises(MemoryForbidden):
            method(request, readonly)
    with pytest.raises(MemoryForbidden):
        store.correct_memory(correction(trail, repo), contributor)
    with pytest.raises(MemoryForbidden):
        store.change_memory(change(trail, "retract"), contributor)
    with pytest.raises(MemoryNotFound):
        store.inspect_memory(trail.id, "private", member(namespace="private"))
    # Even a valid old operation ID cannot bypass the caller's current grant or target.
    with pytest.raises(MemoryForbidden):
        store.feedback_memory(operation, member("colleague", push=False))
    with pytest.raises(MemoryConflict):
        store.feedback_memory(operation, owner)


def test_feedback_latest_per_member_scrubbed_encrypted_and_does_not_change_validity(memory_team):
    store, _, owner, trail = memory_team
    before = store.ask(trail.question, ["team"], 604800, 0.60)
    assert before["status"] == "validated"
    with store.connect() as db:
        created = db.execute("SELECT created_at FROM trails WHERE id=?", (trail.id,)).fetchone()[0]
    note = "Private review narrative; password=must-not-persist"
    first = feedback(trail, note=note)
    store.feedback_memory(first, member("peer"))
    latest = feedback(trail, "outdated", note="A newer private narrative")
    store.feedback_memory(latest, member("peer"))
    store.feedback_memory(feedback(trail, "helpful"), member("second-peer"))
    current = metadata(store, trail, owner)
    assert current == {
        "state": "active",
        "revision": 0,
        "replacement_id": None,
        "feedback": {"helpful": 1, "incorrect": 0, "outdated": 1},
    }
    replay = store.feedback_memory(first, member("peer"))
    assert replay["replayed"] and replay["verdict"] == "incorrect"
    assert replay["memory"] == current  # No old vote is reapplied.
    with store.connect() as db:
        dumped = "\n".join(db.iterdump())
        assert "Private review narrative" not in dumped
        assert "must-not-persist" not in dumped and "A newer private narrative" not in dumped
        assert (
            db.execute("SELECT created_at FROM trails WHERE id=?", (trail.id,)).fetchone()[0]
            == created
        )
    inspected = store.inspect_memory(trail.id, "team", owner)
    assert len(inspected["history"]) == 3
    assert inspected["history"][-1]["note"] == "Private review narrative; [REDACTED]"
    after = store.ask(trail.question, ["team"], 604800, 0.60)
    assert after["status"] == before["status"] == "validated"
    assert after["id"] == before["id"] and after["created_at"] == created
    assert after["memory"] == current


def test_inspection_bounds_history_and_scrubs_unsanitized_legacy_content(memory_team):
    store, repo, owner, trail = memory_team
    legacy = Trail(
        id=new_id(),
        namespace="team",
        question="password=legacy-question",
        content=("password=legacy-content\n" + "x" * 5000),
        sources=[snapshot(repo, "build.py")],
    )
    store.push(legacy, owner)
    inspected = store.inspect_memory(legacy.id, "team", owner)
    assert inspected["question"] == "[REDACTED]"
    assert inspected["content"].startswith("[REDACTED]\n")
    assert len(inspected["content"]) == 4000 and inspected["content_truncated"]
    for _ in range(24):
        last = feedback(trail, "helpful")
        store.feedback_memory(last, owner)
    inspected = store.inspect_memory(trail.id, "team", owner)
    assert len(inspected["history"]) == 20
    assert inspected["history"][0]["operation_id"] == last.operation_id
    assert inspected["memory"]["feedback"]["helpful"] == 1


def test_legacy_redaction_expansion_is_a_bounded_inspection_view_and_client_accepts(memory_team):
    store, repo, owner, _ = memory_team
    normal_source = snapshot(repo, "build.py")
    legacy_source = Source(
        kind="doc",
        ref="token=a " * 128,
        content_hash="0" * 64,
        symbol="token=a " * 25,
        contains="token=a " * 125,
    )
    legacy = Trail(
        id=new_id(),
        namespace="team",
        question="token=a " * 250,
        content="password=private-legacy-sentinel",
        sources=[normal_source, legacy_source],
    )
    # Bypass client scrubbing, as valid historical direct HTTP pushes could do.
    store.push(legacy, owner)
    with store.connect() as db:
        row_before = tuple(db.execute("SELECT * FROM trails WHERE id=?", (legacy.id,)).fetchone())
        sources_before = [
            tuple(row)
            for row in db.execute("SELECT * FROM trail_sources WHERE trail_id=?", (legacy.id,))
        ]
    encrypted_before = (store.blobs / f"{legacy.id}.enc").read_bytes()
    inspected = store.inspect_memory(legacy.id, "team", owner)
    assert len(inspected["question"]) == 2000 and inspected["question_truncated"] is True
    assert inspected["content"] == "[REDACTED]" and not inspected["content_truncated"]
    assert inspected["sources"][0] == normal_source.model_dump()
    expanded = inspected["sources"][1]
    assert expanded["metadata_truncated"] is True
    assert len(expanded["ref"]) == 1024
    assert len(expanded["symbol"]) == 200
    assert len(expanded["contains"]) == 1000
    rendered = json.dumps(inspected)
    assert "token=a" not in rendered and "private-legacy-sentinel" not in rendered
    client = object.__new__(Client)
    client.keyfile = SimpleNamespace(grant=owner)
    client.request = lambda route, payload: inspected
    assert client.memory_inspect(legacy.id, "team") == inspected
    with store.connect() as db:
        assert (
            tuple(db.execute("SELECT * FROM trails WHERE id=?", (legacy.id,)).fetchone())
            == row_before
        )
        assert [
            tuple(row)
            for row in db.execute("SELECT * FROM trail_sources WHERE trail_id=?", (legacy.id,))
        ] == sources_before
    assert (store.blobs / f"{legacy.id}.enc").read_bytes() == encrypted_before


def test_normal_inspection_response_has_identical_fields_and_order(memory_team):
    store, _, owner, trail = memory_team
    expected = {
        "id": trail.id,
        "namespace": trail.namespace,
        "producer_id": owner.member_id,
        "question": trail.question,
        "content": trail.content,
        "content_truncated": False,
        "sources": [source.model_dump() for source in trail.sources],
        "memory": {
            "state": "active",
            "revision": 0,
            "replacement_id": None,
            "feedback": {"helpful": 0, "incorrect": 0, "outdated": 0},
        },
        "history": [],
    }
    assert json.dumps(store.inspect_memory(trail.id, "team", owner)) == json.dumps(expected)


@pytest.mark.parametrize("field", ["question", "ref", "symbol", "contains"])
def test_inspection_caps_never_truncate_mutation_inputs(memory_team, field):
    _, repo, _, trail = memory_team
    counts = {"question": 250, "ref": 128, "symbol": 25, "contains": 125}
    updates = {field: "token=a " * counts[field]}
    if field != "question":
        updates = {"sources": [snapshot(repo, "build.py").model_copy(update=updates)]}
    request = correction(trail, repo, **updates)
    with pytest.raises(MemoryInvalid):
        sanitize_memory(request)


def test_retract_restore_revision_replay_restart_and_push_cannot_reactivate(memory_team):
    store, repo, owner, trail = memory_team
    original = store.ask(trail.question, ["team"], 604800, 0.60)
    retract = change(trail, "retract", note="Temporarily exclude this finding")
    assert store.change_memory(retract, owner)["memory"]["revision"] == 1
    assert not store.ask(trail.question, ["team"], 604800, 0.60)["hit"]
    assert store.push(trail, owner)["duplicate"]
    assert metadata(store, trail, owner)["state"] == "retracted"
    restored = store.change_memory(change(trail, "restore", 1), owner)
    assert restored["memory"]["revision"] == 2 and restored["memory"]["state"] == "active"
    restarted = Store(store.directory, repo)
    replay = restarted.change_memory(retract, owner)
    assert replay["replayed"] and replay["action"] == "retract"
    assert replay["memory"]["state"] == "active" and replay["memory"]["revision"] == 2
    assert len(restarted.inspect_memory(trail.id, "team", owner)["history"]) == 2
    assert (
        restarted.ask(trail.question, ["team"], 604800, 0.60)["created_at"]
        == original["created_at"]
    )
    with pytest.raises(MemoryConflict):
        restarted.change_memory(change(trail, "retract", 0), owner)
    with pytest.raises(MemoryConflict):
        restarted.change_memory(retract.model_copy(update={"note": "different"}), owner)


def test_correction_is_atomic_immutable_tainted_and_own_replay(memory_team, monkeypatch):
    store, repo, owner, trail = memory_team
    original = store.inspect_memory(trail.id, "team", owner)
    request = correction(
        trail,
        repo,
        content="Current TIMEOUT = 30; password=secret-answer",
        note="private correction context",
    )
    # Correct must use the caller's transaction, not call public push/nest a connection.
    monkeypatch.setattr(store, "push", lambda *a: pytest.fail("Nested public push"))
    result = store.correct_memory(request, owner)
    old = store.inspect_memory(trail.id, "team", owner)
    replacement = store.inspect_memory(result["replacement_id"], "team", owner)
    assert old["content"] == original["content"] and old["sources"] == original["sources"]
    assert old["memory"]["state"] == "superseded" and old["memory"]["revision"] == 1
    assert replacement["content"] == "Current TIMEOUT = 30; [REDACTED]"
    assert (
        replacement["memory"]["state"] == "active" and replacement["producer_id"] == owner.member_id
    )
    with store.connect() as db:
        row = db.execute(
            "SELECT tainted FROM trails WHERE id=?", (result["replacement_id"],)
        ).fetchone()
        assert row["tainted"] == 1
        assert db.execute("SELECT count(*) FROM captures").fetchone()[0] == 0
        assert "private correction context" not in "\n".join(db.iterdump())
        assert "secret-answer" not in "\n".join(db.iterdump())
    restarted = Store(store.directory, repo)
    replay = restarted.correct_memory(request, owner)
    assert replay["replayed"] and replay["replacement_id"] == result["replacement_id"]
    assert counts(restarted) == (2, 2, 1, 1)
    assert restarted.push(trail, owner)["duplicate"]
    assert metadata(restarted, trail, owner)["state"] == "superseded"
    assert restarted.ask(trail.question, ["team"], 604800, 0.60)["id"] == result["replacement_id"]
    for action in ("restore", "retract"):
        with pytest.raises(MemoryConflict):
            restarted.change_memory(change(trail, action, 1), owner)
    with pytest.raises(MemoryConflict):
        restarted.correct_memory(correction(trail, repo, expected_revision=1), owner)


@pytest.mark.parametrize("failure", ["stale", "missing", "url", "mixed", "assertion"])
def test_correction_requires_all_current_file_evidence(memory_team, failure):
    store, repo, owner, trail = memory_team
    sources = [snapshot(repo, "build.py")]
    changes = {
        "stale": {"content_hash": "0" * 64},
        "missing": {"ref": "missing.py"},
        "url": {"kind": "url"},
        "assertion": {"contains": "missing snippet"},
    }
    bad = sources[0].model_copy(update=changes.get(failure, {"content_hash": "0" * 64}))
    sources = [*sources, bad] if failure == "mixed" else [bad]
    with pytest.raises(MemoryInvalid):
        store.correct_memory(correction(trail, repo, sources=sources), owner)
    assert metadata(store, trail, owner)["state"] == "active" and counts(store) == (1, 1, 0, 0)


@pytest.mark.parametrize("stage", ["index", "result", "changed_file"])
def test_failed_correction_preserves_original_and_no_operation(memory_team, monkeypatch, stage):
    store, repo, owner, trail = memory_team
    if stage == "result":

        def fail(*args, **kwargs):
            raise RuntimeError("simulated operation persistence failure")

        monkeypatch.setattr(store, "_memory_result", fail)
    else:
        real = store.index_sources

        def fail(db, trail_id, sources):
            real(db, trail_id, sources)
            if stage == "changed_file":
                (repo / "build.py").write_text("TIMEOUT = 60\n")
            else:
                raise RuntimeError("simulated source-index failure")

        monkeypatch.setattr(store, "index_sources", fail)
    with pytest.raises(MemoryInvalid if stage == "changed_file" else RuntimeError):
        store.correct_memory(correction(trail, repo), owner)
    assert counts(store) == (1, 1, 0, 0)
    assert metadata(store, trail, owner)["state"] == "active"
    assert store.ask(trail.question, ["team"], 604800, 0.60)["id"] == trail.id


def test_competing_corrections_use_one_revision_and_one_replacement(memory_team):
    store, repo, owner, trail = memory_team
    requests = [correction(trail, repo, content=f"Current timeout variant {i}") for i in range(2)]

    def submit(request):
        try:
            return store.correct_memory(request, owner)
        except MemoryConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, requests))
    assert sum(result is not None for result in results) == 1
    assert counts(store) == (2, 2, 1, 1)
    assert metadata(store, trail, owner)["revision"] == 1


def test_retired_vectors_cannot_crowd_answer_or_evidence_and_restore_after_reindex(memory_team):
    store, repo, owner, active = memory_team
    retired = []
    for i in range(26):
        ref = f"retired-{i}.md"
        (repo / ref).write_text("TIMEOUT = 30\n")
        trail = Trail(
            id=new_id(),
            namespace="team",
            question=active.question,
            content=active.content,
            sources=[snapshot(repo, ref)],
        )
        store.push(trail, owner)
        store.change_memory(change(trail, "retract"), owner)
        retired.append(trail)
    assert store.find_answer(active.question, ["team"], 604800, 0.6)["id"] == active.id
    evidence = store.find_evidence("TIMEOUT", ["team"], 604800)
    assert evidence["id"] == active.id and evidence["memory"]["state"] == "active"
    with store.connect() as db:
        db.execute("UPDATE settings SET value='old-index' WHERE key='index_version'")
    rebuilt = Store(store.directory, repo)
    with rebuilt.connect() as db:
        assert db.execute("SELECT count(*) FROM trail_vectors").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM source_chunks").fetchone()[0] == 1
    assert metadata(rebuilt, retired[0], owner)["state"] == "retracted"
    assert rebuilt.find_evidence("TIMEOUT", ["team"], 604800)["id"] == active.id
    rebuilt.change_memory(change(retired[-1], "restore", 1), owner)
    assert rebuilt.find_answer(active.question, ["team"], 604800, 0.6)["id"] == retired[-1].id
    assert rebuilt.find_evidence("TIMEOUT", ["team"], 604800)["id"] == retired[-1].id


@pytest.mark.parametrize("path", ["answer", "evidence"])
def test_candidate_retired_during_rerank_is_not_returned(memory_team, monkeypatch, path):
    store, _, owner, trail = memory_team
    retired = False

    def rerank(question, texts):
        nonlocal retired
        if not retired:
            store.change_memory(change(trail, "retract"), owner)
            retired = True
        return [5.0] * len(texts)

    monkeypatch.setattr(colony.store, "rank", rerank)
    if path == "answer":
        assert not store.find_answer(trail.question, ["team"], 604800, 0.6)["hit"]
    else:
        assert store.find_evidence("TIMEOUT", ["team"], 604800) is None
    assert retired and metadata(store, trail, owner)["state"] == "retracted"


def test_additive_legacy_migration_idempotence_and_future_version_rejection(memory_team):
    store, repo, owner, trail = memory_team
    with store.connect() as db:
        original = tuple(db.execute("SELECT * FROM trails WHERE id=?", (trail.id,)).fetchone())
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'memory_%'"
            )
        ]
        for table in tables:
            db.execute(f"DROP TABLE {table}")
    migrated = Store(store.directory, repo)
    assert metadata(migrated, trail, owner)["state"] == "active"
    with migrated.connect() as db:
        assert (
            tuple(db.execute("SELECT * FROM trails WHERE id=?", (trail.id,)).fetchone()) == original
        )
    migrated.change_memory(change(trail, "retract"), owner)
    restarted = Store(store.directory, repo)
    assert metadata(restarted, trail, owner)["state"] == "retracted"
    with restarted.connect() as db:
        assert db.execute("SELECT count(*) FROM memory_state_v1").fetchone()[0] == 1
        db.execute("UPDATE memory_schema SET version=2")
    with pytest.raises(MemoryInvalid):
        Store(store.directory, repo)
    with sqlite3.connect(store.directory / "colony.db") as db:
        assert db.execute("SELECT version FROM memory_schema").fetchone()[0] == 2
        assert db.execute("SELECT state FROM memory_state_v1").fetchone()[0] == "retracted"


def test_migration_failure_is_atomic(memory_team, monkeypatch):
    store, repo, owner, trail = memory_team
    with store.connect() as db:
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'memory_%'"
            )
        ]
        for table in tables:
            db.execute(f"DROP TABLE {table}")
    monkeypatch.setattr(
        colony.store, "MEMORY_SCHEMA_V1", (*colony.store.MEMORY_SCHEMA_V1, "INVALID SQL")
    )
    with pytest.raises(sqlite3.OperationalError):
        Store(store.directory, repo)
    with sqlite3.connect(store.directory / "colony.db") as db:
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'memory_%'"
        ).fetchall()
        assert db.execute("SELECT id FROM trails").fetchone()[0] == trail.id
