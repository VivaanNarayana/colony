import uuid

from conftest import auth

from colony.models import Trail
from colony.validation import snapshot


def test_reuses_only_previously_attached_current_source_context(team):
    app, api, repo, _, key = team
    (repo / "guide.md").write_text(
        "# Application operations\n\n"
        "Start the development web server with uvicorn app:app --port 8080.\n\n"
        "# PostgreSQL backups\n\n"
        "Create a compressed PostgreSQL database backup with pg_dump -Fc appdb > backup.dump.\n"
        "Store the backup outside the application host.\n"
    )
    question = "How do I create a compressed PostgreSQL database backup?"
    # Startup and misses never crawl the repository or manufacture memory hits.
    assert not api.post("/ask", json={"question": question}, headers=auth(key)).json()["hit"]
    trail = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question="Start the development web server",
        content="Use uvicorn app:app --port 8080.",
        sources=[snapshot(repo, "guide.md")],
    )
    api.post("/trails", json=trail.model_dump(), headers=auth(key)).raise_for_status()
    result = api.post("/ask", json={"question": question}, headers=auth(key)).json()
    assert result["hit"] and result["match_kind"] == "evidence"
    assert result["status"] == "validated" and result["id"] == trail.id
    assert "pg_dump -Fc" in str(result["evidence"])
    assert "no matching saved answer" in result["provenance"]
    # Scope filtering applies to the evidence index as well as the answer index.
    assert not app.state.store.ask(question, ["another-team"], 604800, 0.62)["hit"]
    assert not app.state.store.ask(question, [], 604800, 0.62)["hit"]
    # A previously indexed source does not survive a content change as validated.
    (repo / "guide.md").write_text("The backup procedure changed.")
    changed = api.post("/ask", json={"question": question}, headers=auth(key)).json()
    assert changed.get("status") != "validated"


def test_failed_source_assertions_and_age_block_evidence_reuse(team):
    app, api, repo, _, key = team
    (repo / "guide.md").write_text(
        "Start the web server using uvicorn.\n"
        "Create a compressed PostgreSQL backup using pg_dump -Fc appdb.\n"
    )
    question = "How do I create a compressed PostgreSQL database backup?"
    trail = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question="Start the development web server",
        content="Use uvicorn.",
        sources=[snapshot(repo, "guide.md").model_copy(update={"contains": "absent"})],
    )
    api.post("/trails", json=trail.model_dump(), headers=auth(key)).raise_for_status()
    result = api.post("/ask", json={"question": question}, headers=auth(key)).json()
    assert result.get("status") != "validated"
    with app.state.store.connect() as db:
        db.execute("UPDATE trail_sources SET contains=NULL")
        db.execute("UPDATE trails SET created_at=0")
    result = api.post("/ask", json={"question": question}, headers=auth(key)).json()
    assert result.get("status") != "validated"
