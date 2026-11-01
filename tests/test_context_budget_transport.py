import asyncio
import json
import os
import subprocess
import time

import pytest

from colony import git_client, git_context
from colony.client import Client
from colony.context_budget import count_response, fit_response, serialize_response
from colony.models import Ask, ContextBudget, GitContext, Grant, Scope, Trail
from colony.security import generate_keys, issue, private_write
from colony.validation import snapshot


def test_absent_budget_keeps_exact_legacy_ask_bytes():
    expected = '{"question":"q","namespace":null,"include_evidence_files":false}'
    assert Ask(question="q").model_dump_json() == expected
    assert Ask(question="q", context_budget=None).model_dump_json() == expected


@pytest.fixture
def caller(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    (repo / "guide.md").write_bytes(b"TIMEOUT = 30\n")
    generate_keys(home)
    grant = Grant(
        member_id="a",
        name="Fixture",
        server="http://127.0.0.1:1",
        expires_at=int(time.time()) + 3600,
        scopes=[Scope(namespace="team", pull=True, push=True)],
    )
    private_write(
        home / "identity.json", issue(home / "issuer.pem", grant).model_dump_json().encode()
    )
    return Client(home), repo, snapshot(repo, "guide.md")


def capability(encoding="utf8_bytes"):
    return {
        "context_budget": {
            "version": "colony.context-budget/1",
            "min_tokens": 256,
            "max_tokens": 8192,
            "encodings": [encoding],
        },
        "git_context": {"version": "colony.git-context/1", "repository_id": "project"},
    }


@pytest.mark.parametrize("mode", ["missing", "string_encodings", "wrong_version", "bad_range"])
def test_client_refuses_unsupported_budget_without_legacy_request(caller, monkeypatch, mode):
    client, _, _ = caller
    data = capability()
    if mode == "missing":
        data.pop("context_budget")
    elif mode == "string_encodings":
        data["context_budget"]["encodings"] = "utf8_bytes"
    elif mode == "wrong_version":
        data["context_budget"]["version"] = "future"
    else:
        data["context_budget"]["max_tokens"] = True
    calls = []

    def request(route, payload):
        calls.append(route)
        return data

    monkeypatch.setattr(client, "request", request)
    result = client.ask("timeout", "team", context_budget=ContextBudget())
    assert result["hit"] is False and result["reason"] == "context_budget_unsupported"
    assert calls == ["/capabilities"]
    assert count_response(result, ContextBudget()) <= 2048


@pytest.mark.parametrize("mode", ["missing", "used", "encoding", "version", "limit", "measurement"])
def test_client_rejects_unbounded_or_mislabelled_server_receipt(caller, monkeypatch, mode):
    client, _, _ = caller
    budget = ContextBudget()
    answer = fit_response({"hit": False, "reason": "no_relevant_trail"}, budget)
    if mode == "missing":
        answer.pop("context_budget")
    else:
        key, value = {
            "used": ("used", 0),
            "encoding": ("encoding", "o200k_base"),
            "version": ("version", "future"),
            "limit": ("max_tokens", 8192),
            "measurement": ("measurement", "billing_tokens"),
        }[mode]
        answer["context_budget"][key] = value
    monkeypatch.setattr(
        client,
        "request",
        lambda route, payload: capability() if route == "/capabilities" else answer,
    )
    result = client.ask("timeout", "team", context_budget=budget)
    assert result["hit"] is False and result["reason"] == "context_budget_invalid_receipt"
    assert result["context_budget"]["used"] == count_response(result, budget)


def test_git_aware_budget_rebuild_uses_local_text_and_preserves_limited_state(caller, monkeypatch):
    client, repo, source = caller
    context = GitContext(
        repository_id="project",
        state="git",
        branch="main",
        commit="a" * 40,
        detached=False,
        dirty=False,
    )
    monkeypatch.setattr(git_context, "capture", lambda *args: context)
    monkeypatch.setattr(git_client, "capture", lambda *args: context)
    budget = ContextBudget(max_tokens=4096)
    answer = {
        "hit": True,
        "status": "validated",
        "match_kind": "evidence",
        "content": "FABRICATED",
        "sources": [source.model_dump()],
        "evidence": [
            {
                "ref": source.ref,
                "line": 1,
                "text": "FABRICATED!!",
                "complete_file": False,
                "selected_line": 1,
                "selected_end_line": 1,
            }
        ],
        "git": {"origin": context.model_dump(), "request": context.model_dump()},
        "context_budget": {"limited": True},
    }
    answer = fit_response(answer, budget)
    calls = []

    def request(route, payload):
        calls.append(route)
        return capability() if route == "/capabilities" else answer

    monkeypatch.setattr(client, "request", request)
    result = client.ask(
        "timeout", "team", repo=repo, repository_id="project", context_budget=budget
    )
    assert calls == ["/capabilities", "/ask"]
    assert result["hit"] and "TIMEOUT = 30" in result["content"]
    assert "FABRICATED" not in json.dumps(result)
    assert result["context_budget"]["limited"] is True
    assert result["evidence"][0]["selected_line"] == 1
    assert result["context_budget"]["used"] == count_response(result, budget) <= 4096


def test_git_aware_budget_limited_miss_survives_verification(caller, monkeypatch):
    client, repo, _ = caller
    context = GitContext(repository_id="project", state="non_git")
    monkeypatch.setattr(git_context, "capture", lambda *args: context)
    monkeypatch.setattr(git_client, "capture", lambda *args: context)
    budget = ContextBudget()
    answer = fit_response(
        {"hit": False, "reason": "budget_limited", "context_budget": {"limited": True}}, budget
    )
    monkeypatch.setattr(
        client,
        "request",
        lambda route, payload: capability() if route == "/capabilities" else answer,
    )
    result = client.ask(
        "timeout", "team", repo=repo, repository_id="project", context_budget=budget
    )
    assert not result["hit"] and result["reason"] == "budget_limited"
    assert result["context_budget"]["limited"] is True


@pytest.mark.parametrize("two_cores", [False, True])
def test_adaptive_local_rebuild_retains_indivisible_cores_beyond_legacy_caps(
    caller, monkeypatch, two_cores
):
    client, repo, _ = caller
    encoding = "o200k_base" if two_cores else "utf8_bytes"
    if two_cores:
        path = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
        if not path:
            pytest.skip("Explicit local tokenizer fixture unavailable")
        pytest.importorskip("tiktoken")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", path)
    first = "deploy --manifest=" + "a" * 2600
    second = "verify --manifest=" + "b" * 2600
    (repo / "guide.md").write_bytes(
        (first + ("\n\n" + second if two_cores else "") + "\n").encode()
    )
    source = snapshot(repo, "guide.md")
    context = git_context.capture(repo, "project")
    assert context.state == "non_git"
    evidence = [
        {
            "ref": source.ref,
            "line": 1,
            "text": first,
            "complete_file": False,
            "selected_line": 1,
            "selected_end_line": 1,
        }
    ]
    if two_cores:
        evidence.append(
            {
                "ref": source.ref,
                "line": 3,
                "text": second,
                "complete_file": False,
                "selected_line": 3,
                "selected_end_line": 3,
            }
        )
    budget = ContextBudget(max_tokens=8192, encoding=encoding)
    answer = fit_response(
        {
            "hit": True,
            "status": "validated",
            "match_kind": "evidence",
            "sources": [source.model_dump()],
            "evidence": evidence,
            "git": {"origin": context.model_dump(), "request": context.model_dump()},
        },
        budget,
    )
    assert answer["hit"] and answer["context_budget"]["used"] <= 8192
    monkeypatch.setattr(
        client,
        "request",
        lambda route, payload: capability(encoding) if route == "/capabilities" else answer,
    )

    result = client.ask(
        "deploy and verify", "team", repo=repo, repository_id="project", context_budget=budget
    )

    assert result["hit"] and len(result["evidence"]) == (2 if two_cores else 1)
    assert result["evidence"][0]["text"] == first
    if two_cores:
        assert result["evidence"][1]["text"] == second
    assert result["context_budget"]["used"] == count_response(result, budget) <= 8192
    assert git_client._local_reads(repo, [source], evidence, False) == []


def test_legacy_client_never_adds_budget_or_capability_request(caller, monkeypatch):
    client, _, _ = caller
    calls = []
    monkeypatch.setattr(
        client, "request", lambda route, payload: calls.append((route, payload)) or {"hit": False}
    )
    assert client.ask("timeout", "team") == {"hit": False}
    assert calls == [("/ask", {"question": "timeout", "namespace": "team"})]


def test_adaptive_hint_cannot_cut_a_local_command_mid_line(caller):
    _, repo, _ = caller
    command = "deploy --signed-manifest=release.tar --verify"
    (repo / "guide.md").write_bytes((command + "\n").encode())
    source = snapshot(repo, "guide.md")
    hint = {
        "ref": source.ref,
        "line": 1,
        "text": "deploy",
        "complete_file": False,
        "selected_line": 1,
        "selected_end_line": 1,
    }
    reads = git_client._local_reads(repo, [source], [hint], False, adaptive=True)
    assert len(reads) == 1 and reads[0]["text"] == command
    assert git_client._local_reads(repo, [source], [hint], False)[0]["text"] == "deploy"
    hint["selected_end_line"] = 2
    assert git_client._local_reads(repo, [source], [hint], False, adaptive=True) == []


@pytest.mark.parametrize(
    "ref,text,selected",
    [
        (
            "profiles.md",
            "# Production profile\n\nTIMEOUT=30\n\n# Development profile\n\nTIMEOUT=5",
            [3, 7],
        ),
        (
            "profiles.md",
            "# Environment\n\n## Production\n\n### Retry\n\nTIMEOUT=30\n\n"
            "## Development\n\n### Retry\n\nTIMEOUT=5",
            [7, 13],
        ),
        (
            "profiles.md",
            "- ## Production profile\n\n  TIMEOUT=30\n\n"
            "- ## Development profile\n\n  TIMEOUT=5",
            [3, 7],
        ),
        (
            "profiles.md",
            "> ## Production profile\n>\n> TIMEOUT=30\n\n"
            "> ## Development profile\n>\n> TIMEOUT=5",
            [1, 5],
        ),
        (
            "deploy.sh",
            "deploy --manifest=<(cat <<'JSON'\n\n"
            '{"verify_signature":true}\nJSON\n) --production',
            [1],
        ),
        (
            "deploy",
            "#!/usr/bin/env -S bash -eu\n\n"
            "deploy --manifest=<(cat <<'JSON'\n\n"
            '{"verify_signature":true}\nJSON\n) --production',
            [3],
        ),
    ],
    ids=[
        "profile-headings", "nested-headings", "list-headings", "quoted-headings",
        "heredoc", "shebang-heredoc",
    ],
)
@pytest.mark.parametrize("maximum", [256, 4096])
def test_required_source_context_survives_git_client_and_final_mcp(
    caller, monkeypatch, ref, text, selected, maximum
):
    from colony import mcp
    from colony.context import excerpts

    client, repo, _ = caller
    (repo / ref).write_bytes((text + "\n").encode())
    # Only the disposable repository is initialized. Fixture commands are data.
    for arguments in [
        ["init", "-b", "main"],
        ["add", "--", ref, "guide.md"],
        [
            "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "fixture",
        ],
    ]:
        subprocess.run(["git", "-C", str(repo), *arguments], check=True, capture_output=True)
    source = snapshot(repo, ref)
    origin = git_context.capture(repo, "project")
    assert origin.state == "git"
    items = excerpts(
        repo,
        [source],
        "configuration",
        preferred=[(ref, line) for line in selected],
        adaptive=True,
    )
    budget = ContextBudget(max_tokens=maximum)

    def request(route, payload):
        if route == "/capabilities":
            return capability()
        assert route == "/ask"
        return fit_response(
            {
                "hit": True,
                "status": "validated",
                "match_kind": "evidence",
                "sources": [source.model_dump()],
                "evidence": items,
                "git": {"origin": origin.model_dump(), "request": payload["git_context"]},
            },
            budget,
        )

    monkeypatch.setattr(client, "request", request)
    result = client.ask(
        "configuration", "team", repo=repo, repository_id="project", context_budget=budget
    )
    monkeypatch.setattr(mcp, "Client", lambda home: client)
    server = mcp.create_mcp(
        client.home,
        workflow_namespace="team",
        workflow_repo=repo,
        workflow_repository_id="project",
        context_budget=budget,
    )
    rendered = asyncio.run(server.call_tool("ask", {"question": "configuration"}))[0].text
    final = json.loads(rendered)
    for value in (result, final):
        assert value["context_budget"]["used"] == count_response(value, budget) <= maximum
        if maximum == 256:
            assert not value["hit"] and value["reason"] == "budget_limited"
            assert "evidence" not in value and "current_repository_reads" not in value
            continue
        assert value["hit"] and value["status"] == "validated"
        assert value["git"]["compatibility"] == "matched"
        reads = value.get("evidence", value.get("current_repository_reads"))
        remaining = list(text.splitlines())
        for read in reads:
            start = read.get("line", read.get("start_line"))
            content = read.get("text", read.get("content"))
            assert content == "\n".join(remaining[start - 1 : start + content.count("\n")])
            assert read["selected_line"] == start
            assert read["selected_end_line"] == start + content.count("\n")
            assert not read["complete_file"]
            if "sha256" in read:
                assert read["sha256"] == source.content_hash
        # Retaining only unqualified values or an unfinished heredoc is insufficient.
        combined = "\n".join(read.get("text", read.get("content")) for read in reads)
        if ref.endswith(".md"):
            # A blank separator between independent cited spans is optional.
            assert [line for line in combined.splitlines() if line.strip()] == [
                line for line in text.splitlines() if line.strip()
            ]
        else:
            assert combined == text
    assert rendered == serialize_response(final)


@pytest.mark.parametrize("encoding", ["utf8_bytes", "o200k_base"])
def test_http_counts_exact_final_json_and_retains_actual_fact(team, monkeypatch, encoding):
    from conftest import auth

    if encoding == "o200k_base":
        path = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
        if not path:
            pytest.skip("Explicit verified local tokenizer fixture unavailable")
        pytest.importorskip("tiktoken")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", path)
    app, api, repo, _, key = team
    trail = Trail(
        id="b" * 32,
        namespace="team",
        question="Build timeout",
        content="The timeout is30.",
        sources=[snapshot(repo, "build.py")],
    )
    assert api.post("/trails", json=trail.model_dump(), headers=auth(key)).status_code == 200
    budget = ContextBudget(max_tokens=4096, encoding=encoding)
    response = api.post(
        "/ask",
        json={"question": "Build timeout", "context_budget": budget.model_dump()},
        headers=auth(key),
    )
    assert response.status_code == 200
    result = response.json()
    assert result["hit"] and result["status"] == "validated"
    assert any("TIMEOUT = 30" in item["text"] for item in result["evidence"])
    assert response.content == serialize_response(result).encode("utf-8")
    assert result["context_budget"]["used"] == count_response(result, budget) <= 4096
    limited = api.post(
        "/ask",
        json={"question": "Build timeout", "context_budget": {"max_tokens": 256}},
        headers=auth(key),
    ).json()
    assert not limited["hit"] and limited["reason"] == "budget_limited"
    assert len(serialize_response(limited).encode()) <= 256
    # One caller's budget never changes stored lifecycle/freshness for other callers.
    legacy = api.post("/ask", json={"question": "Build timeout"}, headers=auth(key)).json()
    assert legacy["hit"] and legacy["match_kind"] == "answer"
    assert "context_budget" not in legacy


@pytest.mark.parametrize("value", [True, 255, 8193, 512.5, "512"])
def test_http_refuses_invalid_budgets_without_echo(team, value):
    from conftest import auth

    _, api, _, _, key = team
    result = api.post(
        "/ask",
        json={"question": "synthetic-private-query", "context_budget": {"max_tokens": value}},
        headers=auth(key),
    )
    assert result.status_code == 422 and result.json() == {"detail": "Invalid request"}
    assert "synthetic-private-query" not in result.text


def test_http_capability_and_unavailable_counter_fail_closed(team, monkeypatch):
    from conftest import auth

    monkeypatch.delenv("COLONY_CONTEXT_TOKENIZER", raising=False)
    _, api, _, _, key = team
    assert api.post("/capabilities", json={}).status_code == 401
    available = api.post("/capabilities", json={}, headers=auth(key)).json()["context_budget"]
    assert available["encodings"] == ["utf8_bytes"]
    result = api.post(
        "/ask",
        json={"question": "timeout", "context_budget": {"encoding": "o200k_base"}},
        headers=auth(key),
    )
    assert result.status_code == 422
    assert result.json() == {"detail": "Context budget encoding unavailable"}
