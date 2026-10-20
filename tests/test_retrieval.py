import uuid

from conftest import auth

from colony.models import Trail
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
        assert db.execute("SELECT value FROM settings").fetchone()[0].startswith("semantic-v3:")
