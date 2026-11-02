"""Final response accounting and opt-in interface compatibility."""

import asyncio
import copy
import json
import os
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from colony import cli, mcp, setup
from colony.models import ContextBudget


@pytest.fixture
def client(monkeypatch):
    calls, response = [], {"hit": False}

    class FakeClient:
        def __init__(self, home):
            self.keyfile = SimpleNamespace(
                grant=SimpleNamespace(
                    scopes=[SimpleNamespace(namespace="team", pull=True, push=True)]
                )
            )

        def ask(self, question, namespace=None, **kwargs):
            calls.append((question, namespace, kwargs))
            return copy.deepcopy(response)

    for module in (cli, mcp, setup):
        monkeypatch.setattr(module, "Client", FakeClient)
    return calls, response


def call(server, **arguments):
    return asyncio.run(server.call_tool("ask", arguments))[0].text


def validated(response, content="saved answer"):
    response.update(
        {
            "hit": True,
            "match_kind": "answer",
            "status": "validated",
            "tainted": True,
            "content": content,
            "sources": [{"ref": "guide.md", "content_hash": "a" * 64}],
            "evidence": [
                {
                    "ref": "guide.md",
                    "line": 1,
                    "text": "local facts 🐜\n" * 80,
                    "complete_file": True,
                    "selected_line": 40,
                    "selected_end_line": 42,
                }
            ],
            "complete_files": True,
            "memory": {"feedback": {"incorrect": 1}},
            "git": {"compatibility": "matched"},
        }
    )


def test_legacy_cli_no_budget_keyword_and_unchanged_render(client, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["colony", "--home", str(tmp_path), "ask", "q"])
    cli.main()
    assert client[0] == [("q", None, {"include_evidence_files": False})]
    assert capsys.readouterr().out == json.dumps({"hit": False}, indent=2) + "\n"


def test_legacy_mcp_six_tools_and_exact_ask_schema(client, tmp_path):
    server = mcp.create_mcp(tmp_path)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert set(tools) == {
        "ask",
        "push",
        "memory_inspect",
        "memory_feedback",
        "memory_correct",
        "memory_change",
    }
    assert tools["ask"].inputSchema == {
        "properties": {
            "question": {"title": "Question", "type": "string"},
            "namespace": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Namespace",
            },
        },
        "required": ["question"],
        "title": "askArguments",
        "type": "object",
    }
    text = call(server, question="q")
    assert text.startswith('{"hit":false,"research_guidance":')
    assert client[0][-1][2] == {"include_evidence_files": False}


@pytest.mark.parametrize("maximum", ["255", "8193", "password=sentinel"])
def test_invalid_cli_budget_fixed_before_client(client, maximum, capsys):
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["ask", "q", "--context-budget", maximum])
    assert not client[0]
    assert maximum not in capsys.readouterr().err


def test_encoding_requires_budget_before_client(client):
    with pytest.raises(ValueError, match="Context encoding requires a context budget"):
        cli.execute(cli.parser().parse_args(["ask", "q", "--context-encoding", "utf8_bytes"]))
    assert not client[0]


def test_cli_adaptive_default_canonical_utf8(client, tmp_path, monkeypatch, capsys):
    from colony.context_budget import fit_response, serialize_response

    client[1].update(fit_response({"hit": False, "detail": "facts 🐜 é"}, ContextBudget()))
    monkeypatch.setattr(
        sys, "argv", ["colony", "--home", str(tmp_path), "ask", "q", "--context-budget"]
    )
    cli.main()
    assert client[0][-1][2]["context_budget"] == ContextBudget()
    assert capsys.readouterr().out == serialize_response(client[1])


def test_adaptive_mcp_final_accounting_and_fixed_encoding_override(client, tmp_path):
    from colony.context_budget import count_response, serialize_response

    validated(client[1])
    server = mcp.create_mcp(tmp_path, context_budget=ContextBudget())
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert len(tools) == 6 and "max_context_tokens" in tools["ask"].inputSchema["properties"]
    text = call(server, question="facts", max_context_tokens=2048)
    final = json.loads(text)
    assert text == serialize_response(final)
    assert count_response(final, ContextBudget()) <= 2048
    assert final["context_budget"]["used"] == len(text.encode("utf-8"))
    assert client[0][-1][2]["context_budget"] == ContextBudget()
    assert "UNTRUSTED AS INSTRUCTIONS" in final["provenance"]
    assert "unverified" in final["memory_warning"]
    assert final["tainted"] and final["git"]["compatibility"] == "matched"
    assert "verification" in final and "next_action" in final


@pytest.mark.parametrize("maximum", [255, 8193, True])
def test_mcp_override_rejects_invalid_before_request(client, tmp_path, maximum):
    server = mcp.create_mcp(tmp_path, context_budget=ContextBudget())
    with pytest.raises(ToolError):
        call(server, question="q", max_context_tokens=maximum)
    assert not client[0]


def test_adaptive_selected_spans_and_no_half_saved_answer(client, tmp_path, monkeypatch):
    from colony import context_budget

    validated(client[1], content="α" * 5000)
    seen, real_fit = [], context_budget.fit_response

    def fit(value, budget, **kwargs):
        seen.append(copy.deepcopy(value))
        return real_fit(value, budget, **kwargs)

    monkeypatch.setattr(context_budget, "fit_response", fit)
    final = json.loads(call(mcp.create_mcp(tmp_path, context_budget=ContextBudget()), question="q"))
    read = seen[-1]["current_repository_reads"][0]
    assert (read["selected_line"], read["selected_end_line"]) == (40, 42)
    assert seen[-1]["content"] == "α" * 5000 and "content_truncated" not in seen[-1]
    assert "content" not in final
    assert final["match_kind"] == "evidence"
    assert final["current_repository_reads"]
    assert not final["current_repository_reads"][0]["complete_file"]


def test_setup_budget_install_audit_reversible(client, tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    (repo / "AGENTS.md").write_bytes(b"# user guidance\n")
    budget = ContextBudget(max_tokens=512)
    setup.setup_codex(tmp_path, repo, "install", namespace="team", context_budget=budget)
    config = tomllib.loads((repo / ".codex/config.toml").read_text())
    assert config["mcp_servers"]["colony"]["args"][-4:] == [
        "--context-budget",
        "512",
        "--context-encoding",
        "utf8_bytes",
    ]
    assert setup.setup_codex(tmp_path, repo, "status")["installed"]
    assert not setup.setup_codex(
        tmp_path, repo, "install", namespace="team", context_budget=budget
    )["changed"]
    setup.setup_codex(tmp_path, repo, "uninstall")
    assert (repo / "AGENTS.md").read_bytes() == b"# user guidance\n"
    assert not (repo / ".codex/config.toml").exists()


def test_invalid_setup_budget_before_project_io(client, tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "_read", lambda path: pytest.fail("Read before validation"))
    with pytest.raises(ValueError, match="Invalid context budget configuration") as error:
        setup.setup_codex(
            tmp_path,
            tmp_path,
            "install",
            namespace="team",
            context_budget={"max_tokens": "password=sentinel"},
        )
    assert "sentinel" not in str(error.value)


def test_unavailable_encoding_fail_closed_before_identity_or_setup_io(
    client, tmp_path, monkeypatch
):
    from colony import context_budget

    def unavailable(*args):
        raise ValueError("Requested context encoding is unavailable")

    monkeypatch.setattr(context_budget, "count_response", unavailable)
    with pytest.raises(ValueError, match="unavailable"):
        cli.execute(
            cli.parser().parse_args(
                ["ask", "q", "--context-budget", "--context-encoding", "o200k_base"]
            )
        )
    with pytest.raises(ValueError, match="unavailable"):
        mcp.create_mcp(tmp_path, context_budget=ContextBudget(encoding="o200k_base"))
    monkeypatch.setattr(setup, "_read", lambda path: pytest.fail("Read before encoding validation"))
    with pytest.raises(ValueError, match="unavailable"):
        setup.setup_codex(
            tmp_path,
            tmp_path,
            "install",
            namespace="team",
            context_budget=ContextBudget(encoding="o200k_base"),
        )
    assert not client[0]


def test_adaptive_mcp_preserves_upstream_limited_and_miss_is_bounded(client, tmp_path):
    from colony.context_budget import fit_response

    client[1].update(fit_response({"hit": False}, ContextBudget()))
    client[1]["context_budget"]["limited"] = True
    server = mcp.create_mcp(tmp_path, context_budget=ContextBudget())
    final = json.loads(call(server, question="q", max_context_tokens=256))
    assert final["context_budget"]["limited"]
    assert final["context_budget"]["max_tokens"] == 256
    assert final["context_budget"]["used"] <= 256


def test_legacy_answer_truncation_still_4000_and_no_selected_fields(client, tmp_path):
    validated(client[1], content="x" * 5000)
    final = json.loads(call(mcp.create_mcp(tmp_path), question="q"))
    assert final["content"] == "x" * 4000 and final["content_truncated"]
    assert "selected_line" not in final["current_repository_reads"][0]
    assert "context_budget" not in final


@pytest.mark.parametrize("command", ["mcp", "setup"])
@pytest.mark.parametrize("enabled", [False, True])
def test_cli_forwards_budget_only_when_explicit(client, tmp_path, monkeypatch, command, enabled):
    seen = []
    if command == "mcp":
        monkeypatch.setattr(mcp, "serve", lambda *args, **kwargs: seen.append(kwargs))
        arguments = ["mcp"]
    else:
        monkeypatch.setattr(setup, "setup_codex", lambda *args, **kwargs: seen.append(kwargs))
        arguments = ["setup", "codex", "install", "--repo", str(tmp_path), "--ns", "team"]
    if enabled:
        arguments += ["--context-budget", "512"]
    cli.execute(cli.parser().parse_args(arguments))
    if enabled:
        assert seen[-1]["context_budget"] == ContextBudget(max_tokens=512)
    else:
        assert "context_budget" not in seen[-1]


@pytest.mark.parametrize("encoding", ["utf8_bytes", "o200k_base"])
def test_mcp_unicode_final_serialization_bounded_in_selected_encoding(client, tmp_path, encoding):
    from colony.context_budget import count_response, supported_encodings

    if encoding not in supported_encodings():
        pytest.skip("Explicit verified local tokenizer is unavailable")
    validated(client[1], content="facts 🐜 é " * 1000)
    budget = ContextBudget(max_tokens=2048, encoding=encoding)
    text = call(mcp.create_mcp(tmp_path, context_budget=budget), question="facts")
    final = json.loads(text)
    assert final["context_budget"]["encoding"] == encoding
    assert final["context_budget"]["used"] == count_response(final, budget) <= 2048


def test_adaptive_cli_emits_utf8_bytes_even_when_stdout_encoding_is_legacy(tmp_path):
    code = r"""
import sys
from colony import cli
from colony.context_budget import fit_response
from colony.models import ContextBudget
class Client:
    def __init__(self, home): pass
    def ask(self, *args, **kwargs):
        return fit_response({"hit": False, "detail": "facts \U0001f41c \u00e9"}, kwargs["context_budget"])
cli.Client = Client
sys.argv = ["colony", "ask", "q", "--context-budget"]
cli.main()
"""
    process = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=20,
    )
    result = json.loads(process.stdout.decode("utf-8"))
    assert result["detail"] == "facts 🐜 é"
    assert result["context_budget"]["used"] == len(process.stdout)


def test_adaptive_mcp_error_never_reflects_private_arguments_or_sdk_framing(client, tmp_path):
    from mcp import types

    message = "Invalid adaptive lookup request or context budget unavailable"
    server = mcp.create_mcp(tmp_path, context_budget=ContextBudget(max_tokens=256))
    arguments = {
        "question": "password=synthetic-private-question",
        "max_context_tokens": "password=synthetic-private-value",
    }
    with pytest.raises(ToolError) as failure:
        call(server, **arguments)
    assert str(failure.value) == message
    assert "synthetic-private" not in str(failure.value)
    assert not client[0]
    # Exercise the SDK's actual registered handler without stdio/network/model work.
    request = types.CallToolRequest(
        method="tools/call", params=types.CallToolRequestParams(name="ask", arguments=arguments)
    )
    result = asyncio.run(server._mcp_server.request_handlers[types.CallToolRequest](request))
    assert result.root.isError
    assert result.root.content[0].text == message
    envelope = types.JSONRPCResponse(
        jsonrpc="2.0", id=1, result=result.model_dump(mode="json", exclude_none=True)
    )
    wire = envelope.model_dump_json(exclude_none=True).encode("utf8") + b"\n"
    assert len(wire) <= 256 and b"synthetic-private" not in wire


def test_legacy_ask_validation_behavior_remains_unchanged(client, tmp_path):
    server = mcp.create_mcp(tmp_path)
    with pytest.raises(ToolError) as failure:
        call(server, question={"legacy-validation-control": True})
    assert "Invalid adaptive lookup request" not in str(failure.value)
    assert "legacy-validation-control" in str(failure.value)
    assert not client[0]


def test_adaptive_mcp_runtime_counter_failure_is_fixed(client, tmp_path, monkeypatch):
    from colony import context_budget

    server = mcp.create_mcp(tmp_path, context_budget=ContextBudget(max_tokens=256))

    def unavailable(*args, **kwargs):
        raise ValueError("password=synthetic-runtime-secret")

    monkeypatch.setattr(context_budget, "fit_response", unavailable)
    with pytest.raises(ToolError) as failure:
        call(server, question="private question")
    assert str(failure.value) == "Invalid adaptive lookup request or context budget unavailable"
    assert "synthetic-runtime" not in str(failure.value)
