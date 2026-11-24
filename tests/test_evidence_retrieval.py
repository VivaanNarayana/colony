import os
import uuid

import pytest
from conftest import auth

from colony.compact import PROFILE
from colony.context_budget import count_response
from colony.models import ContextBudget, Trail
from colony.store import Store
from colony.validation import snapshot


@pytest.mark.parametrize(("by_file", "complementary"), [
    ({"index": {"a", "b", "c"}, "copy": {"a", "b", "c"}, "ab": {"a", "b"}, "a": {"a"}}, False),
    ({"index": {"a", "b", "c"}, "a": {"a"}, "b": {"b"}}, False),
    ({"index": {"a", "b", "c"}, "a": {"a"}, "b": {"b"}, "c": {"c"}}, True),
])
def test_keyword_index_requires_independent_evidence_in_its_other_files(by_file, complementary):
    assert Store._complementary_terms(by_file) is complementary


def test_stale_ranked_passage_allows_current_source_set_fallback(team, monkeypatch):
    from colony import store

    app, api, repo, _, key = team
    question = "Describe cache expiration and the eviction limit."
    learned = "What controls cache expiration and the eviction limit?"
    stale = "Cache expiration and eviction limit were previously defined together."
    for path, text in (("old.md", stale), ("clock.py", "CACHE_EXPIRATION = 900"),
                       ("capacity.py", "EVICTION_LIMIT = 7")):
        (repo / path).write_text(text)
    for query, paths in ((stale, ["old.md"]), (learned, ["clock.py", "capacity.py"])):
        trail = Trail(id=uuid.uuid4().hex, namespace="team", question=query, content="Old answer",
                      sources=[snapshot(repo, p) for p in paths])
        api.post("/trails", json=trail.model_dump(), headers=auth(key)).raise_for_status()

    def rerank(query, passages):
        if stale in passages:
            (repo / "old.md").write_text("Changed after initial source validation")
        return [1.0 if passage in (stale, learned) else -9.0 for passage in passages]

    monkeypatch.setattr(store, "rank", rerank)
    result = app.state.store.find_evidence(question, ["team"], 604800, context_budget=ContextBudget(),
                                           source_set_threshold=0.6)
    assert result["id"] == trail.id and result["status"] == "validated"
    assert set(result["complete_files"]) == {"clock.py", "capacity.py"}
    assert (repo / "old.md").read_text() == "Changed after initial source validation"


def test_compound_lookup_reuses_joint_sources_only_while_current(team, monkeypatch):
    from colony import store

    app, api, repo, _, key = team
    question = "Describe cache expiration and the eviction limit."
    learned = "What controls cache expiration and the eviction limit?"
    (repo / "clock.py").write_text("CACHE_EXPIRATION = 900\n")
    (repo / "capacity.py").write_text("EVICTION_LIMIT = 7\n")
    trail = Trail(id=uuid.uuid4().hex, namespace="team", question=learned,
                  content="A historical summary without the resolved values.",
                  sources=[snapshot(repo, p) for p in ("clock.py", "capacity.py")])
    api.post("/trails", json=trail.model_dump(), headers=auth(key)).raise_for_status()
    # Neither code passage ranks on its own. The learned question locates the
    # source set, but its answer text must never be returned as current evidence.
    monkeypatch.setattr(store, "rank", lambda q, passages: [-1.0 if p == learned else -9.0 for p in passages])
    database = app.state.store
    def find(namespaces, threshold):
        return database.find_evidence(question, namespaces, 604800, context_budget=ContextBudget(),
                                      source_set_threshold=threshold)

    result = find(["team"], 0.6)
    assert result["id"] == trail.id and result["match_kind"] == "evidence"
    assert result["status"] == "validated" and set(result["complete_files"]) == {"clock.py", "capacity.py"}
    assert "900" in result["content"] and "7" in result["content"] and trail.content not in result["content"]
    assert find(["private"], 0.6) is None
    assert find(["team"], 1.0) is None
    assert find(["team"], None) is None
    (repo / "clock.py").write_text("CACHE_EXPIRATION = 300\n")
    assert find(["team"], 0.6) is None


def test_compound_lookup_ranks_saved_questions_before_limiting_shared_source_sets(team, monkeypatch):
    from colony import store

    app, api, repo, _, key = team
    question = "Describe cache expiration and the eviction limit."
    learned = "What controls cache expiration and the eviction limit?"
    (repo / "clock.py").write_text("CACHE_EXPIRATION = 900\n")
    (repo / "capacity.py").write_text("EVICTION_LIMIT = 7\n")
    sources = [snapshot(repo, p) for p in ("clock.py", "capacity.py")]
    target = Trail(id=uuid.uuid4().hex, namespace="team", question=learned,
                   content="Cache behavior.", sources=sources)
    api.post("/trails", json=target.model_dump(), headers=auth(key)).raise_for_status()
    for i in range(store.CANDIDATES + 1):
        newer = target.model_copy(update={"id": uuid.uuid4().hex,
                                          "question": f"Who owns cache expiration and eviction policy {i}?"})
        api.post("/trails", json=newer.model_dump(), headers=auth(key)).raise_for_status()

    def rank(query, passages):
        assert len(passages) <= store.CANDIDATES
        return [-1.0 if text == learned else -9.0 for text in passages]

    monkeypatch.setattr(store, "rank", rank)
    result = app.state.store.find_evidence(question, ["team"], 604800, context_budget=ContextBudget(),
                                           source_set_threshold=0.6)
    assert result and result["id"] == target.id and result["match_kind"] == "evidence"
    assert set(result["complete_files"]) == {"clock.py", "capacity.py"}


@pytest.mark.parametrize(("release", "extra_files"), [
    pytest.param(None, 1, id="stale"),
    pytest.param("x" * 2801, 1, id="oversized"),
    pytest.param("Notes.", 7, id="too_many_files"),
    pytest.param("", 1, id="empty"),
])
def test_undeliverable_dependencies_cannot_exhaust_compound_question_reranking(
    team, monkeypatch, release, extra_files
):
    from colony import store

    app, api, repo, _, key = team
    question = "Describe cache expiration and the eviction limit."
    learned = "What controls cache expiration and the eviction limit?"
    (repo / "clock.py").write_text("CACHE_EXPIRATION = 900\n")
    (repo / "capacity.py").write_text("EVICTION_LIMIT = 7\n")
    shared = [snapshot(repo, p) for p in ("clock.py", "capacity.py")]
    original = Trail(id=uuid.uuid4().hex, namespace="team", question=learned,
                     content="Cache behavior.", sources=shared)
    api.post("/trails", json=original.model_dump(), headers=auth(key)).raise_for_status()
    for i in range(store.CANDIDATES):
        extra = []
        for n in range(extra_files):
            path = f"release{n}.txt"
            (repo / path).write_text(f"Release {i}." if release is None else release)
            extra.append(snapshot(repo, path))
        newer = original.model_copy(update={"id": uuid.uuid4().hex,
                                            "sources": [*shared, *extra]})
        api.post("/trails", json=newer.model_dump(), headers=auth(key)).raise_for_status()
    if release is None:
        (repo / "release0.txt").write_text("Next release.")

    ranked_questions = []

    def rank(query, passages):
        ranked_questions.extend(text for text in passages if text == learned)
        return [-1.0 if text == learned else -9.0 for text in passages]

    monkeypatch.setattr(store, "rank", rank)
    result = app.state.store.find_evidence(question, ["team"], 604800, context_budget=ContextBudget(),
                                           source_set_threshold=0.6)
    assert result and result["id"] == original.id and result["status"] == "validated"
    assert set(result["complete_files"]) == {"clock.py", "capacity.py"}
    assert ranked_questions == [learned]


@pytest.mark.parametrize(("newer_count", "encoding", "limit"), [
    (1, "utf8_bytes", 2048), (12, "utf8_bytes", 2048), (12, "o200k_base", 512),
])
def test_compound_ask_selects_highest_ranked_complete_bundle_that_fits(
    team, monkeypatch, newer_count, encoding, limit
):
    from colony import store

    app, api, repo, _, key = team
    if encoding == "o200k_base":
        pytest.importorskip("tiktoken")
        tokenizer = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
        if not tokenizer:
            pytest.skip("Verified local tokenizer fixture unavailable")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", tokenizer)
    question = "Describe cache expiration and the eviction limit."
    learned = "What controls cache expiration and the eviction limit?"
    (repo / "clock.py").write_text("CACHE_EXPIRATION = 900\n")
    (repo / "capacity.py").write_text("EVICTION_LIMIT = 7\n")
    (repo / "release.txt").write_text("Release notes.\n" * 115)
    shared = [snapshot(repo, p) for p in ("clock.py", "capacity.py")]
    original = Trail(id=uuid.uuid4().hex, namespace="team", question=learned,
                     content="Historical interpretation", sources=shared)
    api.post("/trails", json=original.model_dump(), headers=auth(key)).raise_for_status()
    for _ in range(newer_count):
        newest = original.model_copy(update={"id": uuid.uuid4().hex,
                                             "sources": [*shared, snapshot(repo, "release.txt")]})
        api.post("/trails", json=newest.model_dump(), headers=auth(key)).raise_for_status()

    def rank(query, passages):
        if learned in passages:
            assert passages == [learned]  # All current publications share one question logit.
        return [-1.0 if text == learned else -9.0 for text in passages]

    monkeypatch.setattr(store, "rank", rank)

    def ask(limit, encoding):
        budget = ContextBudget(max_tokens=limit, encoding=encoding)
        # The saved prose must fail the answer gate; the learned question still
        # locates its complementary files above the source-set similarity cutoff.
        result = app.state.store.ask(question, ["team"], 604800, 0.81,
                                     context_budget=budget, response_profile=PROFILE)
        assert result["context_budget"]["used"] == count_response(result, budget) <= limit
        return result

    wide = ask(8192, encoding)
    assert wide["id"] == newest.id and wide["match_kind"] == "evidence"
    assert wide["evidence_scope"] == "cited_files"
    tight = ask(limit, encoding)
    assert tight.get("id") == original.id and tight["match_kind"] == "evidence"
    assert tight["evidence_scope"] == "cited_files" and "content" not in tight
    assert {r["path"]: r["content"] for r in tight["current_repository_reads"]} == {
        "clock.py": "CACHE_EXPIRATION = 900", "capacity.py": "EVICTION_LIMIT = 7"}
    miss = ask(256, "utf8_bytes")
    assert not miss["hit"] and miss["reason"] == "budget_limited"
    with app.state.store.connect() as db:
        hits = db.execute("SELECT trail_id FROM validations WHERE validator='repo-evidence-v1'").fetchall()
    assert sorted(r[0] for r in hits) == sorted([newest.id, original.id])


def test_service_overview_is_not_a_procedure_but_remains_descriptive_evidence(team):
    _, api, repo, _, key = team
    (repo / "guide.md").write_text(
        "# Worker\n\nThe local processing worker is a background service that executes queued jobs.\n"
    )
    trail = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question="Audit retention period",
        content="Keep audit records for fourteen days.",
        sources=[snapshot(repo, "guide.md")],
    )
    api.post("/trails", json=trail.model_dump(), headers=auth(key)).raise_for_status()
    procedure = api.post(
        "/ask", json={"question": "Start the local processing worker."}, headers=auth(key)
    ).json()
    assert not procedure["hit"]
    description = api.post(
        "/ask", json={"question": "What is the local processing worker?"}, headers=auth(key)
    ).json()
    assert description["status"] == "validated" and description["match_kind"] == "evidence"
    assert "executes queued jobs" in description["content"]


def test_saved_bridge_answer_does_not_displace_http_backend_evidence(team):
    _, api, repo, _, key = team
    (repo / "guide.md").write_text(
        "# Beacon\n\n## Agent bridge\n\nRun the stdio bridge with `beacon mcp`.\n\n"
        "## HTTP backend\n\nStart the local HTTP server with `beacon serve --config api.toml`.\n"
    )
    trail = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question="Connect an agent through the stdio bridge",
        content="Run the Beacon stdio server with `beacon mcp` to connect an agent.",
        sources=[snapshot(repo, "guide.md")],
    )
    api.post("/trails", json=trail.model_dump(), headers=auth(key)).raise_for_status()
    result = api.post(
        "/ask", json={"question": "Start the local HTTP server."}, headers=auth(key)
    ).json()
    assert result["status"] == "validated" and result["match_kind"] == "evidence"
    assert "beacon serve --config api.toml" in result["content"]


def test_short_command_with_semantic_support_does_not_need_literal_query_words(team, monkeypatch):
    import colony.store

    _, api, repo, _, key = team
    (repo / "guide.md").write_text(
        "# Project\n\n## Verification\n\n```sh\npython -m mypy src/\n```\n"
    )
    trail = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question="Formatting configuration",
        content="Use Black.",
        sources=[snapshot(repo, "guide.md")],
    )
    api.post("/trails", json=trail.model_dump(), headers=auth(key)).raise_for_status()
    monkeypatch.setattr(
        colony.store, "rank", lambda q, passages: [-1.0 if "mypy" in p else -10.0 for p in passages]
    )
    result = api.post(
        "/ask", json={"question": "Which command runs static typing checks?"}, headers=auth(key)
    ).json()
    assert result["status"] == "validated" and result["match_kind"] == "evidence"
    assert "python -m mypy src/" in result["content"]


def test_question_example_does_not_hide_deployment_in_a_different_section(team):
    _, api, repo, _, key = team
    (repo / "guide.md").write_text(
        "# Service\n\n## Command logging\n\n"
        '```sh\nlogger run --question "How do I deploy the service?" -- echo placeholder\n```\n\n'
        "## Deployment\n\nPublish a new production release using this command:\n\n"
        "```sh\nshipctl deploy --target production\n```\n"
    )
    trail = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question="Record command output",
        content="Use the logger wrapper.",
        sources=[snapshot(repo, "guide.md")],
    )
    api.post("/trails", json=trail.model_dump(), headers=auth(key)).raise_for_status()
    result = api.post(
        "/ask", json={"question": "How do I deploy the service?"}, headers=auth(key)
    ).json()
    assert result["status"] == "validated" and result["match_kind"] == "evidence"
    assert "shipctl deploy --target production" in result["content"]


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
    assert "read from the current checkout" in result["provenance"]
    assert "pg_dump -Fc" in result["content"]
    # Scope filtering applies to the evidence index as well as the answer index.
    assert not app.state.store.ask(question, ["another-team"], 604800, 0.62)["hit"]
    assert not app.state.store.ask(question, [], 604800, 0.62)["hit"]
    assert not app.state.store.ask(
        question, ["another-team"], 604800, 0.62, include_evidence_files=True
    )["hit"]
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


def test_grouped_source_passage_keeps_alternative_valid_trail_origins(team):
    _, api, repo, _, key = team
    (repo / "guide.md").write_text(
        "# Operations\n\nStart the web application using uvicorn.\n\n"
        "# Backup\n\nCreate a PostgreSQL backup using pg_dump -Fc appdb.\n"
    )
    good = None
    for invalid in [False, True, True]:
        source = snapshot(repo, "guide.md")
        if invalid:
            source = source.model_copy(update={"contains": "absent"})
        trail = Trail(
            id=uuid.uuid4().hex,
            namespace="team",
            question="Launch web application",
            content="Start uvicorn.",
            sources=[source],
        )
        api.post("/trails", json=trail.model_dump(), headers=auth(key)).raise_for_status()
        if not invalid:
            good = trail.id
    result = api.post(
        "/ask", json={"question": "How do I make a PostgreSQL backup?"}, headers=auth(key)
    ).json()
    assert result["status"] == "validated" and result["match_kind"] == "evidence"
    assert result["id"] == good and "pg_dump" in result["content"]
