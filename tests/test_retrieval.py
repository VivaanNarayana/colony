import uuid

import pytest
import sqlite_vec
from conftest import auth

from colony.models import Trail
from colony.retrieval import index_version, passage
from colony.store import Store
from colony.validation import snapshot


def push(api, key, repo, question, content, **source_changes):
    source = snapshot(repo, "build.py").model_copy(update=source_changes)
    trail = Trail(
        id=uuid.uuid4().hex, namespace="team", question=question, content=content, sources=[source]
    )
    assert api.post("/trails", json=trail.model_dump(), headers=auth(key)).status_code == 200
    return trail.id


def ask(api, key, question):
    return api.post("/ask", json={"question": question}, headers=auth(key)).json()


def test_semantic_paraphrase_and_wrong_topic_rejection(team):
    _, api, repo, _, key = team
    migration = push(
        api,
        key,
        repo,
        "Revert a database migration",
        "Use alembic downgrade -1 to undo the latest database schema migration.",
    )
    push(
        api,
        key,
        repo,
        "Launch the development web application",
        "Start the HTTP listener with uvicorn app:app --port 8080.",
    )
    hit = ask(api, key, "How can I roll back the most recent schema update?")
    assert hit["id"] == migration and hit["status"] == "validated"
    assert hit["recommendation"] == "reuse_if_relevant"
    assert hit["sources"][0]["ref"] == "build.py"
    assert not ask(api, key, "How can I rotate a database password?")["hit"]
    assert not ask(api, key, "How do I prune the container image cache?")["hit"]


@pytest.mark.parametrize(
    "question,content,expected",
    [
        (
            "What is the backup encryption algorithm?",
            "Database backups are retained for thirty-seven days.",
            False,
        ),
        (
            "What is the worker retry delay?",
            "Worker audit logs rotate every week.",
            False,
        ),
        (
            "Which regions replicate ledger records?",
            "Ledger records are signed with RSA.",
            False,
        ),
        (
            "What is the backup encryption algorithm?",
            "Backup metadata is listed. A section boundary. Encryption is discussed elsewhere. "
            "Another section boundary. Algorithm catalogue.",
            False,
        ),
        (
            "What is the backup retention period?",
            "The backup retention period is thirty-seven days.",
            True,
        ),
    ],
)
def test_saved_descriptions_need_fact_support_even_with_strong_topic_scores(
    team, monkeypatch, question, content, expected
):
    import colony.store

    app, api, repo, _, key = team
    store = app.state.store
    push(api, key, repo, "Operations reference", content)
    vector = sqlite_vec.serialize_float32([1.0] + [0.0] * 383)
    with store.connect() as db:
        db.execute("UPDATE trail_vectors SET embedding=?", (vector,))
        db.execute("UPDATE trail_questions SET embedding=?", (vector,))
    ranked = []

    def strong_rank(query, passages):
        ranked.extend(passages)
        return [20.0] * len(passages)

    monkeypatch.setattr(colony.store, "rank", strong_rank)
    result = store.find_answer(question, ["team"], 604800, 0.60, query_vector=vector)
    assert result["hit"] == expected
    # A literal match needs only its answer score. A semantic fallback also
    # scores the saved question; uniformly high topic scores prove no answer.
    answer = passage("Operations reference", content)
    assert ranked == ([answer] if expected else [answer, "Operations reference"])
    if expected:
        assert result["content"] == content and result["status"] == "validated"


def test_stale_high_ranked_candidate_cannot_hide_valid_alternative(team, monkeypatch):
    import colony.store

    _, api, repo, _, key = team
    push(
        api,
        key,
        repo,
        "Configure build timeout",
        "Outdated timeout configuration: TIMEOUT = 30",
        contains="this snippet does not exist",
    )
    valid = push(
        api, key, repo, "Configure build timeout", "Current timeout configuration: TIMEOUT = 30"
    )
    monkeypatch.setattr(
        colony.store,
        "rank",
        lambda q, passages: [9.0 if "Outdated" in p else -10.0 for p in passages],
    )
    assert ask(api, key, "Configure build timeout")["id"] == valid
    (repo / "build.py").write_text("changed")
    assert ask(api, key, "Configure build timeout")["status"] == "stale"


def test_existing_vector_index_is_rebuilt_without_losing_trails(team):
    app, api, repo, admin, key = team
    trail = push(api, key, repo, "Configure build timeout", "Set TIMEOUT = 30")
    original = app.state.store
    with original.connect() as db:
        # Simulate a pre-semantic database with a legacy vector table and no version metadata.
        db.execute("DROP TABLE settings")
        db.execute("DELETE FROM trail_vectors")
    migrated = Store(original.directory, repo)
    result = migrated.ask("Configure build timeout", ["team"], 604800, 0.55)
    assert result["id"] == trail and result["content"] == "Set TIMEOUT = 30"
    with migrated.connect() as db:
        assert db.execute("SELECT count(*) FROM trail_vectors").fetchone()[0] == 1
        assert db.execute("SELECT value FROM settings").fetchone()[0] == index_version()


def test_stronger_positive_rerank_wins_over_higher_dense_question_match(team, monkeypatch):
    import colony.store

    _, api, repo, _, key = team
    push(api, key, repo, "Start the service", "Run the stdio bridge with worker mcp.")
    correct = push(
        api, key, repo, "Start the local service", "Start the HTTP listener with worker serve."
    )
    monkeypatch.setattr(
        colony.store, "rank", lambda q, passages: [9.0 if "HTTP" in p else 6.0 for p in passages]
    )
    assert ask(api, key, "Start the service")["id"] == correct
