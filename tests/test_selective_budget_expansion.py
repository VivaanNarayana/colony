"""No agent sessions: exercise scoped expansion through storage and transports."""
import asyncio
import copy
import hashlib
import json
import os
import subprocess

import pytest
from test_compact_budget_fallback import transport as transport

from colony import context, mcp
from colony.compact import PROFILE
from colony.context_budget import count_response, fit_response, serialize_response
from colony.models import ContextBudget, Trail
from colony.validation import snapshot


def publish(client, repo, *, git=False, ending="\n", same_file=False):
    files = {
        "config.py": "BASE_DEADLINE_MS = 730  # café 🐜\n\nNEEDLE = BASE_DEADLINE_MS",
        "caller.py": "UNUSED_LABEL = 'extra'\n\nHAYSTACK = 19",
        "other.py": "OTHER = 1",
        "notes.txt": "notes\n\n",
    }
    if same_file:
        files["config.py"] += "\n\nHAYSTACK = 19"
        files["caller.py"] = "UNUSED_LABEL = 'extra'"
    files["notes.txt"] += "x" * (2800 - sum(map(len, files.values())))
    for name, text in files.items():
        (repo / name).write_bytes(text.replace("\n", ending).encode())
    if git:
        for args in [["init", "-b", "fixture"], ["config", "core.autocrlf", "false"],
                     ["add", "--", "."], ["-c", "user.name=Fixture", "-c",
                     "user.email=fixture@example.invalid", "commit", "-m", "fixture"]]:
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    trail = Trail(id="c" * 32, namespace="team", question="What are NEEDLE and HAYSTACK?",
                  content="Saved finding. " * 100, tainted=True,
                  sources=[snapshot(repo, name) for name in files])
    assert client.push(trail, **({"repo": repo, "repository_id": "project"} if git else {}))["stored"]
    return trail


def selected(database, trail, budget, **options):
    return database.find_evidence(
        trail.question, ["team"], 604800, context_budget=budget,
        restricted_trail_id=trail.id,
        required_source_identities=frozenset((s.ref, s.content_hash) for s in trail.sources),
        **options,
    )


def packet(value, budget):
    return fit_response({**value, "response_profile": PROFILE}, budget)


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
@pytest.mark.parametrize("encoding", ["utf8_bytes", "o200k_base"])
@pytest.mark.parametrize("git", [False, True])
def test_primary_file_expands_without_extra_lookup_or_secondary_padding(
    transport, ending, encoding, git, monkeypatch,
):
    client, repo, database, _ = transport
    if encoding == "o200k_base":
        if not os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER"):
            pytest.skip("Pinned local tokenizer required")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", os.environ["COLONY_TEST_CONTEXT_TOKENIZER"])
    trail = publish(client, repo, git=git, ending=ending)
    budget = ContextBudget(max_tokens=(3072 if git else 2048) if encoding == "utf8_bytes"
                           else (1000 if git else 900), encoding=encoding)
    calls = []
    request = client.__class__.request

    def counted(self, route, payload):
        calls.append(route)
        return request(self, route, payload)

    monkeypatch.setattr(client.__class__, "request", counted)
    server = mcp.create_mcp(client.home, workflow_namespace="team", workflow_repo=repo,
                            workflow_repository_id="project" if git else None,
                            workflow_read_only=True, context_budget=budget)
    text = asyncio.run(server.call_tool("ask", {"question": trail.question}))[0].text
    value = json.loads(text)
    assert calls.count("/ask") == 1 and "/push" not in calls
    assert value["hit"] and value["match_kind"] == "evidence"
    assert "content" not in value and value["tainted"]
    assert value["context_budget"]["used"] == count_response(value, budget) <= budget.max_tokens
    assert value["sources"] == [{"ref": s.ref, "content_hash": s.content_hash} for s in trail.sources]
    reads = {r["path"]: r for r in value["current_repository_reads"]}
    assert set(reads) == {"config.py", "caller.py"}
    assert "BASE_DEADLINE_MS = 730" in reads["config.py"]["content"]
    assert reads["config.py"]["complete_file"] and reads["config.py"]["start_line"] == 1
    assert reads["caller.py"]["content"] == "HAYSTACK = 19"
    assert not reads["caller.py"]["complete_file"]
    assert value["evidence_scope"] == "partial"
    for read in reads.values():
        assert read["sha256"] == hashlib.sha256((repo / read["path"]).read_bytes()).hexdigest()
        assert "selected_line" not in read
    if git:
        assert value["git"]["compatibility"] == "matched"


def test_required_cores_fit_before_optional_file_and_refits_fail_closed(transport):
    client, repo, database, _ = transport
    trail = publish(client, repo)
    budget = ContextBudget(max_tokens=2048)
    core_input = selected(database, trail, budget)
    core_input["context_budget"] = {"limited": True}
    core = packet(core_input, budget)
    expanded_input = selected(database, trail, budget, expand_first_file=True)
    expanded_input["context_budget"] = {"limited": True}
    expanded = packet(expanded_input, budget)
    assert expanded["context_budget"]["used"] > core["context_budget"]["used"]
    # Before the first wire boundary, required span coordinates are still private
    # and optional context can be removed without losing either selected unit.
    tight = ContextBudget(max_tokens=core["context_budget"]["used"])
    reduced = packet(expanded_input, tight)
    assert reduced["hit"]
    assert [r["content"] for r in reduced["current_repository_reads"]] == [
        r["content"] for r in core["current_repository_reads"]]
    assert reduced["sources"] == core["sources"]
    too_small = ContextBudget(max_tokens=tight.max_tokens - 1)
    assert packet(expanded_input, too_small)["reason"] == "budget_limited"
    # After public serialization, never infer a smaller required span or trust
    # hidden selection metadata that no longer exists. Later overhead fails closed.
    public = json.loads(serialize_response(expanded))
    assert fit_response(public, tight)["reason"] == "budget_limited"
    public["git"] = {"compatibility": "matched", "framing": "x" * 2048}
    pressured = fit_response(public, budget)
    assert pressured["reason"] == "budget_limited" and "current_repository_reads" not in pressured
    assert pressured["context_budget"]["used"] <= budget.max_tokens
    for _ in range(3):
        expanded = fit_response(expanded, budget)
        assert expanded["hit"] and "730" in expanded["current_repository_reads"][0]["content"]


def test_same_file_disjoint_cores_never_become_overlapping_full_reads(transport):
    client, repo, database, _ = transport
    trail = publish(client, repo, same_file=True)
    budget = ContextBudget(max_tokens=2048)
    value = packet(selected(database, trail, budget, expand_first_file=True), budget)
    reads = value["current_repository_reads"]
    assert len(reads) == 2 and all(r["path"] == "config.py" for r in reads)
    assert {r["content"] for r in reads} == {"NEEDLE = BASE_DEADLINE_MS", "HAYSTACK = 19"}
    assert not any(r["complete_file"] for r in reads)
    assert reads[0]["start_line"] != reads[1]["start_line"]


def test_expansion_cannot_broaden_unrestricted_evidence_lookup(transport):
    client, repo, database, _ = transport
    trail = publish(client, repo)
    budget = ContextBudget(max_tokens=2048)
    baseline = database.find_evidence(trail.question, ["team"], 604800, context_budget=budget)
    for restriction in ({}, {"restricted_trail_id": trail.id},
                        {"required_source_identities": frozenset((s.ref, s.content_hash) for s in trail.sources)}):
        value = database.find_evidence(trail.question, ["team"], 604800,
                                      context_budget=budget, expand_first_file=True, **restriction)
        assert value["evidence"] == baseline["evidence"]


def test_generic_selection_and_margin_behavior_are_unchanged(transport):
    client, repo, database, _ = transport
    trail = publish(client, repo)
    preferred = [("config.py", 3), ("caller.py", 3)]
    ordinary = context.excerpts(repo, trail.sources, trail.question, preferred=preferred, adaptive=True)
    expanded = context.excerpts(repo, trail.sources, trail.question, preferred=preferred,
                                adaptive=True, expand_first_file=True)
    assert not any(item["complete_file"] for item in ordinary)
    assert expanded[0]["complete_file"] and not expanded[1]["complete_file"]
    assert expanded[0]["selected_line"] == ordinary[0]["selected_line"]
    assert expanded[1] == ordinary[1]
    assert context.excerpts(repo, trail.sources, trail.question, preferred=preferred) == context.excerpts(
        repo, trail.sources, trail.question, preferred=preferred, expand_first_file=True)


@pytest.mark.parametrize("status", ["stale", "unverified"])
def test_nonvalidated_prose_is_withheld_without_erasing_identity_or_status(transport, status):
    client, repo, database, _ = transport
    trail = publish(client, repo)
    raw = database.find_answer(trail.question, ["team"], 604800, .60)
    raw.update(status=status, evidence=[], content="OLD_UNSUPPORTED_VALUE " * 100)
    before = copy.deepcopy(raw)
    budget = ContextBudget(max_tokens=2048)
    value = packet(raw, budget)
    assert raw == before and value["hit"]
    assert value["content_withheld"] == value["status"] == status
    assert "OLD_UNSUPPORTED_VALUE" not in serialize_response(value)
    for key in ("id", "sources", "memory", "tainted", "namespace", "age_flag"):
        assert value[key] == raw[key]
    assert value["context_budget"]["used"] <= 2048
    assert fit_response(value, budget) == value
    assert "OLD_UNSUPPORTED_VALUE" in fit_response(raw, ContextBudget(max_tokens=8192))["content"]


@pytest.mark.parametrize("mutation,status", [("edit", "stale"), ("age", "unverified")])
def test_actual_nonvalidated_lookup_withholds_prose_and_preserves_legacy(transport, mutation, status):
    client, repo, database, _ = transport
    trail = publish(client, repo)
    if mutation == "edit":
        (repo / "config.py").write_bytes(b"BASE_DEADLINE_MS = 999\n")
    else:
        with database.connect() as db:
            db.execute("UPDATE trails SET created_at=0 WHERE id=?", (trail.id,))
    server = mcp.create_mcp(client.home, workflow_namespace="team", workflow_repo=repo,
                            workflow_read_only=True, context_budget=ContextBudget(max_tokens=2048))
    text = asyncio.run(server.call_tool("ask", {"question": trail.question}))[0].text
    value = json.loads(text)
    assert value["hit"] and value["status"] == value["content_withheld"] == status
    assert value["id"] == trail.id and value["memory"]["state"] == "active"
    assert value["sources"] == [{"ref": s.ref, "content_hash": s.content_hash} for s in trail.sources]
    assert value["recommendation"] == "research" and value["current_repository_reads"] == []
    assert "Saved finding" not in text and "content" not in value
    assert value["context_budget"]["used"] == len(text.encode()) <= 2048
    legacy = client.ask(trail.question, "team")
    assert legacy["status"] == status and legacy["content"] == trail.content
