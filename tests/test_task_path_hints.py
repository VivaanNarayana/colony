import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_workflow import identity as identity

from colony.task_mcp import TaskMCP
from colony.workflow import Workflow


def startup(server, monkeypatch):
    @asynccontextmanager
    async def streams():
        yield None, None

    monkeypatch.setattr("colony.task_mcp.stdio_server", streams)
    monkeypatch.setattr(server._mcp_server, "run", AsyncMock())
    asyncio.run(server.run_stdio_async())
    return server.instructions


@pytest.mark.parametrize("paths", [
    [],
    ["settings.py", "missing.py"],
    ["settings.py", "../private.txt"],
    ["settings.py", 'read\nmore.py'],
    ["settings.py", "sk-abcdefghijklmnopqrstuv"],
    ["a" * 48 + ".py", "b" * 48 + ".py"],
    ["settings.py"] * 9,
    ["a" * 83 + ".py"],
])
def test_ineligible_path_hints_use_ordinary_startup(identity, monkeypatch, paths):
    client, repo, _ = identity
    for path in ("sk-abcdefghijklmnopqrstuv", "a" * 83 + ".py", "a" * 48 + ".py", "b" * 48 + ".py"):
        (repo / path).touch()
    workflow = Workflow(client, "team", repo)
    ordinary = TaskMCP(workflow, "Task", empty_memory_routing=False)
    hinted = TaskMCP(workflow, "Task", known_paths=paths, empty_memory_routing=False)
    assert startup(hinted, monkeypatch) == startup(ordinary, monkeypatch)


def test_hints_use_current_host_permissions_and_never_mint_evidence(identity, monkeypatch):
    client, repo, _ = identity
    allowed = {"settings.py", "README.md"}
    server = TaskMCP(Workflow(client, "team", repo), "Task", empty_memory_routing=False,
                     known_paths=["settings.py", "README.md"], read_path_allowed=allowed.__contains__)
    allowed.remove("README.md")
    assert "Path hints:" not in startup(server, monkeypatch)
    assert not server.evidence._observations and server._read_access.admitted == 0
    allowed.add("README.md")
    assert '["settings.py","README.md"]' in startup(server, monkeypatch)
    client.keyfile.grant.member_id = "different"
    assert "Path hints:" not in startup(server, monkeypatch)


def test_direct_path_and_opaque_host_wrapper_omit_hints(identity, monkeypatch):
    client, repo, _ = identity
    workflow = Workflow(client, "team", repo)
    for direct_path in (None, "settings.py"):
        server = TaskMCP(workflow, "Task", known_paths=["README.md"], direct_path=direct_path,
                         empty_memory_routing=False)
        if direct_path is None:
            monkeypatch.setattr(server.evidence, "read", lambda *a, **kw: pytest.fail("content read"))
        assert "Path hints:" not in startup(server, monkeypatch)


def test_binding_change_during_metadata_check_omits_hint(identity, monkeypatch):
    client, repo, _ = identity

    def permit(path):
        client.keyfile.grant.member_id = "different"
        return True

    server = TaskMCP(Workflow(client, "team", repo), "Task", known_paths=["settings.py"],
                     read_path_allowed=permit, empty_memory_routing=False)
    assert "Path hints:" not in startup(server, monkeypatch)


def test_real_stdio_can_start_with_one_batch_using_known_paths(identity):
    client, repo, _ = identity
    args = ["-m", "colony.task_mcp", "--home", str(client.home), "--repo", str(repo),
            "--ns", "team", "--question", "Check both files", "--read-only",
            "--read-unit-limit", "2", "--no-empty-memory-routing",
            "--known-path", "settings.py", "--known-path", "README.md", "--known-path", "settings.py"]
    params = StdioServerParameters(command=sys.executable, args=args,
                                   env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})

    async def check():
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            init = await session.initialize()
            assert init.instructions.startswith('Path hints: ["settings.py","README.md"]. ')
            assert "2 units total" in init.instructions
            pages = await session.call_tool("read", {"batch": [{"path": "settings.py"}, {"path": "README.md"}]})
            rows = json.loads(pages.content[0].text)["results"]
            assert [r["path"] for r in rows] == ["settings.py", "README.md"]
            assert all(r["complete_file"] and r["evidence_ref"] for r in rows)
            exhausted = await session.call_tool("read", {"path": "settings.py"})
            assert exhausted.isError

    asyncio.run(check())
