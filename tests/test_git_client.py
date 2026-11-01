import asyncio
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


@pytest.mark.parametrize("other_branch", [False, True])
def test_local_markdown_section_retains_selected_fact_after_line_32(
    caller, monkeypatch, other_branch
):
    from colony.context import excerpts, source_chunks

    client, repo, context, _ = caller
    lines = ["# Build timeout configuration", ""] + [f"item {i:02d}" for i in range(1, 91)]
    lines[86] = "BUILD_TIMEOUT = 9876"
    (repo / "guide.md").write_bytes(("\n".join(lines) + "\n").encode())
    source = snapshot(repo, "guide.md")
    selected = next(
        (ref, line) for ref, line, text in source_chunks(repo, [source]) if "9876" in text
    )
    hints = excerpts(repo, [source], "Build timeout configuration", preferred=[selected])
    assert hints[0]["line"] == 1 and hints[0]["text"].count("\n") > 32
    assert "BUILD_TIMEOUT = 9876" in hints[0]["text"]
    origin = context.model_copy(update={"branch": "feature"}) if other_branch else context
    data = response(context, source, origin)
    data["evidence"] = hints
    transport(client, context, data, monkeypatch)

    result = client.ask("Build timeout configuration", "team", repo=repo, repository_id="project")

    assert result["hit"] and result["status"] == "validated"
    assert result["evidence"] == hints
    assert "BUILD_TIMEOUT = 9876" in result["evidence"][0]["text"]
    if other_branch:
        assert result["match_kind"] == "evidence"
        assert "BUILD_TIMEOUT = 9876" in result["content"]
        assert "SAVED ANSWER" not in json.dumps(result)
    else:
        assert result["content"] == "SAVED ANSWER"


@pytest.mark.parametrize("other_branch", [False, True])
@pytest.mark.parametrize("whole_files", [False, True])
def test_distinct_selected_sections_in_one_file_survive_local_rebuild(
    caller, monkeypatch, other_branch, whole_files
):
    from colony.context import excerpts, source_chunks

    client, repo, context, _ = caller
    text = (
        "# Build timeout\n\nBUILD_TIMEOUT = 30\n\n"
        "# Retry configuration\n\nRETRY_LIMIT = 5\n\n# Unrelated appendix\n\n"
        + "background detail\n" * 1000
    )
    (repo / "guide.md").write_bytes(text.encode())
    source = snapshot(repo, "guide.md")
    preferred = [
        (ref, line)
        for ref, line, text in source_chunks(repo, [source])
        if "BUILD_TIMEOUT = 30" in text or "RETRY_LIMIT = 5" in text
    ]
    hints = excerpts(
        repo, [source], "Build timeout and retry limit", preferred=preferred, whole_files=whole_files
    )
    assert len(hints) == 2 and not any(hint["complete_file"] for hint in hints)
    origin = context.model_copy(update={"branch": "feature"}) if other_branch else context
    data = response(context, source, origin)
    data["evidence"] = hints
    transport(client, context, data, monkeypatch)

    result = client.ask(
        "Build timeout and retry limit",
        "team",
        repo=repo,
        repository_id="project",
        include_evidence_files=whole_files,
    )

    assert result["hit"] and result["status"] == "validated"
    assert result["evidence"] == hints
    assert "BUILD_TIMEOUT = 30" in result["evidence"][0]["text"]
    assert "RETRY_LIMIT = 5" in result["evidence"][1]["text"]
    if other_branch:
        assert result["match_kind"] == "evidence"
        assert "RETRY_LIMIT = 5" in result["content"]
        assert "SAVED ANSWER" not in json.dumps(result)
        from colony import mcp

        monkeypatch.setattr(mcp, "Client", lambda home: client)
        server = mcp.create_mcp(
            client.home,
            evidence_files=whole_files,
            workflow_namespace="team",
            workflow_repo=repo,
            workflow_read_only=True,
            workflow_repository_id="project",
        )
        receipt = json.loads(
            asyncio.run(server.call_tool("ask", {"question": "Build timeout and retry limit"}))[0].text
        )
        reads = receipt["current_repository_reads"]
        assert len(reads) == 2
        assert "BUILD_TIMEOUT = 30" in reads[0]["content"]
        assert "RETRY_LIMIT = 5" in reads[1]["content"]
        assert "SAVED ANSWER" not in json.dumps(receipt)


def test_overlapping_duplicate_hints_leave_room_for_a_distinct_section(caller):
    _, repo, _, _ = caller
    (repo / "guide.md").write_bytes(b"# Build\nTIMEOUT = 30\n\n# Retry\nRETRY_LIMIT = 5\n")
    source = snapshot(repo, "guide.md")
    first = {"ref": source.ref, "line": 1, "text": "X" * len("# Build\nTIMEOUT = 30")}
    overlap = {"ref": source.ref, "line": 2, "text": "X" * len("TIMEOUT = 30")}
    second = {"ref": source.ref, "line": 4, "text": "X" * len("# Retry\nRETRY_LIMIT = 5")}

    reads = git_client._local_reads(repo, [source], [first, first, overlap, second], False)

    assert [item["line"] for item in reads] == [1, 4]
    assert "RETRY_LIMIT = 5" in reads[1]["text"]


def test_duplicate_complete_files_do_not_consume_evidence_file_slots(caller):
    _, repo, _, _ = caller
    (repo / "retry.txt").write_bytes(b"RETRY_LIMIT = 5\n")
    sources = [snapshot(repo, name) for name in ("settings.py", "retry.txt")]
    hints = [
        {"ref": source.ref, "line": 1, "text": "untrusted", "complete_file": True}
        for source in sources
    ]

    reads = git_client._local_reads(repo, sources, [hints[0], hints[0], hints[1]], True)

    assert len(reads) == 2 and all(item["complete_file"] for item in reads)
    assert [item["text"] for item in reads] == ["TIMEOUT = 30", "RETRY_LIMIT = 5"]


@pytest.mark.parametrize(
    "hint_text", [None, 5, "", "x" * 2401], ids=["missing", "not_text", "empty", "oversized"]
)
def test_unbounded_or_malformed_excerpt_hint_cannot_return_a_hit(
    caller, monkeypatch, hint_text
):
    client, repo, context, source = caller
    data = response(context, source)
    data["evidence"][0]["text"] = hint_text
    transport(client, context, data, monkeypatch)
    result = client.ask("timeout?", "team", repo=repo, repository_id="project")
    assert not result["hit"] and "content" not in result and "evidence" not in result


def test_excerpt_span_is_rebuilt_locally_with_exact_unicode_character_bound(caller, monkeypatch):
    client, repo, context, _ = caller
    (repo / "guide.md").write_bytes("outside\nαβγ\nselected: 😀 done\nuncited tail\n".encode())
    source = snapshot(repo, "guide.md")
    data = response(context, source)
    expected = "αβγ\nselected: 😀"
    data["evidence"] = [{"ref": source.ref, "line": 2, "text": "X" * len(expected)}]
    transport(client, context, data, monkeypatch)

    result = client.ask("selected?", "team", repo=repo, repository_id="project")

    assert result["evidence"] == [
        {"ref": source.ref, "line": 2, "text": expected, "complete_file": False}
    ]
    assert "uncited tail" not in json.dumps(result)


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
