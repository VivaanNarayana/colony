"""Provider-free real HTTP/MCP transport regression; never invokes a worker model."""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_client_server_memory_streams
from test_integration import running_team as running_team
from test_workflow import identity as identity

from colony.task_mcp import ANSWER_POLICY, TaskMCP
from colony.workflow import Workflow


@pytest.mark.parametrize("state", ["direct", "empty", "present", "unknown"])
def test_mcp_initialization_delivers_routing_and_keeps_explicit_recall(identity, monkeypatch, state):
    client, repo, _ = identity
    probes, lookups = [], []
    monkeypatch.setattr(client, "memory_availability", lambda *args: probes.append(args) or state)
    miss = {"hit": False, "reason": "no_relevant_trail"}
    monkeypatch.setattr(client, "ask", lambda *args, **kwargs: lookups.append(args) or miss)
    question = "What timeout is configured?"
    direct_path = "settings.py" if state == "direct" else None
    server = TaskMCP(Workflow(client, "team", repo, read_only=True), question, direct_path=direct_path)

    async def check():
        async with (
            create_client_server_memory_streams() as (client_streams, server_streams),
            anyio.create_task_group() as group,
        ):
            @asynccontextmanager
            async def memory_stdio():
                yield server_streams

            monkeypatch.setattr("colony.task_mcp.stdio_server", memory_stdio)
            group.start_soon(server.run_stdio_async)
            try:
                async with ClientSession(*client_streams) as session:
                    init = await session.initialize()
                    routing = init.instructions.partition(ANSWER_POLICY)[0].casefold()
                    # The pinned host keeps roughly 250 characters including its ellipsis.
                    preview = routing[:247]
                    read_first = "read the specified path first" if direct_path else "batch known paths first"
                    assert preview.index(read_first) < preview.index("recall")
                    assert "batch listed scope for absence if it fits" in preview
                    assert "note gaps" in preview and "uncaught errors exit loops" in preview
                    assert "list unknown paths once" in preview
                    assert ("started empty" in preview) == (state == "empty")
                    assert ("recall only if asked" in preview) == (state == "empty")
                    assert ("to replace discovery" in preview) == (state != "empty")
                    assert "skip speculative lookups once sources suffice" in init.instructions.casefold()
                    assert len(probes) == (0 if state == "direct" else 1)

                    tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                    assert set(tools) == {"read", "research"}
                    description = tools["research"].description.casefold()
                    assert all(term in description for term in ("requested recall", "verify", "current"))
                    assert ("empty at last check" in description) == (state == "empty")
                    assert ("would replace discovery" in description) == (state != "empty")
                    read_description = tools["read"].description.casefold()
                    assert all(term in read_description for term in (
                        "read its definition", "caller", "config", "operation", "unbound backend",
                        "calls", "additional retries", "returns", "uncaught exception propagation", "remaining read units", "bytes",
                        "list unknown paths once",
                    ))

                    args = {} if direct_path else {"path": "settings.py"}
                    read = await session.call_tool("read", args)
                    assert not read.isError and not lookups
                    page = json.loads(read.content[0].text)
                    assert page["content"] == "TIMEOUT = 30" and page["complete_file"]
                    assert "lookup_if" not in page and "next_offset" not in page

                    recall = await session.call_tool("research", {})
                    assert not recall.isError and json.loads(recall.content[0].text) == miss
                    assert lookups == [(question, "team")]
            finally:
                group.cancel_scope.cancel()

    asyncio.run(asyncio.wait_for(check(), timeout=10))


def test_live_empty_hint_bundle_quota_and_readonly(running_team):
    _, homes, repo, _ = running_team
    (repo / "handler.py").write_text("import settings\nimport missing\n")

    async def check():
        args = [
            "-m",
            "colony.task_mcp",
            "--home",
            str(homes["b"]),
            "--repo",
            str(repo),
            "--ns",
            "team",
            "--question",
            "Trace handler",
            "--trace-reads",
            "--read-only",
            "--read-unit-limit",
            "2",
        ]
        params = StdioServerParameters(
            command=sys.executable,
            args=args,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            init = await session.initialize()
            assert "started empty" in init.instructions
            assert "Recall only if asked" in init.instructions
            assert "allowance: 2 units total" in init.instructions
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert set(tools) == {"read", "research", "trace_read"}
            assert "empty at last check" in tools["research"].description
            result = await session.call_tool("trace_read", {"paths": ["handler.py", "settings.py", "missing.py"]})
            assert not result.isError
            value = json.loads(result.content[0].text)
            assert value["read_accounting"] == {"admitted": 2, "denied": 1, "succeeded": 2}
            assert "publication_guidance" not in value
            assert value["results"][2]["evidence_ref"] is None
            denied = await session.call_tool("read", {"path": "settings.py"})
            assert denied.isError

    asyncio.run(check())
