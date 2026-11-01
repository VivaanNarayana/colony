import hashlib
import json
import os
import subprocess
import sys
import time

import httpx
import pytest
from cryptography.fernet import Fernet

from colony import git_client, git_context
from colony.client import Client
from colony.models import GitContext, Grant, MemoryCorrection, Scope, Trail
from colony.security import generate_keys, issue, private_write
from colony.validation import snapshot


@pytest.fixture
def caller(tmp_path, monkeypatch):
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    (repo / "settings.py").write_text("TIMEOUT = 30\n")
    generate_keys(home)
    grant = Grant(
        member_id="a",
        name="A",
        server="http://127.0.0.1:7331",
        expires_at=int(time.time()) + 3600,
        scopes=[Scope(namespace="team", pull=True, push=True)],
    )
    private_write(
        home / "identity.json", issue(home / "issuer.pem", grant).model_dump_json().encode()
    )
    private_write(home / "queue.key", Fernet.generate_key())
    client = Client(home)
    context = GitContext(
        repository_id="project",
        state="git",
        commit="a" * 40,
        branch="main",
        detached=False,
        dirty=False,
    )
    monkeypatch.setattr(git_context, "capture", lambda *a: context)
    monkeypatch.setattr(git_client, "capture", lambda *a: context)
    source = snapshot(repo, "settings.py")
    return client, repo, context, source


def response(context, source, origin=None):
    return {
        "hit": True,
        "id": "b" * 32,
        "namespace": "team",
        "match_kind": "answer",
        "status": "validated",
        "content": "SAVED ANSWER",
        "sources": [source.model_dump()],
        "git": {
            "origin": (origin or context).model_dump(),
            "request": context.model_dump(),
            "server_observed": context.model_dump(),
            "compatibility": "matched",
        },
        "evidence": [{"ref": source.ref, "line": 1, "text": "FABRICATED SERVER TEXT"}],
    }


def transport(client, context, data, monkeypatch):
    calls = []

    def request(route, payload):
        calls.append((route, payload))
        if route == "/capabilities":
            return {
                "git_context": {"version": context.version, "repository_id": context.repository_id}
            }
        return data

    monkeypatch.setattr(client, "request", request)
    return calls


def test_caller_reads_replace_server_excerpts_without_local_model(caller, monkeypatch):
    client, repo, context, source = caller
    from colony import retrieval

    monkeypatch.setattr(retrieval, "models", lambda: pytest.fail("client must not load models"))
    calls = transport(client, context, response(context, source), monkeypatch)
    result = client.ask("timeout?", "team", repo=repo, repository_id="project")
    assert result["content"] == "SAVED ANSWER"
    assert result["evidence"][0]["text"] == "TIMEOUT = 30"
    assert "FABRICATED" not in json.dumps(result) and result["git"]["compatibility"] == "matched"
    assert len(json.dumps(result["git"]).encode()) < 1000
    assert [route for route, _ in calls] == ["/capabilities", "/ask"]
    assert calls[1][1]["git_context"] == context.model_dump()


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"branch": "feature"}, "branch_mismatch"),
        ({"commit": "c" * 40}, "commit_mismatch"),
        ({"dirty": True}, "dirty"),
    ],
)
def test_client_withholds_full_answer_even_if_server_claims_match(
    caller, monkeypatch, change, reason
):
    client, repo, context, source = caller
    origin = context.model_copy(update=change)
    transport(client, context, response(context, source, origin), monkeypatch)
    result = client.ask("timeout?", "team", repo=repo, repository_id="project")
    assert result["match_kind"] == "evidence" and result["status"] == "validated"
    assert "TIMEOUT = 30" in result["content"] and "SAVED ANSWER" not in json.dumps(result)
    assert result["git"]["compatibility"] == reason


def test_different_repository_never_exposes_saved_answer_or_reads(caller, monkeypatch):
    client, repo, context, source = caller
    transport(
        client,
        context,
        response(context, source, context.model_copy(update={"repository_id": "other"})),
        monkeypatch,
    )
    result = client.ask("timeout?", "team", repo=repo, repository_id="project")
    assert not result["hit"] and "content" not in result and "evidence" not in result


def test_matching_server_evidence_is_rejected_when_caller_file_changed(caller, monkeypatch):
    client, repo, context, source = caller
    transport(client, context, response(context, source), monkeypatch)
    (repo / "settings.py").write_text("TIMEOUT = 60\n")
    result = client.ask("timeout?", "team", repo=repo, repository_id="project")
    assert not result["hit"] and "content" not in result


def test_branch_switch_during_lookup_cannot_return_a_validated_answer(caller, monkeypatch):
    client, repo, context, source = caller
    transport(client, context, response(context, source), monkeypatch)
    monkeypatch.setattr(
        git_client, "capture", lambda *a: context.model_copy(update={"branch": "other"})
    )
    assert not client.ask("timeout?", "team", repo=repo, repository_id="project")["hit"]


@pytest.mark.parametrize("mode", ["unsupported", "wrong_repository"])
def test_no_silent_legacy_downgrade_when_capability_missing(caller, monkeypatch, mode):
    client, repo, context, source = caller
    calls = []

    def request(route, payload):
        calls.append(route)
        if mode == "wrong_repository":
            return {"git_context": {"version": context.version, "repository_id": "other"}}
        raise httpx.HTTPStatusError(
            "not found",
            request=httpx.Request("POST", "http://localhost"),
            response=httpx.Response(404),
        )

    monkeypatch.setattr(client, "request", request)
    assert (
        client.ask("q", "team", repo=repo, repository_id="project")["reason"]
        == "git_context_unsupported"
    )
    assert calls == ["/capabilities"]


def test_capable_server_missing_receipt_fails_closed(caller, monkeypatch):
    client, repo, context, source = caller
    data = response(context, source)
    del data["git"]
    transport(client, context, data, monkeypatch)
    assert not client.ask("q", "team", repo=repo, repository_id="project")["hit"]


def test_encrypted_queue_preserves_origin_across_retry_after_switch(caller, monkeypatch):
    client, repo, context, source = caller
    trail = Trail(id="d" * 32, namespace="team", question="q", content="answer", sources=[source])
    monkeypatch.setattr(
        client, "request", lambda *a: (_ for _ in ()).throw(httpx.ConnectError("offline"))
    )
    assert client.push(trail, repo=repo, repository_id="project")["queued"]
    encrypted = (client.home / "queue" / (trail.id + ".enc")).read_bytes()
    frozen = json.loads(Fernet((client.home / "queue.key").read_bytes()).decrypt(encrypted))
    assert frozen["trail"]["git_context"] == context.model_dump()
    monkeypatch.setattr(
        git_context, "capture", lambda *a: pytest.fail("retry must not restamp origin")
    )
    sent = []

    def request(route, payload):
        if route == "/capabilities":
            return {"git_context": {"version": context.version, "repository_id": "project"}}
        sent.append(payload)
        return {"stored": True}

    monkeypatch.setattr(client, "request", request)
    assert client.retry()["sent"] == 1 and sent == [frozen["trail"]]


def test_legacy_absent_git_fields_preserve_exact_payload_bytes(caller):
    _, _, _, source = caller
    trail = Trail(id="e" * 32, namespace="team", question="q", content="answer", sources=[source])
    legacy = {
        "id": "e" * 32,
        "namespace": "team",
        "question": "q",
        "content": "answer",
        "sources": [
            {
                "kind": "file",
                "ref": "settings.py",
                "content_hash": source.content_hash,
                "symbol": None,
                "contains": None,
            }
        ],
        "tainted": True,
        "capture": "none",
    }
    assert trail.model_dump_json() == json.dumps(legacy, separators=(",", ":"))
    assert (
        hashlib.sha256(trail.model_dump_json().encode()).digest()
        == hashlib.sha256(json.dumps(legacy, separators=(",", ":")).encode()).digest()
    )
    correction = MemoryCorrection(
        trail_id=trail.id,
        namespace="team",
        operation_id="f" * 32,
        expected_revision=0,
        question="q",
        content="answer",
        sources=[source],
    )
    assert "git_context" not in correction.model_dump()


def test_publication_refuses_context_changed_after_finding(caller, monkeypatch):
    client, repo, context, source = caller
    trail = Trail(
        id="f" * 32,
        namespace="team",
        question="q",
        content="answer",
        sources=[source],
        git_context=context.model_copy(update={"branch": "old"}),
    )
    monkeypatch.setattr(client, "request", lambda *a: pytest.fail("must fail before publication"))
    with pytest.raises(ValueError, match="context changed"):
        client.push(trail, repo=repo, repository_id="project")


@pytest.mark.parametrize(
    "field,value",
    [
        ("branch", "password=synthetic-sentinel"),
        ("branch", "https://private.example/repo"),
        ("branch", "/private/path"),
        ("repository_id", "ghp_" + "a" * 20),
    ],
)
def test_manual_git_metadata_cannot_publish_sensitive_or_path_values(caller, field, value):
    _, _, context, _ = caller
    values = context.model_dump()
    values[field] = value
    with pytest.raises(ValueError):
        GitContext.model_validate(values)


def test_setup_cli_never_echoes_invalid_repository_id(caller, monkeypatch, capsys):
    from colony import cli

    client, repo, _, _ = caller
    sentinel = "password=synthetic-setup-sentinel"
    monkeypatch.setattr(
        "sys.argv",
        [
            "colony",
            "--home",
            str(client.home),
            "setup",
            "codex",
            "install",
            "--repo",
            str(repo),
            "--ns",
            "team",
            "--repository-id",
            sentinel,
        ],
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    output = capsys.readouterr()
    assert sentinel not in output.err and "input_value" not in output.err
    assert "nonsensitive repository identifier" in output.err
    assert not (repo / ".codex").exists()


def test_cold_git_client_import_never_loads_native_retrieval_stack(tmp_path):
    code = """
import importlib.abc, sys
blocked = ("numpy", "sqlite_vec", "fastembed", "onnxruntime", "colony.retrieval", "colony.context")
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("Git-aware client imported a native retrieval dependency")
sys.meta_path.insert(0, Guard())
from colony.git_client import verify_lookup
assert not any(name in sys.modules for name in blocked)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=tmp_path,
        env=os.environ.copy(),
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def test_local_source_redaction_preserves_lines_and_rejects_changed_bytes(tmp_path):
    source_file = tmp_path / "source.txt"
    source_file.write_bytes(
        b"before\n-----BEGIN PRIVATE KEY-----\nsynthetic-private\n"
        b"-----END PRIVATE KEY-----\nafter\n"
    )
    source = snapshot(tmp_path, "source.txt")
    lines = git_client._source_lines(tmp_path, source)
    assert lines[0] == "before" and lines[4] == "after"
    assert "synthetic-private" not in "\n".join(lines) and "[REDACTED]" in lines[1]
    source_file.write_bytes(b"different")
    assert git_client._source_lines(tmp_path, source) == []
