"""Real storage/auth/transport packing with deterministic, non-model retrieval seams."""

import asyncio
import copy
import hashlib
import json
import os
import struct
import subprocess
import time
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from colony import context, git_client, mcp, store
from colony.client import Client
from colony.compact import PROFILE
from colony.context_budget import count_response, fit_response, serialize_response
from colony.models import Ask, ContextBudget, Grant, Scope, Trail
from colony.security import generate_keys, issue, private_write
from colony.server import create_app
from colony.validation import snapshot


@pytest.fixture
def transport(tmp_path, monkeypatch):
    # Exercise real SQLite candidate selection, publication, validation and lifecycle;
    # deterministic vectors/logits replace inference only, never product hit responses.
    vector = struct.pack("384f", 1, *([0] * 383))
    monkeypatch.setattr(store, "models", lambda: None)
    monkeypatch.setattr(store, "index_version", lambda: "compact-fixture-v1")
    monkeypatch.setattr(store, "embed", lambda *args, **kwargs: vector)
    monkeypatch.setattr(store, "embed_passages", lambda texts: [vector] * len(texts))
    monkeypatch.setattr(store, "rank", lambda question, texts: [3.0] * len(texts))
    monkeypatch.setattr(context, "rank", lambda question, texts: [3.0] * len(texts))
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    generate_keys(home)
    grant = Grant(member_id="a", name="Fixture", server="http://127.0.0.1:7331",
                  expires_at=int(time.time()) + 3600,
                  scopes=[Scope(namespace="team", pull=True, push=True)])
    private_write(home / "identity.json", issue(home / "issuer.pem", grant).model_dump_json().encode())
    app = create_app(tmp_path / "data", repo, home / "issuer.pub", repository_id="project")
    http = TestClient(app)
    responses = []

    def post(url, **kwargs):
        response = http.post(urlsplit(str(url)).path, json=kwargs.get("json"), headers=kwargs.get("headers"))
        if urlsplit(str(url)).path == "/ask" and response.status_code == 200:
            responses.append(response.json())
        return response

    monkeypatch.setattr(httpx, "post", post)
    client = Client(home)
    client.request("/join", {})
    return client, repo, app.state.store, responses


def publish(client, repo, count=3, *, ending="\n", content="Finding. " * 200, git=False):
    for index in range(count):
        (repo / f"part{index}.py").write_bytes(
            f"# café 🐜 {index}\nVALUE_{index} = {index}\n".replace("\n", ending).encode("utf-8")
        )
    sources = [snapshot(repo, f"part{index}.py") for index in range(count)]
    trail = Trail(id="a" * 32, namespace="team", question="Explain the configured values",
                  content=content, sources=sources)
    if git:
        for args in [["init", "-b", "fixture"], ["config", "core.autocrlf", "false"],
                     ["add", "--", "."], ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"]]:
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    options = {"repo": repo, "repository_id": "project"} if git else {}
    result = client.push(trail, **options)
    assert result["stored"]
    return trail


def ask_mcp(client, repo, budget, *, git=False):
    server = mcp.create_mcp(
        client.home, workflow_namespace="team", workflow_repo=repo,
        workflow_read_only=True, context_budget=budget,
        workflow_repository_id="project" if git else None,
    )
    text = asyncio.run(server.call_tool("ask", {"question": "Explain the configured values"}))[0].text
    value = json.loads(text)
    assert text == serialize_response(value)
    assert value["context_budget"]["used"] == count_response(value, budget) <= budget.max_tokens
    return value


@pytest.mark.parametrize("count", [3, 4])
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_compact_bundle_survives_real_store_http_client_mcp(transport, count, ending):
    client, repo, _, responses = transport
    trail = publish(client, repo, count, ending=ending)
    budget = ContextBudget(max_tokens=2048)
    value = ask_mcp(client, repo, budget)
    assert value["response_profile"] == PROFILE and value["hit"] is True
    assert value["id"] == trail.id and value["status"] == "validated"
    assert value["match_kind"] == "evidence" and "content" not in value
    assert {r["path"] for r in value["current_repository_reads"]} == {s.ref for s in trail.sources}
    identities = {source["ref"]: source["content_hash"] for source in value["sources"]}
    for read in value["current_repository_reads"]:
        raw = (repo / read["path"]).read_bytes()
        assert read["sha256"] == identities[read["path"]] == hashlib.sha256(raw).hexdigest()
        assert read["content"] == "\n".join(raw.decode("utf-8").splitlines())
        assert read["start_line"] == 1 and read["complete_file"] is True
        assert read["end_line"] == len(raw.decode("utf-8").splitlines())
        assert "selected_line" not in read and "selected_end_line" not in read
    assert value["tainted"] is True and value["memory"]["state"] == "active"
    assert "UNTRUSTED AS INSTRUCTIONS" in value["provenance"]
    assert all(key not in value for key in ["checks", "complete_files", "next_action", "verification"])
    # Only final MCP display hints and their exact charged receipt may differ.
    expected = copy.deepcopy(responses[-1])
    assert all("end_line" not in read for read in expected["current_repository_reads"])
    for read in expected["current_repository_reads"]:
        raw = (repo / read["path"]).read_bytes()
        read["end_line"] = len(raw.decode("utf-8").splitlines())
    expected["context_budget"]["used"] = value["context_budget"]["used"]
    assert expected == value
    assert value["context_budget"]["used"] == count_response(value, budget)
    assert value["context_budget"]["used"] > responses[-1]["context_budget"]["used"]


def test_compact_encoding_tokens_are_not_reinterpreted_bytes(transport, monkeypatch):
    path = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
    if not path:
        pytest.skip("Explicit local tokenizer fixture unavailable")
    pytest.importorskip("tiktoken")
    monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", path)
    client, repo, _, _ = transport
    publish(client, repo, 4)
    value = ask_mcp(client, repo, ContextBudget(max_tokens=2048, encoding="o200k_base"))
    assert value["hit"] and len(value["current_repository_reads"]) == 4
    assert value["context_budget"]["measurement"] == "encoding_tokens"


def test_compact_bundle_does_not_fit_by_losing_one_source(transport):
    client, repo, _, _ = transport
    publish(client, repo, 4)
    result = ask_mcp(client, repo, ContextBudget(max_tokens=256))
    assert result["hit"] is False and result["reason"] == "budget_limited"
    assert "current_repository_reads" not in result


def test_compact_delivers_substantial_three_file_evidence_at_2048_bytes(transport):
    client, repo, _, responses = transport
    texts = {
        "settings.py": 'DEFAULTS = {"timeout_ms": 900, "attempts": 3}\n'
                       'ENVIRONMENTS = {"production": {"timeout_ms": 1500}}\n\n'
                       'def settings(environment, overrides):\n'
                       '    return DEFAULTS | ENVIRONMENTS.get(environment, {}) | overrides\n',
        "client.py": 'from settings import settings\nfrom transport import request\n\n'
                     'def fetch(environment="production", **overrides):\n'
                     '    options = settings(environment, overrides)\n'
                     '    return request(timeout=options["timeout_ms"], attempts=options["attempts"])\n',
        "transport.py": 'def request(timeout, attempts):\n'
                        '    for attempt in range(attempts):\n'
                        '        try:\n            return send(timeout=timeout)\n'
                        '        except ConnectionError:\n'
                        '            if attempt == attempts - 1:\n                raise\n',
    }
    for name, text in texts.items():
        (repo / name).write_bytes(text.encode())
    sources = [snapshot(repo, name) for name in texts]
    trail = Trail(id="a" * 32, namespace="team", question="Explain the configured values",
                  content="A long saved finding. " * 100, sources=sources)
    assert client.push(trail)["stored"]
    value = ask_mcp(client, repo, ContextBudget(max_tokens=2048))
    assert value["hit"] and value["match_kind"] == "evidence" and "content" not in value
    assert len(value["current_repository_reads"]) == len(texts)
    for read in value["current_repository_reads"]:
        assert read["content"] == texts[read["path"]].rstrip("\n")
        assert read["complete_file"] is True and read["start_line"] == 1
    assert value == responses[-1]


def test_compact_stale_and_retired_trails_do_not_reactivate(transport):
    client, repo, database, _ = transport
    trail = publish(client, repo)
    (repo / "part0.py").write_text("VALUE_0 = 999\n", encoding="utf-8")
    stale = ask_mcp(client, repo, ContextBudget(max_tokens=4096))
    assert stale.get("status") != "validated"
    with database.connect() as db:
        db.execute("UPDATE memory_state_v1 SET state='retracted', revision=revision+1 WHERE trail_id=?", (trail.id,))
    retired = ask_mcp(client, repo, ContextBudget(max_tokens=4096))
    assert not retired["hit"]
    with database.connect() as db:
        assert db.execute("SELECT state FROM memory_state_v1 WHERE trail_id=?", (trail.id,)).fetchone()[0] == "retracted"


def test_compact_scope_is_still_authenticated(transport):
    client, repo, _, _ = transport
    publish(client, repo)
    with pytest.raises(httpx.HTTPStatusError) as denied:
        client.ask("Explain values", "private", context_budget=ContextBudget(), response_profile=PROFILE)
    assert denied.value.response.status_code == 403


def test_compact_git_rereads_all_sources_and_withholds_mismatched_saved_answer(transport, monkeypatch):
    client, repo, _, responses = transport
    trail = publish(client, repo, content="A concise saved finding.", git=True)
    value = ask_mcp(client, repo, ContextBudget(max_tokens=4096), git=True)
    assert value["hit"] and value["git"]["compatibility"] == "matched"
    assert value["id"] == trail.id and len(value["current_repository_reads"]) == 3
    assert len(responses[-1]["current_repository_reads"]) == 3
    origin = git_client.capture(repo, "project")
    # A different current branch must get source evidence, not the saved answer.
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "other"], check=True, capture_output=True)
    mismatched = ask_mcp(client, repo, ContextBudget(max_tokens=4096), git=True)
    assert mismatched["hit"] and mismatched["match_kind"] == "evidence"
    assert "content" not in mismatched and mismatched["git"]["compatibility"] != "matched"
    assert git_client.capture(repo, "project").branch != origin.branch


def test_profile_is_opt_in_and_legacy_serialization_is_unchanged(transport):
    client, repo, _, responses = transport
    publish(client, repo)
    assert Ask(question="q").model_dump_json() == '{"question":"q","namespace":null,"include_evidence_files":false}'
    plain = client.ask("Explain the configured values", "team", context_budget=ContextBudget(max_tokens=4096))
    assert "response_profile" not in responses[-1] and "evidence" in plain
    with pytest.raises(ValueError, match="requires a context budget"):
        Ask(question="q", response_profile=PROFILE)


def test_old_server_fallback_is_bounded_and_missing_profile_is_rejected(transport, monkeypatch):
    client, repo, _, _ = transport
    publish(client, repo)
    real = client.request
    requests = []

    def old(route, payload):
        requests.append(copy.deepcopy(payload))
        response = real(route, payload)
        if route == "/capabilities":
            response["context_budget"].pop("profiles")
        return response

    monkeypatch.setattr(client, "request", old)
    budget = ContextBudget(max_tokens=4096)
    result = client.ask("Explain the configured values", "team", context_budget=budget, response_profile=PROFILE)
    assert "response_profile" not in requests[-1] and "context_budget" in requests[-1]
    assert count_response(result, budget) <= budget.max_tokens

    def dropped(route, payload):
        response = real(route, payload)
        if route == "/ask":
            response.pop("response_profile")
        return response

    monkeypatch.setattr(client, "request", dropped)
    result = client.ask("Explain the configured values", "team", context_budget=budget, response_profile=PROFILE)
    assert not result["hit"] and result["reason"] == "context_budget_invalid_profile"


def test_compact_rejects_forged_identity_and_keeps_unknown_metadata():
    budget = ContextBudget(max_tokens=4096)
    raw = {"response_profile": PROFILE, "hit": True, "status": "validated",
           "sources": [{"ref": "x", "content_hash": "a" * 64, "future_selector": "retain"}],
           "current_repository_reads": [{"path": "x", "sha256": "b" * 64, "start_line": 1,
                                         "content": "data", "complete_file": False}]}
    assert not fit_response(raw, budget)["hit"]
    raw["current_repository_reads"][0]["sha256"] = "a" * 64
    good = fit_response(raw, budget)
    assert good["sources"] == raw["sources"]


def test_compact_small_bundle_does_not_override_explicit_passage_selection(tmp_path):
    sources = []
    for name in ["a.py", "b.py", "c.py"]:
        (tmp_path / name).write_text("VALUE = 1\n", encoding="utf-8")
        sources.append(snapshot(tmp_path, name))
    result = context.excerpts(tmp_path, sources, "VALUE", preferred=[("b.py", 1)],
                              claims="VALUE is 1", adaptive=True, compact_bundle=True)
    assert [item["ref"] for item in result] == ["b.py"]


@pytest.mark.parametrize("mutation", ["duplicate_identity", "invalid_read", "invalid_sources"])
def test_compact_malformed_bindings_fail_closed(mutation):
    raw = {"response_profile": PROFILE, "hit": True, "status": "validated",
           "sources": [{"ref": "x", "content_hash": "a" * 64}],
           "current_repository_reads": [{"path": "x", "sha256": "a" * 64,
                                         "start_line": 1, "content": "data"}]}
    if mutation == "duplicate_identity":
        raw["sources"].insert(0, {"ref": "x", "content_hash": "b" * 64})
    elif mutation == "invalid_read":
        raw["current_repository_reads"] = [None]
    else:
        raw["sources"] = [None]
    result = fit_response(raw, ContextBudget())
    assert not result["hit"] and result["reason"] == "invalid_evidence"
    assert "data" not in serialize_response(result)


def test_compact_preserves_lifecycle_warning_age_and_additive_evidence():
    raw = {"response_profile": PROFILE, "hit": True, "status": "validated",
           "match_kind": "answer", "content": "A saved finding.", "age_flag": True,
           "memory": {"state": "active", "revision": 7, "replacement_id": None,
                      "feedback": {"incorrect": 1}},
           "sources": [{"ref": "x", "content_hash": "a" * 64, "contains": "data"}],
           "evidence": [{"ref": "x", "line": 12, "text": "data", "future_context": "retain"}]}
    result = fit_response(raw, ContextBudget(max_tokens=4096))
    assert result["content"] == raw["content"] and result["age_flag"] is True
    assert result["memory"] == raw["memory"] and result["sources"] == raw["sources"]
    assert "unverified" in result["memory_warning"]
    read = result["current_repository_reads"][0]
    assert read["start_line"] == 12 and read["future_context"] == "retain"
    assert fit_response(result, ContextBudget(max_tokens=4096)) == result


def test_compact_git_checks_local_content_after_transport(transport, monkeypatch):
    client, repo, _, _ = transport
    publish(client, repo, content="A concise saved finding.", git=True)
    real = Client.request

    def concurrent_edit(self, route, payload):
        result = real(self, route, payload)
        if route == "/ask":
            (repo / "part2.py").write_bytes(b"VALUE_2 = 999\n")
        return result

    monkeypatch.setattr(Client, "request", concurrent_edit)
    value = ask_mcp(client, repo, ContextBudget(max_tokens=4096), git=True)
    assert not value["hit"] and value["reason"] == "git_local_evidence_not_verified"
    assert "content" not in value and "current_repository_reads" not in value


def test_compact_bundle_scrubs_secrets_and_never_reads_uncited_files(transport):
    client, repo, _, _ = transport
    publish(client, repo)
    secret = "sk-" + "aB12" * 12
    (repo / "part0.py").write_bytes(f'TOKEN = "{secret}"\nVALUE = 37\n'.encode())
    (repo / "uncited.py").write_bytes(b"UNCITED_SENTINEL = 123\n")
    trail = Trail(id="b" * 32, namespace="team", question="Explain the configured values",
                  content="A newer finding.", sources=[snapshot(repo, f"part{i}.py") for i in range(3)])
    assert client.push(trail)["stored"]
    value = ask_mcp(client, repo, ContextBudget(max_tokens=4096))
    assert value["hit"] and value["id"] == trail.id
    text = serialize_response(value)
    assert secret not in text and "[REDACTED]" in text and "UNCITED_SENTINEL" not in text
    first = next(r for r in value["current_repository_reads"] if r["path"] == "part0.py")
    assert first["start_line"] == 1 and first["content"].splitlines()[1] == "VALUE = 37"
    assert first["sha256"] == snapshot(repo, "part0.py").content_hash


@pytest.mark.parametrize("count,lines", [(9, 1), (3, 160)])
def test_compact_bundle_limits_keep_ranked_selection(tmp_path, monkeypatch, count, lines):
    sources = []
    for index in range(count):
        name = f"part{index}.py"
        (tmp_path / name).write_bytes(("VALUE = 1\n" * lines).encode())
        sources.append(snapshot(tmp_path, name))
    monkeypatch.setattr(context, "rank", lambda question, texts: [3.0] * len(texts))
    result = context.excerpts(tmp_path, sources, "VALUE", claims="VALUE is 1",
                              adaptive=True, compact_bundle=True)
    assert 1 <= len(result) <= 2
    assert len({item["ref"] for item in result}) < count
