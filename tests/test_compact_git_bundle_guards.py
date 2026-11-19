"""Git caller reconstruction must retain or reject every compact bundle source."""

import copy
import hashlib

import pytest
import test_compact_delivery as controls

from colony import git_client, git_context
from colony.client import Client
from colony.compact import PROFILE
from colony.context_budget import count_response, fit_response, serialize_response
from colony.models import ContextBudget


@pytest.fixture
def transport(tmp_path, monkeypatch):
    # Real SQLite, publication/source checks, auth, HTTP and client; inference
    # uses this existing deterministic fixture. No provider calls or downloads.
    return controls.transport.__wrapped__(tmp_path, monkeypatch)


def publish(client, repo, count):
    return controls.publish(client, repo, count, content="Configured component values.", git=True)


def ask(client, repo, trail, maximum=8192):
    budget = ContextBudget(max_tokens=maximum)
    value = client.ask(
        trail.question, "team", repo=repo, repository_id="project",
        context_budget=budget, response_profile=PROFILE,
    )
    assert value["context_budget"]["used"] == count_response(value, budget) <= maximum
    return value


def assert_all_local(value, trail, repo):
    assert value["hit"] and value["status"] == "validated" and value["id"] == trail.id
    assert value["git"]["compatibility"] == "matched"
    assert value["evidence_scope"] == "cited_files"
    expected = {s.ref: s.content_hash for s in trail.sources}
    assert {s["ref"]: s["content_hash"] for s in value["sources"]} == expected
    reads = value["current_repository_reads"]
    assert len(reads) == len(expected) and {r["path"] for r in reads} == set(expected)
    for read in reads:
        raw = (repo / read["path"]).read_bytes()
        assert read["sha256"] == expected[read["path"]] == hashlib.sha256(raw).hexdigest()
        assert read["start_line"] == 1 and read["complete_file"] is True
        assert read["content"] == "\n".join(raw.decode("utf-8").splitlines())


@pytest.mark.parametrize("count,target", [(5, 4), (8, 4), (8, 7)])
@pytest.mark.parametrize("mutation", ["missing", "changed", "forged-identity"])
def test_late_bundle_source_cannot_be_skipped_after_server_validation(
    transport, monkeypatch, count, target, mutation,
):
    client, repo, _, _ = transport
    trail = publish(client, repo, count)
    request = Client.request
    seen = []

    def alter(self, route, payload):
        value = request(self, route, payload)
        if route == "/ask":
            assert value["hit"] and len(value["current_repository_reads"]) == count
            seen.append(True)
            ref = f"part{target}.py"
            if mutation == "missing":
                (repo / ref).unlink()
            elif mutation == "changed":
                (repo / ref).write_bytes(b"SPOOFED_LOCAL_VALUE = 999\n")
            else:
                # Keep read/source identity agreement and exact response byte
                # count. Only independent caller file validation can reject this.
                for source in value["sources"]:
                    if source["ref"] == ref:
                        source["content_hash"] = "f" * 64
                for read in value["current_repository_reads"]:
                    if read["path"] == ref:
                        read["sha256"] = "f" * 64
                budget = ContextBudget.model_validate(payload["context_budget"])
                assert count_response(value, budget) == value["context_budget"]["used"]
        return value

    monkeypatch.setattr(Client, "request", alter)
    value = ask(client, repo, trail)
    assert seen == [True]
    assert not value["hit"] and value["reason"] == "git_local_evidence_not_verified"
    assert "content" not in value and "current_repository_reads" not in value
    assert "SPOOFED" not in serialize_response(value)


def test_eight_verified_cores_fit_jointly_or_return_budget_miss(transport, monkeypatch):
    client, repo, _, _ = transport
    trail = publish(client, repo, 8)
    request = Client.request
    server = []

    def capture(self, route, payload):
        value = request(self, route, payload)
        if route == "/ask":
            server.append(copy.deepcopy(value))
        return value

    monkeypatch.setattr(Client, "request", capture)
    full = ask(client, repo, trail)
    assert_all_local(full, trail, repo)
    # Derive the actual smallest complete evidence-only representation. The Git
    # rebuild cannot save a byte by omitting the fifth or eighth required core.
    evidence = copy.deepcopy(full)
    evidence["match_kind"] = "evidence"
    evidence.pop("content", None)
    complete = fit_response(evidence, ContextBudget(max_tokens=8192))
    assert len(complete["current_repository_reads"]) == 8
    context = git_context.capture(repo, "project")
    near_budget = ContextBudget(max_tokens=complete["context_budget"]["used"] - 1)
    near = git_client.verify_lookup(server[0], repo, context, context_budget=near_budget)
    # The pressure flag's JSON spelling "true" is one byte shorter than "false".
    # That legal saving must not be confused with losing a required source core.
    assert_all_local(near, trail, repo)
    assert near["context_budget"]["limited"] is True
    assert count_response(near, near_budget) == near["context_budget"]["used"] <= near_budget.max_tokens

    evidence["context_budget"] = {"limited": True}
    complete = fit_response(evidence, ContextBudget(max_tokens=8192))
    floor = complete["context_budget"]["used"]
    floor_budget = ContextBudget(max_tokens=floor)
    at_floor = git_client.verify_lookup(server[0], repo, context, context_budget=floor_budget)
    assert_all_local(at_floor, trail, repo)
    assert at_floor["context_budget"]["limited"] is True
    assert count_response(at_floor, floor_budget) == at_floor["context_budget"]["used"] == floor
    maximum = floor - 1
    assert maximum >= 256
    budget = ContextBudget(max_tokens=maximum)
    limited = git_client.verify_lookup(server[0], repo, context, context_budget=budget)
    assert not limited["hit"] and limited["reason"] == "budget_limited"
    assert "content" not in limited and "current_repository_reads" not in limited
    assert limited["context_budget"]["limited"] is True
    assert count_response(limited, budget) == limited["context_budget"]["used"] <= maximum


@pytest.mark.parametrize("adaptive,compact,whole,expected", [
    (False, False, False, 2),
    (False, False, True, 4),
    (True, False, True, 4),
    (False, True, True, 4),
    (True, True, True, 8),
])
def test_only_adaptive_compact_bundle_expands_local_read_limit(
    transport, adaptive, compact, whole, expected,
):
    client, repo, _, _ = transport
    trail = publish(client, repo, 8)
    hints = [{"ref": s.ref, "line": 1, "text": "\n".join(
        (repo / s.ref).read_text(encoding="utf-8").splitlines()), "complete_file": True}
        for s in trail.sources]
    reads = git_client._local_reads(
        repo, trail.sources, hints, whole, adaptive=adaptive, compact_bundle=compact,
    )
    assert len(reads) == expected
    assert [r["ref"] for r in reads] == [s.ref for s in trail.sources[:expected]]


def test_compact_local_reconstruction_never_reads_a_ninth_hint(transport, monkeypatch):
    client, repo, _, _ = transport
    trail = publish(client, repo, 9)
    hints = [{"ref": s.ref, "line": 1, "text": "VALUE", "complete_file": True}
             for s in trail.sources]
    original = git_client._source_lines
    touched = []

    def counted(root, source):
        touched.append(source.ref)
        return original(root, source)

    monkeypatch.setattr(git_client, "_source_lines", counted)
    reads = git_client._local_reads(
        repo, trail.sources, hints, True, adaptive=True, compact_bundle=True,
    )
    assert len(reads) == 8 and touched == [s.ref for s in trail.sources[:8]]


def test_remote_ninth_required_hint_cannot_be_silently_dropped(transport, monkeypatch):
    client, repo, _, _ = transport
    trail = publish(client, repo, 9)
    request = Client.request

    def extra(self, route, payload):
        value = request(self, route, payload)
        if route == "/ask":
            assert value["hit"] and len(value["sources"]) == 9
            value["current_repository_reads"] = [
                {"path": s.ref, "sha256": s.content_hash, "start_line": 1,
                 "content": "\n".join((repo / s.ref).read_text(encoding="utf-8").splitlines()),
                 "complete_file": True}
                for s in trail.sources
            ]
            value = fit_response(value, ContextBudget.model_validate(payload["context_budget"]))
            assert value["hit"] and len(value["current_repository_reads"]) == 9
        return value

    monkeypatch.setattr(Client, "request", extra)
    value = ask(client, repo, trail)
    assert not value["hit"] and value["reason"] == "git_local_evidence_not_verified"
    assert "content" not in value and "current_repository_reads" not in value
