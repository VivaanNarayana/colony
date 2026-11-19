"""Real storage/HTTP/MCP countercontrols for one bounded evidence-only fallback."""

import asyncio
import copy
import json
import subprocess
import time

import pytest
import test_compact_delivery as controls

from colony import context, mcp, store
from colony.client import Client
from colony.compact import PROFILE
from colony.context_budget import count_response, fit_response, serialize_response
from colony.models import ContextBudget, Trail
from colony.validation import snapshot


@pytest.fixture
def transport(tmp_path, monkeypatch):
    value = controls.transport.__wrapped__(tmp_path, monkeypatch)
    # Vary relevance instead of making every source passage a positive match.
    def rank(question, passages):
        return [8.0 if "NEEDLE =" in text or "HAYSTACK =" in text else -8.0
                for text in passages]

    monkeypatch.setattr(store, "rank", rank)
    monkeypatch.setattr(context, "rank", rank)
    return value


def publish_bundle(client, repo, *, two_cores=False, git=False):
    texts = {f"part{i}.py": f"OTHER_{i} = {i}" for i in range(4)}
    texts["part3.py"] = "NEEDLE = 77"
    if two_cores:
        texts["part2.py"] = "HAYSTACK = 88"
    texts["part0.py"] += "\n\n#" + "x" * (2800 - sum(map(len, texts.values())) - 3)
    assert sum(map(len, texts.values())) == 2800
    for name, text in texts.items():
        (repo / name).write_bytes(text.encode())
    trail = Trail(
        id="a" * 32, namespace="team",
        question="What are NEEDLE and HAYSTACK?" if two_cores else "What is NEEDLE?",
        content="SAVED_FINDING_MUST_NOT_SURVIVE_FALLBACK", tainted=True,
        sources=[snapshot(repo, name) for name in texts],
    )
    if git:
        for args in [["init", "-b", "fixture"], ["config", "core.autocrlf", "false"],
                     ["add", "--", "."], ["-c", "user.name=Fixture", "-c",
                     "user.email=fixture@example.invalid", "commit", "-m", "fixture"]]:
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    assert client.push(trail, **({"repo": repo, "repository_id": "project"} if git else {}))["stored"]
    return trail


def original_packet(database, trail, budget, **kwargs):
    answer = database.find_answer(trail.question, ["team"], 604800, 0.60,
                                  context_budget=budget, compact_bundle=True, **kwargs)
    return fit_response({**answer, "response_profile": PROFILE}, budget, question=trail.question)


def ask(client, trail, budget, **kwargs):
    value = client.ask(trail.question, "team", context_budget=budget,
                       response_profile=PROFILE, **kwargs)
    assert value["context_budget"]["used"] == count_response(value, budget) <= budget.max_tokens
    return value


def test_automatic_bundle_budget_miss_gets_one_same_finding_evidence_fallback(transport, monkeypatch):
    client, repo, database, responses = transport
    trail = publish_bundle(client, repo)
    budget = ContextBudget(max_tokens=2048)
    before = original_packet(database, trail, budget)
    assert not before["hit"] and before["reason"] == "budget_limited"
    calls = []
    select = database.find_evidence

    def counted(*args, **kwargs):
        calls.append(kwargs)
        return select(*args, **kwargs)

    monkeypatch.setattr(database, "find_evidence", counted)
    value = ask(client, trail, budget)
    assert len(calls) == 1 and calls[0]["restricted_trail_id"] == trail.id
    assert calls[0]["required_source_identities"] == frozenset(
        (source.ref, source.content_hash) for source in trail.sources)
    assert value["hit"] and value["status"] == "validated" and value["id"] == trail.id
    assert value["match_kind"] == "evidence" and "content" not in value
    assert value["context_budget"]["limited"] is True
    assert value["tainted"] is True and value["memory"]["state"] == "active"
    assert value["sources"] == [{"ref": s.ref, "content_hash": s.content_hash} for s in trail.sources]
    assert [read["path"] for read in value["current_repository_reads"]] == ["part3.py"]
    assert value["current_repository_reads"][0]["content"] == "NEEDLE = 77"
    assert "SAVED_FINDING" not in serialize_response(value)
    assert value == responses[-1]


def test_all_selected_fallback_cores_fit_or_result_remains_a_miss(transport):
    client, repo, database, _ = transport
    trail = publish_bundle(client, repo, two_cores=True)
    result = ask(client, trail, ContextBudget(max_tokens=2048))
    assert {r["path"] for r in result["current_repository_reads"]} == {"part2.py", "part3.py"}
    too_small = ContextBudget(max_tokens=result["context_budget"]["used"] - 1)
    assert not original_packet(database, trail, too_small)["hit"]
    limited = ask(client, trail, too_small)
    assert not limited["hit"] and limited["reason"] == "budget_limited"
    assert "current_repository_reads" not in limited


@pytest.mark.parametrize("mutation", ["stale", "retracted", "aged", "namespace", "sources"])
def test_fallback_rechecks_original_source_scope_and_lifecycle(transport, monkeypatch, mutation):
    client, repo, database, _ = transport
    trail = publish_bundle(client, repo)
    select = database.find_evidence

    def changed(*args, **kwargs):
        if mutation == "stale":
            (repo / "part3.py").write_bytes(b"NEEDLE = 999\n")
        else:
            with database.connect() as db:
                if mutation == "retracted":
                    db.execute("UPDATE memory_state_v1 SET state='retracted' WHERE trail_id=?", (trail.id,))
                elif mutation == "aged":
                    db.execute("UPDATE trails SET created_at=? WHERE id=?", (time.time() - 604801, trail.id))
                elif mutation == "namespace":
                    db.execute("UPDATE trails SET namespace='private' WHERE id=?", (trail.id,))
                else:
                    db.execute("DELETE FROM trail_sources WHERE trail_id=? AND ref='part0.py'", (trail.id,))
        return select(*args, **kwargs)

    monkeypatch.setattr(database, "find_evidence", changed)
    value = ask(client, trail, ContextBudget(max_tokens=2048))
    assert not value["hit"] and value["reason"] == "budget_limited"
    assert "content" not in value and "current_repository_reads" not in value


def test_fallback_cannot_switch_to_an_unrelated_higher_ranked_finding(transport, monkeypatch):
    client, repo, database, _ = transport
    trail = publish_bundle(client, repo)
    (repo / "unrelated.py").write_bytes(b"NEEDLE = 12345\n")
    other = Trail(id="b" * 32, namespace="team", question="Other question", content="Other answer",
                  sources=[snapshot(repo, "unrelated.py")])
    assert client.push(other)["stored"]
    monkeypatch.setattr(store, "rank", lambda question, passages: [
        10.0 if "12345" in text or "SAVED_FINDING" in text else -10.0 for text in passages])
    value = ask(client, trail, ContextBudget(max_tokens=2048))
    assert not value["hit"] and value["reason"] == "budget_limited"


@pytest.mark.parametrize("mode", ["whole_file", "legacy_budget", "no_budget"])
def test_explicit_whole_file_legacy_and_no_budget_never_use_new_fallback(transport, monkeypatch, mode):
    client, repo, database, _ = transport
    trail = publish_bundle(client, repo)

    def forbidden(*args, **kwargs):
        pytest.fail("An unaffected interface must not enter evidence fallback")

    monkeypatch.setattr(database, "find_evidence", forbidden)
    if mode == "whole_file":
        result = ask(client, trail, ContextBudget(max_tokens=2048), include_evidence_files=True)
        assert not result["hit"] and result["reason"] == "budget_limited"
    else:
        result = client.ask(trail.question, "team", **(
            {"context_budget": ContextBudget(max_tokens=4096)} if mode == "legacy_budget" else {}))
        assert "response_profile" not in result


def test_successful_limited_response_is_byte_identical_without_fallback(transport, monkeypatch):
    client, repo, database, _ = transport
    trail = controls.publish(client, repo)
    budget = ContextBudget(max_tokens=2048)
    expected = original_packet(database, trail, budget)
    assert expected["hit"] and expected["context_budget"]["limited"]

    def forbidden(*args, **kwargs):
        pytest.fail("Receipt limited=True is not a budget miss")

    monkeypatch.setattr(database, "find_evidence", forbidden)
    assert serialize_response(ask(client, trail, budget)) == serialize_response(expected)


def test_malformed_identity_never_triggers_budget_fallback(transport, monkeypatch):
    client, repo, database, _ = transport
    trail = publish_bundle(client, repo)
    answer = database.find_answer(trail.question, ["team"], 604800, 0.60,
                                  context_budget=ContextBudget(max_tokens=2048), compact_bundle=True)
    conflicting = copy.deepcopy(answer["sources"][0])
    conflicting["content_hash"] = "0" * 64
    answer["sources"].append(conflicting)
    monkeypatch.setattr(database, "find_answer", lambda *args, **kwargs: copy.deepcopy(answer))
    monkeypatch.setattr(database, "find_evidence", lambda *args, **kwargs: pytest.fail("invalid evidence fallback"))
    value = ask(client, trail, ContextBudget(max_tokens=2048))
    assert not value["hit"] and value["reason"] == "invalid_evidence"


@pytest.mark.parametrize("edit_after_transport", [False, True])
def test_git_verified_fallback_keeps_full_identity_checks(transport, monkeypatch, edit_after_transport):
    client, repo, _, _ = transport
    trail = publish_bundle(client, repo, git=True)
    if edit_after_transport:
        request = Client.request

        def edit(self, route, payload):
            result = request(self, route, payload)
            if route == "/ask":
                (repo / "part0.py").write_bytes(b"OTHER_0 = 999\n")
            return result

        monkeypatch.setattr(Client, "request", edit)
    value = ask(client, trail, ContextBudget(max_tokens=2048), repo=repo, repository_id="project")
    if edit_after_transport:
        assert not value["hit"] and value["reason"] == "git_local_evidence_not_verified"
    else:
        assert value["hit"] and value["match_kind"] == "evidence"
        assert value["git"]["compatibility"] == "matched"
        assert value["id"] == trail.id and len(value["sources"]) == 4


def test_fallback_survives_real_mcp_serialization(transport):
    client, repo, _, _ = transport
    trail = publish_bundle(client, repo)
    server = mcp.create_mcp(client.home, workflow_namespace="team", workflow_repo=repo,
                            workflow_read_only=True, context_budget=ContextBudget(max_tokens=2048))
    text = asyncio.run(server.call_tool("ask", {"question": trail.question}))[0].text
    value = json.loads(text)
    assert text == serialize_response(value)
    assert value["hit"] and value["match_kind"] == "evidence" and "content" not in value
    assert value["context_budget"]["limited"]
    assert value["context_budget"]["used"] == len(text.encode()) <= 2048
