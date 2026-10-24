"""A later source-read failure must invalidate an earlier successful check."""

import json
import uuid

import pytest

import colony.context as context
import colony.store as storage
from colony.models import Trail
from colony.validation import snapshot

QUESTION = "What is archive retention?"
ANSWER = "Archive retention is seventeen days."


def save(store, repo, key, refs, *, question=QUESTION):
    trail = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question=question,
        content=ANSWER,
        sources=[snapshot(repo, ref) for ref in refs],
    )
    store.push(trail, key.grant)
    return trail


def change_after_validation(monkeypatch, ref, action):
    original = storage.validate
    changed = False

    def validate(root, sources):
        nonlocal changed
        result = original(root, sources)
        if not changed and result[0] == "validated" and any(s.ref == ref for s in sources):
            changed = True
            action(root / ref)
        return result

    monkeypatch.setattr(storage, "validate", validate)


@pytest.mark.parametrize("action", ["replace", "delete"])
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("whole_files", [False, True])
def test_answer_rejects_observed_change_after_validation(
    team, monkeypatch, action, mixed, whole_files
):
    app, _, repo, _, key = team
    store = app.state.store
    (repo / "archive.txt").write_text(ANSWER, encoding="utf-8")
    (repo / "encoding.txt").write_text("Exports use UTF-8.", encoding="utf-8")
    refs = ["archive.txt", "encoding.txt"] if mixed else ["archive.txt"]
    trail = save(store, repo, key, refs)
    change_after_validation(
        monkeypatch,
        "archive.txt",
        (lambda p: p.unlink()) if action == "delete" else lambda p: p.write_text("Replaced."),
    )
    result = store.find_answer(
        QUESTION, ["team"], 604800, 0.60, include_evidence_files=whole_files
    )
    assert result["status"] == "stale"
    assert result["recommendation"] == "research" and result["evidence"] == []
    assert next(c for c in result["checks"] if c["ref"] == "archive.txt")["result"] == "stale"
    with store.connect() as db:
        assert db.execute("SELECT status FROM trails WHERE id=?", (trail.id,)).fetchone()[0] == "stale"
        checks = json.loads(db.execute("SELECT result FROM validations ORDER BY id DESC").fetchone()[0])
        assert any(c["ref"] == "archive.txt" and c["result"] == "stale" for c in checks)


@pytest.mark.parametrize("data", [b"", b"\xff\xfe", ANSWER.encode()])
def test_matching_empty_binary_or_text_source_remains_validated(team, data):
    app, _, repo, _, key = team
    store = app.state.store
    (repo / "archive.txt").write_bytes(data)
    save(store, repo, key, ["archive.txt"])
    result = store.find_answer(QUESTION, ["team"], 604800, 0.60)
    assert result["status"] == "validated" and result["recommendation"] == "reuse_if_relevant"
    if data == ANSWER.encode():
        assert ANSWER in result["evidence"][0]["text"]
    else:
        assert result["evidence"] == []


def test_changed_answer_candidate_does_not_hide_valid_alternative(team, monkeypatch):
    app, _, repo, _, key = team
    store = app.state.store
    for ref in ["first.txt", "second.txt"]:
        (repo / ref).write_text(ANSWER, encoding="utf-8")
    save(store, repo, key, ["second.txt"])
    first = save(store, repo, key, ["first.txt"])
    change_after_validation(monkeypatch, "first.txt", lambda p: p.write_text("Replaced."))
    # Identical passages tie; recency gives the first candidate to the mutated file.
    result = store.find_answer(QUESTION, ["team"], 604800, 0.60)
    assert result["id"] != first.id and result["status"] == "validated"
    assert [e["ref"] for e in result["evidence"]] == ["second.txt"]


@pytest.mark.parametrize("mixed", [False, True])
def test_evidence_rejects_partial_context_after_one_source_changes(team, monkeypatch, mixed):
    app, _, repo, _, key = team
    store = app.state.store
    for ref in ["archive.txt", "other.txt"]:
        (repo / ref).write_text(ANSWER, encoding="utf-8")
    refs = ["archive.txt", "other.txt"] if mixed else ["archive.txt"]
    trail = save(store, repo, key, refs, question="Where are the archive logs?")
    change_after_validation(monkeypatch, "archive.txt", lambda p: p.write_text("Replaced."))
    assert store.find_evidence(QUESTION, ["team"], 604800) is None
    with store.connect() as db:
        assert db.execute("SELECT status FROM trails WHERE id=?", (trail.id,)).fetchone()[0] == "stale"


def test_answer_catches_change_between_chunk_discovery_and_final_display(team, monkeypatch):
    app, _, repo, _, key = team
    store = app.state.store
    (repo / "archive.txt").write_text(ANSWER, encoding="utf-8")
    save(store, repo, key, ["archive.txt"])
    original = context.source_chunks

    def chunks(root, sources, **options):
        result = original(root, sources, **options)
        (root / "archive.txt").write_text("Replaced.")
        return result

    monkeypatch.setattr(context, "source_chunks", chunks)
    result = store.find_answer(QUESTION, ["team"], 604800, 0.60)
    assert result["status"] == "stale" and result["evidence"] == []


def test_stale_discovery_source_does_not_suppress_other_valid_trail(team):
    app, _, repo, _, key = team
    store = app.state.store
    for ref in ["old.txt", "current.txt"]:
        (repo / ref).write_text(ANSWER, encoding="utf-8")
        save(store, repo, key, [ref], question="Where are the archive logs?")
    (repo / "old.txt").write_text("Replaced.")
    result = store.find_evidence(QUESTION, ["team"], 604800)
    assert result["status"] == "validated"
    assert [item["ref"] for item in result["evidence"]] == ["current.txt"]
