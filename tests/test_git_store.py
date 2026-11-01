import hashlib
import json
import time
import uuid

import pytest
import sqlite_vec

import colony.context
import colony.store
from colony.memory import MemoryConflict
from colony.models import GitContext, Grant, MemoryChange, MemoryCorrection, Scope, Trail
from colony.store import Store
from colony.validation import snapshot


def context(*, repository_id="project", branch="main", commit="a" * 40, dirty=False, state="git"):
    if state != "git":
        return GitContext(repository_id=repository_id, state=state)
    return GitContext(
        repository_id=repository_id,
        state=state,
        branch=branch,
        commit=commit,
        dirty=dirty,
        detached=branch is None,
    )


@pytest.fixture
def setup_store(tmp_path, monkeypatch):
    vector = sqlite_vec.serialize_float32([1.0] + [0.0] * 383)
    monkeypatch.setattr(colony.store, "models", lambda: None)
    monkeypatch.setattr(colony.store, "index_version", lambda: "git-store-fixture-v1")
    monkeypatch.setattr(colony.store, "embed", lambda *a, **k: vector)
    monkeypatch.setattr(colony.store, "embed_passages", lambda texts: [vector] * len(texts))
    monkeypatch.setattr(colony.store, "rank", lambda q, texts: [5.0] * len(texts))
    monkeypatch.setattr(colony.context, "rank", lambda q, texts: [5.0] * len(texts))
    captured = []

    def capture(repo, repository_id):
        captured.append((repo, repository_id))
        return context(repository_id=repository_id, branch="server-view", commit="f" * 40)

    monkeypatch.setattr(colony.store, "capture_git_context", capture)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "guide.md").write_text("The current build timeout configuration is 30 seconds.\n")
    grant = Grant(
        member_id="author",
        name="Author",
        server="http://localhost:7331",
        expires_at=int(time.time()) + 3600,
        scopes=[Scope(namespace="team", pull=True, push=True)],
    )

    def create(*, repository_id="project"):
        return Store(tmp_path / "data", repo, repository_id=repository_id)

    return create, repo, grant, captured


def finding(repo, origin=None, **updates):
    values = dict(
        id=uuid.uuid4().hex,
        namespace="team",
        question="Build timeout configuration",
        content="SAVED-ANSWER-SENTINEL: timeout is 30 seconds.",
        sources=[snapshot(repo, "guide.md")],
        git_context=origin,
    )
    values.update(updates)
    return Trail(**values)


def ask(store, origin=None):
    return store.ask("Build timeout configuration", ["team"], 604800, 0.6, git_context=origin)


def test_producer_origin_encrypted_and_server_observation_independent(setup_store):
    create, repo, grant, captured = setup_store
    store = create()
    origin = context(branch="private-branch-sentinel")
    trail = finding(repo, origin)
    store.push(trail, grant)
    assert len(captured) == 1
    inspection = store.inspect_memory(trail.id, "team", grant)
    assert inspection["git"]["origin"] == origin.model_dump()
    assert inspection["git"]["server_observed"]["branch"] == "server-view"
    with store.connect() as db:
        encrypted = db.execute("SELECT provenance FROM trail_git_provenance_v1").fetchone()[0]
        assert b"private-branch-sentinel" not in encrypted and b"server-view" not in encrypted
        assert len(db.execute("PRAGMA table_info(trails)").fetchall()) == 10
    result = ask(store, origin)
    assert result["match_kind"] == "answer" and result["git"]["compatibility"] == "matched"
    assert result["git"]["request"] == origin.model_dump()
    assert "git" not in ask(store)
    assert len(captured) == 1  # Queries do not collect or restamp server Git state.


@pytest.mark.parametrize(
    "origin,requested,compatibility",
    [
        (context(branch="feature"), context(), "branch_mismatch"),
        (context(commit="b" * 40), context(), "commit_mismatch"),
        (context(dirty=True), context(), "dirty"),
        (context(), context(dirty=True), "dirty"),
        (context(state="unavailable"), context(), "unavailable"),
        (None, context(), "legacy"),
    ],
)
def test_context_mismatches_expose_only_current_file_evidence(
    setup_store, origin, requested, compatibility
):
    create, repo, grant, _ = setup_store
    store = create()
    trail = finding(repo, origin)
    store.push(trail, grant)
    answer = store.find_answer(trail.question, ["team"], 604800, 0.6, git_context=requested)
    assert not answer["hit"] and answer["git"]["compatibility"] == compatibility
    with store.connect() as db:
        assert db.execute("SELECT status FROM trails").fetchone()[0] == "unverified"
    result = ask(store, requested)
    assert result["match_kind"] == "evidence" and result["status"] == "validated"
    assert result["git"]["compatibility"] == compatibility
    assert "SAVED-ANSWER-SENTINEL" not in json.dumps(result)
    assert "30 seconds" in result["content"]
    assert store.inspect_memory(trail.id, "team", grant)["memory"]["state"] == "active"


def test_same_commit_different_branches_do_not_share_full_answers(setup_store):
    create, repo, grant, _ = setup_store
    store = create()
    main = finding(repo, context(), content="MAIN-ANSWER")
    feature = finding(repo, context(branch="feature"), content="FEATURE-ANSWER")
    store.push(main, grant)
    store.push(feature, grant)
    assert ask(store, context())["content"] == "MAIN-ANSWER"
    assert ask(store, context(branch="feature"))["content"] == "FEATURE-ANSWER"
    assert main.sources == feature.sources


def test_compatible_candidate_is_selected_before_existing_candidate_cap(setup_store):
    create, repo, grant, _ = setup_store
    store = create()
    compatible = finding(repo, context(), content="COMPATIBLE-ANSWER")
    store.push(compatible, grant)
    for _ in range(colony.store.CANDIDATES + 2):
        store.push(finding(repo, context(branch="other")), grant)
    result = ask(store, context())
    assert result["id"] == compatible.id and result["match_kind"] == "answer"


def test_shared_source_evidence_excludes_foreign_repository_provenance(setup_store):
    create, repo, grant, _ = setup_store
    store = create()
    permitted = finding(repo, context(branch="old"))
    foreign = finding(repo, context(repository_id="different"))
    store.push(permitted, grant)
    store.push(foreign, grant)
    result = store.find_evidence(permitted.question, ["team"], 604800, git_context=context())
    assert result["id"] == permitted.id
    assert result["git"]["compatibility"] == "branch_mismatch"
    assert "SAVED-ANSWER-SENTINEL" not in json.dumps(result)
    store.change_memory(
        MemoryChange(
            trail_id=permitted.id,
            namespace="team",
            operation_id=uuid.uuid4().hex,
            expected_revision=0,
            action="retract",
        ),
        grant,
    )
    assert not ask(store, context())["hit"]
    assert store.find_evidence(permitted.question, ["team"], 604800, git_context=context()) is None


@pytest.mark.parametrize(
    "binding,requested", [(None, context()), ("project", context(repository_id="other"))]
)
def test_aware_requests_require_matching_configured_repository_before_retrieval(
    setup_store, monkeypatch, binding, requested
):
    create, repo, grant, _ = setup_store
    store = create(repository_id=binding)
    store.push(finding(repo, context()), grant)
    monkeypatch.setattr(colony.store, "embed", lambda *a, **k: pytest.fail("unbound retrieval"))
    assert not ask(store, requested)["hit"]
    assert (
        store.find_evidence("Build timeout configuration", ["team"], 604800, git_context=requested)
        is None
    )


def test_non_git_contexts_are_explicitly_not_applicable(setup_store):
    create, repo, grant, _ = setup_store
    store = create()
    non_git = context(state="non_git")
    store.push(finding(repo, non_git), grant)
    result = ask(store, non_git)
    assert result["match_kind"] == "answer" and result["git"]["compatibility"] == "not_applicable"


def test_existing_store_migration_does_not_invent_or_rewrite_old_origins(setup_store):
    create, repo, grant, captured = setup_store
    store = create(repository_id=None)
    old = finding(repo)
    store.push(old, grant)
    with store.connect() as db:
        payload_hash = db.execute("SELECT payload_hash FROM trails").fetchone()[0]
        db.execute("DROP TABLE trail_git_provenance_v1")
    restarted = create()
    assert not captured
    assert "git" not in restarted.inspect_memory(old.id, "team", grant)
    with restarted.connect() as db:
        assert db.execute("SELECT count(*) FROM trail_git_provenance_v1").fetchone()[0] == 0
        assert db.execute("SELECT payload_hash FROM trails").fetchone()[0] == payload_hash
    assert ask(restarted)["match_kind"] == "answer"
    assert ask(restarted, context())["match_kind"] == "evidence"
    assert create().inspect_memory(old.id, "team", grant).get("git") is None


def test_absent_context_preserves_legacy_payload_hash_and_duplicate_retry(setup_store):
    create, repo, grant, captured = setup_store
    store = create()
    trail = finding(repo)
    legacy = {key: value for key, value in trail.model_dump().items() if key != "git_context"}
    expected = json.dumps(legacy, separators=(",", ":"), ensure_ascii=False).encode()
    assert trail.model_dump_json().encode() == expected
    store.push(trail, grant)
    explicit_none = Trail.model_validate({**legacy, "git_context": None})
    assert store.push(explicit_none, grant)["duplicate"]
    assert len(captured) == 1
    with store.connect() as db:
        assert (
            db.execute("SELECT payload_hash FROM trails").fetchone()[0]
            == hashlib.sha256(expected).hexdigest()
        )
    with pytest.raises(ValueError, match="different payload"):
        store.push(Trail.model_validate({**legacy, "git_context": context().model_dump()}), grant)
    assert len(captured) == 1


def test_correction_records_new_origin_replay_and_restore_preserve_it(setup_store):
    create, repo, grant, captured = setup_store
    store = create()
    original = finding(repo, context())
    store.push(original, grant)
    corrected_context = context(branch="corrected", commit="c" * 40)
    request = MemoryCorrection(
        trail_id=original.id,
        namespace="team",
        operation_id=uuid.uuid4().hex,
        expected_revision=0,
        question=original.question,
        content="Corrected answer",
        sources=original.sources,
        git_context=corrected_context,
    )
    result = store.correct_memory(request, grant)
    corrected_id = result["replacement_id"]
    assert len(captured) == 2
    assert (
        store.inspect_memory(original.id, "team", grant)["git"]["origin"] == context().model_dump()
    )
    expected = store.inspect_memory(corrected_id, "team", grant)["git"]
    assert expected["origin"] == corrected_context.model_dump()
    assert store.correct_memory(request, grant)["replayed"]
    with pytest.raises(MemoryConflict):
        store.correct_memory(request.model_copy(update={"git_context": context()}), grant)
    for revision, action in enumerate(("retract", "restore")):
        store.change_memory(
            MemoryChange(
                trail_id=corrected_id,
                namespace="team",
                operation_id=uuid.uuid4().hex,
                expected_revision=revision,
                action=action,
            ),
            grant,
        )
    assert len(captured) == 2
    assert store.inspect_memory(corrected_id, "team", grant)["git"] == expected
    assert create().inspect_memory(corrected_id, "team", grant)["git"] == expected


def test_corrupt_or_unknown_provenance_withholds_answer_without_inventing_origin(setup_store):
    create, repo, grant, _ = setup_store
    store = create()
    trail = finding(repo, context())
    store.push(trail, grant)
    with store.connect() as db:
        db.execute(
            "UPDATE trail_git_provenance_v1 SET provenance=?",
            (store.cipher.encrypt(b'{"version":"unknown"}'),),
        )
    result = ask(store, context())
    assert result["match_kind"] == "evidence"
    assert result["git"]["origin"] is None and result["git"]["compatibility"] == "unavailable"
    assert "SAVED-ANSWER-SENTINEL" not in json.dumps(result)


def test_changed_file_prevents_evidence_fallback_and_does_not_relabel_git(setup_store):
    create, repo, grant, _ = setup_store
    store = create()
    trail = finding(repo, context(branch="other"))
    store.push(trail, grant)
    (repo / "guide.md").write_text("The evidence changed.\n")
    result = ask(store, context())
    assert not result["hit"] and "SAVED-ANSWER-SENTINEL" not in json.dumps(result)
    assert (
        store.inspect_memory(trail.id, "team", grant)["git"]["origin"]
        == trail.git_context.model_dump()
    )
