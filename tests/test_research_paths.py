"""Miss navigation never becomes an answer, proof, or scope/freshness bypass."""

import copy
import json
import time
from types import SimpleNamespace

import pytest

import colony.mcp
from colony.models import Trail
from colony.validation import snapshot

QUESTION = "Start the replica journal spool."


def seed(store, repo, grant, *, scope="team", assertion=False):
    path = repo / "spool.md"
    path.write_text(
        "# Replica journal spool\n\nThe replica journal spool is a background service that manages replication journals.\n",
        encoding="utf-8",
    )
    source = snapshot(repo, "spool.md")
    if assertion:
        source = source.model_copy(update={"contains": "Required text that is absent"})
    trail = Trail(
        id="d" * 32,
        namespace=scope,
        question="What is audit retention?",
        content="Keep audits for five days.",
        sources=[source],
    )
    store.push(trail, grant)
    return path


def test_current_scoped_location_is_navigation_only_and_direct_lookup_still_misses(team):
    app, _, repo, _, key = team
    store = app.state.store
    seed(store, repo, key.grant)
    result = store.ask(QUESTION, ["team"], 604800, 0.7)
    assert result == {"hit": False, "reason": "no_relevant_trail", "research_paths": ["spool.md"]}
    assert store.find_evidence(QUESTION, ["team"], 604800) is None
    assert (
        not {"status", "content", "sources", "evidence", "checks", "current_repository_reads"}
        & result.keys()
    )


@pytest.mark.parametrize(
    "failure", ["changed", "missing", "old", "assertion", "private", "empty-scope"]
)
def test_unavailable_or_unauthorized_proofs_never_supply_a_navigation_path(team, failure):
    app, _, repo, _, key = team
    store = app.state.store
    path = seed(store, repo, key.grant, assertion=failure == "assertion")
    if failure == "changed":
        path.write_text("Changed service.", encoding="utf-8")
    if failure == "missing":
        path.unlink()
    if failure == "old":
        with store.connect() as db:
            db.execute("UPDATE trails SET created_at=?", (time.time() - 700000,))
    scopes = [] if failure == "empty-scope" else ["other"] if failure == "private" else ["team"]
    result = store.ask(QUESTION, scopes, 604800, 0.7)
    assert not result["hit"] and "research_paths" not in result


def test_all_attached_proofs_must_validate_before_any_path_hint(team):
    app, _, repo, _, key = team
    store = app.state.store
    seed(store, repo, key.grant)
    with store.connect() as db:
        db.execute(
            "INSERT INTO trail_sources VALUES (?,?,?,?,?,?)",
            ("d" * 32, "url", "https://example.invalid", "a" * 64, None, None),
        )
    result = store.ask(QUESTION, ["team"], 604800, 0.7)
    assert not result["hit"] and "research_paths" not in result


def test_mcp_hint_is_not_a_reusable_read_and_save_still_requires_new_fingerprints(
    monkeypatch, tmp_path
):
    packet = {"hit": False, "reason": "no_relevant_trail", "research_paths": ["src/example.py"]}
    monkeypatch.setattr(
        colony.mcp,
        "Client",
        lambda home: SimpleNamespace(ask=lambda *a, **k: copy.deepcopy(packet)),
    )
    result = json.loads(
        colony.mcp.create_mcp(tmp_path)
        ._tool_manager.get_tool("ask")
        .fn("Which replica options are required?")[0]
        .text
    )
    assert all(result[key] == value for key, value in packet.items())
    assert "not answers, reads or proofs" in result["research_guidance"]
    assert "actual new-read fingerprints" in result["research_guidance"]
    assert not {"sources", "current_repository_reads", "content", "status"} & result.keys()


def test_navigation_validation_has_a_fixed_three_trail_bound(team, monkeypatch):
    import colony.store

    app, _, repo, _, key = team
    store = app.state.store
    seed(store, repo, key.grant)
    for i in range(3):
        store.push(
            Trail(
                id=f"{i + 1:032x}",
                namespace="team",
                question="What is audit retention?",
                content="Keep audits for five days.",
                sources=[
                    snapshot(repo, "spool.md").model_copy(update={"contains": f"missing-{i}"})
                ],
            ),
            key.grant,
        )
    calls = []
    original = colony.store.validate

    def validate(repo, sources):
        calls.append(sources)
        return original(repo, sources)

    monkeypatch.setattr(colony.store, "validate", validate)
    result = store.ask(QUESTION, ["team"], 604800, 0.7)
    assert not result["hit"] and "research_paths" not in result
    assert len(calls) == 3


def test_validated_answer_path_remains_unchanged_and_needs_no_navigation(team):
    app, _, repo, _, key = team
    store = app.state.store
    seed(store, repo, key.grant)
    question = "What is the replica journal spool?"
    store.push(
        Trail(
            id="e" * 32,
            namespace="team",
            question=question,
            content="The replica journal spool manages replication journals.",
            sources=[snapshot(repo, "spool.md")],
        ),
        key.grant,
    )
    result = store.ask(question, ["team"], 604800, 0.7)
    assert result["status"] == "validated" and result["match_kind"] == "answer"
    assert "research_paths" not in result
