import asyncio
import json
import re
import sys

import httpx
import pytest
import test_memory_api
from mcp.server.fastmcp.exceptions import ToolError

from colony import cli
from colony.client import Client, MemoryUnavailable
from colony.mcp import MEMORY_TOOLS, create_mcp
from colony.memory import MemoryInvalid

local_identity = test_memory_api.local_identity


@pytest.mark.parametrize("action", ["inspect", "feedback", "correct", "retract", "restore"])
def test_memory_cli_dispatch_and_correction_evidence(tmp_path, monkeypatch, action):
    recorded = []

    class Stub:
        def __getattr__(self, method):
            def call(*args, **kwargs):
                recorded.append((method, args, kwargs))
                request = {"trail_id": args[0], "namespace": args[1], **kwargs}
                route = "/memory/" + method.removeprefix("memory_")
                return test_memory_api.memory_acknowledgment(route, request)

            return call

    monkeypatch.setattr(cli, "Client", lambda home: Stub())
    argv = ["memory", action, "a" * 32]
    if action == "correct":
        file = tmp_path / "correction.json"
        file.write_text(json.dumps({"question": "q", "content": "new answer"}))
        (tmp_path / "settings.py").write_text("TIMEOUT = 30\n")
        argv += [str(file), "--repo", str(tmp_path), "--source", "settings.py"]
    argv += ["--ns", "team"]
    if action != "inspect":
        argv += ["--note", "explicit operation", "--operation-id", "b" * 32]
    if action == "feedback":
        argv += ["--verdict", "outdated"]
    if action in {"correct", "retract", "restore"}:
        argv += ["--expected-revision", "2"]
    assert cli.execute(cli.parser().parse_args(argv))["id"] == "a" * 32
    method, args, options = recorded[0]
    assert args == ("a" * 32, "team")
    assert method == "memory_" + ("change" if action in {"retract", "restore"} else action)
    if action != "inspect":
        assert options["operation_id"] == "b" * 32 and options["note"] == "explicit operation"
    if action == "correct":
        assert options["question"] == "q" and options["content"] == "new answer"
        assert options["expected_revision"] == 2
        assert options["sources"][0]["ref"] == "settings.py"
        assert len(options["sources"][0]["content_hash"]) == 64


def test_memory_cli_safe_invalid_file_and_unknown_outcome(tmp_path, monkeypatch, capsys):
    file = tmp_path / "bad.json"
    file.write_text('{"password":"private-file-sentinel","id":"not-allowed"}')
    monkeypatch.setattr(cli, "Client", lambda home: object())
    with pytest.raises(MemoryInvalid) as caught:
        cli.execute(
            cli.parser().parse_args(
                [
                    "memory",
                    "correct",
                    "a" * 32,
                    str(file),
                    "--ns",
                    "team",
                    "--expected-revision",
                    "0",
                ]
            )
        )
    assert "sentinel" not in str(caught.value)

    class Offline:
        def memory_feedback(self, *args, **kwargs):
            raise MemoryUnavailable("b" * 32)

    monkeypatch.setattr(cli, "Client", lambda home: Offline())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "colony",
            "memory",
            "feedback",
            "a" * 32,
            "--ns",
            "team",
            "--verdict",
            "helpful",
            "--note",
            "private-note-sentinel",
        ],
    )
    with pytest.raises(SystemExit) as exit_code:
        cli.main()
    assert exit_code.value.code == 1
    output = capsys.readouterr()
    assert "outcome unknown" in output.err and "b" * 32 in output.err
    assert "sentinel" not in output.err + output.out


def test_memory_cli_revision_error_does_not_echo_input(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Client", lambda home: object())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "colony",
            "memory",
            "retract",
            "a" * 32,
            "--ns",
            "team",
            "--expected-revision",
            "private-revision-sentinel",
        ],
    )
    with pytest.raises(SystemExit):
        cli.main()
    output = capsys.readouterr()
    assert "sentinel" not in output.err + output.out
    assert "Colony memory:" in output.err


def invoke(server, tool, **arguments):
    return asyncio.run(server.call_tool(tool, arguments))


def test_memory_mcp_manual_schema_annotations_and_read_only_workflow(
    local_identity, tmp_path, monkeypatch
):
    recorded = []

    def request(self, route, payload):
        recorded.append((route, payload))
        return test_memory_api.memory_acknowledgment(route, payload)

    monkeypatch.setattr(Client, "request", request)
    manual = create_mcp(local_identity.home)
    tools = {tool.name: tool for tool in asyncio.run(manual.list_tools())}
    assert set(tools) == {"ask", "push", *MEMORY_TOOLS}
    for name in MEMORY_TOOLS:
        assert "namespace" in tools[name].inputSchema["required"]
        assert tools[name].annotations.readOnlyHint == (name == "memory_inspect")
    assert set(tools["push"].inputSchema["required"]) == {"namespace", "question", "content"}
    invoke(manual, "memory_inspect", trail_id="a" * 32, namespace="team")
    assert recorded[-1][0] == "/memory/inspect"
    read_only = create_mcp(
        local_identity.home,
        workflow_namespace="team",
        workflow_repo=tmp_path,
        workflow_read_only=True,
    )
    assert {tool.name for tool in asyncio.run(read_only.list_tools())} == {"ask"}


@pytest.mark.parametrize(
    "tool,extra",
    [
        ("memory_inspect", {}),
        ("memory_feedback", {"verdict": "helpful"}),
        ("memory_change", {"expected_revision": 0, "action": "retract"}),
        (
            "memory_correct",
            {
                "expected_revision": 0,
                "question": "q",
                "content": "c",
                "sources": [{"ref": "file.py", "content_hash": "a" * 64}],
            },
        ),
    ],
)
def test_memory_mcp_workflow_binding_for_every_tool(
    local_identity, tmp_path, monkeypatch, tool, extra
):
    recorded = []

    def request(self, route, payload):
        recorded.append((route, payload))
        return test_memory_api.memory_acknowledgment(route, payload)

    monkeypatch.setattr(Client, "request", request)
    server = create_mcp(local_identity.home, workflow_namespace="team", workflow_repo=tmp_path)
    invoke(server, tool, trail_id="a" * 32, **extra)
    assert recorded[-1][1]["namespace"] == "team"
    before = len(recorded)
    with pytest.raises(ToolError):
        invoke(server, tool, trail_id="a" * 32, namespace="private", **extra)
    assert len(recorded) == before
    schema = next(t for t in asyncio.run(server.list_tools()) if t.name == tool).inputSchema
    assert "namespace" not in schema.get("required", [])


@pytest.mark.parametrize("malformed", ["note", "source", "namespace"])
def test_memory_mcp_sdk_validation_does_not_echo_private_values(local_identity, malformed):
    server = create_mcp(local_identity.home)
    arguments = {"trail_id": "a" * 32, "namespace": "team", "verdict": "helpful"}
    tool = "memory_feedback"
    if malformed == "note":
        arguments["note"] = {"password": "private-input-sentinel"}
    elif malformed == "namespace":
        arguments["namespace"] = {"password": "private-input-sentinel"}
    else:
        tool = "memory_correct"
        arguments = {
            "trail_id": "a" * 32,
            "namespace": "team",
            "expected_revision": 0,
            "question": "q",
            "content": "c",
            "sources": [{"ref": "file.py", "content_hash": "private-input-sentinel"}],
        }
    with pytest.raises(ToolError) as caught:
        invoke(server, tool, **arguments)
    assert "private-input-sentinel" not in str(caught.value)


def test_memory_mcp_unknown_outcome_keeps_only_validated_operation_id(local_identity, monkeypatch):
    recorded = []

    def offline(self, route, payload):
        recorded.append(payload)
        raise httpx.ReadTimeout("private-transport-sentinel")

    monkeypatch.setattr(Client, "request", offline)
    server = create_mcp(local_identity.home)
    with pytest.raises(ToolError) as caught:
        invoke(server, "memory_feedback", trail_id="a" * 32, namespace="team", verdict="helpful")
    assert recorded[0]["operation_id"] in str(caught.value)
    assert re.fullmatch("[a-f0-9]{32}", recorded[0]["operation_id"])
    assert "sentinel" not in str(caught.value)
    assert "sentinel" not in str(MemoryUnavailable("private-invalid-id-sentinel"))


def test_memory_metadata_and_unverified_feedback_warning_survive_mcp(local_identity, monkeypatch):
    metadata = {
        "state": "active",
        "revision": 1,
        "replacement_id": None,
        "feedback": {"helpful": 0, "incorrect": 1, "outdated": 0},
    }

    def request(self, route, payload):
        return {
            "hit": True,
            "id": "a" * 32,
            "content": "Unverified answer",
            "status": "unverified",
            "memory": metadata,
        }

    monkeypatch.setattr(Client, "request", request)
    blocks = invoke(create_mcp(local_identity.home), "ask", question="q")
    response = json.loads(blocks[0].text)
    assert response["memory"] == metadata
    assert "unverified" in response["memory_warning"]
    assert response["status"] == "unverified"
