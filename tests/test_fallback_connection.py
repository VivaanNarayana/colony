"""Connection reuse preserves the answer/evidence transaction boundary."""

import uuid
from contextlib import contextmanager

import pytest

from colony.models import Trail
from colony.validation import snapshot


def track_connections(monkeypatch, store):
    opened = []
    original = store.connect

    @contextmanager
    def connect():
        with original() as db:
            opened.append(db)
            yield db

    monkeypatch.setattr(store, "connect", connect)
    return opened


@pytest.mark.parametrize("question", ["What is the build timeout?", "Who owns lunar archives?"])
def test_answer_or_miss_opens_one_connection(team, monkeypatch, question):
    app, _, repo, _, key = team
    store = app.state.store
    store.push(
        Trail(
            id=uuid.uuid4().hex,
            namespace="team",
            question="What is the build timeout?",
            content="The build timeout is thirty seconds.",
            sources=[snapshot(repo, "build.py")],
        ),
        key.grant,
    )
    opened = track_connections(monkeypatch, store)
    result = store.ask(question, ["team"], 604800, 0.60)
    assert len(opened) == 1
    assert result["hit"] is (question == "What is the build timeout?")


def test_real_evidence_fallback_matches_standalone_result(team, monkeypatch):
    app, _, repo, _, key = team
    store = app.state.store
    (repo / "archive.md").write_text("Archive retention is seventeen days.\n", encoding="utf-8")
    store.push(
        Trail(
            id=uuid.uuid4().hex,
            namespace="team",
            question="Where are archive logs stored?",
            content="Archive logs go to the journal.",
            sources=[snapshot(repo, "archive.md")],
        ),
        key.grant,
    )
    question = "What is archive retention?"
    expected = store.find_evidence(question, ["team"], 604800)
    assert expected and expected["status"] == "validated"
    opened = track_connections(monkeypatch, store)
    monkeypatch.setattr(store, "find_answer", lambda *a, **kw: {"hit": False})
    assert store.ask(question, ["team"], 604800, 0.60) == expected
    assert len(opened) == 1
    opened.clear()
    assert store.find_evidence(question, ["team"], 604800) == expected
    assert len(opened) == 1  # Public standalone calls still own their connection.


def test_fallback_exception_keeps_prior_validation_committed(team, monkeypatch):
    app, _, _, _, key = team
    store = app.state.store
    trail = Trail(id=uuid.uuid4().hex, namespace="team", question="Retention?", content="17 days.")
    store.push(trail, key.grant)
    original = store.connect
    opened = track_connections(monkeypatch, store)

    def answer(*args, _connection, **kwargs):
        _connection.execute(
            "INSERT INTO validations(trail_id,validator,result,checked_at) VALUES (?,?,?,?)",
            (trail.id, "answer-stage", "[]", 1),
        )
        return {"hit": True, "status": "stale"}

    def evidence(*args, _connection, **kwargs):
        assert _connection is opened[0] and not _connection.in_transaction
        _connection.execute(
            "INSERT INTO validations(trail_id,validator,result,checked_at) VALUES (?,?,?,?)",
            (trail.id, "failed-evidence-stage", "[]", 2),
        )
        raise RuntimeError("display failure")

    monkeypatch.setattr(store, "find_answer", answer)
    monkeypatch.setattr(store, "find_evidence", evidence)
    with pytest.raises(RuntimeError, match="display failure"):
        store.ask("Retention?", ["team"], 604800, 0.60)
    assert len(opened) == 1
    with original() as db:
        assert [r[0] for r in db.execute("SELECT validator FROM validations")] == ["answer-stage"]
