import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import Mock

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_client_server_memory_streams
from test_workflow import identity as identity

from colony.task_mcp import TaskMCP
from colony.validation import repo_file
from colony.workflow import Workflow


def startup(server, monkeypatch, before_initialize=None):
    async def handshake():
        async with (
            create_client_server_memory_streams() as (client_streams, server_streams),
            anyio.create_task_group() as group,
        ):
            started = anyio.Event()

            @asynccontextmanager
            async def streams():
                started.set()
                yield server_streams

            monkeypatch.setattr("colony.task_mcp.stdio_server", streams)
            group.start_soon(server.run_stdio_async)
            try:
                async with ClientSession(*client_streams) as session:
                    await started.wait()
                    if before_initialize is not None:
                        before_initialize()
                    return (await session.initialize()).instructions
            finally:
                group.cancel_scope.cancel()

    return asyncio.run(handshake())


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


def test_path_hint_does_not_sample_a_custom_policy_or_consume_read_units(identity, monkeypatch):
    client, repo, _ = identity
    allowed = {"settings.py", "README.md"}
    permit = Mock(wraps=allowed.__contains__)
    server = TaskMCP(Workflow(client, "team", repo), "Task", empty_memory_routing=False,
                     known_paths=["settings.py", "README.md"], read_path_allowed=permit)
    instructions = startup(server, monkeypatch)
    assert all(name not in instructions for name in allowed)
    permit.assert_not_called()
    assert not server.evidence._observations and server._read_access.admitted == 0


def test_authorized_names_are_only_in_the_initialize_reply(identity, monkeypatch):
    client, repo, _ = identity
    server = TaskMCP(Workflow(client, "team", repo), "Task", known_paths=["README.md"],
                     empty_memory_routing=False)
    instructions = startup(server, monkeypatch)
    assert "README.md" not in server.instructions
    assert instructions == 'Path hints: ["README.md"]. ' + server.instructions
    assert not server.evidence._observations and server._read_access.admitted == 0


def test_direct_path_and_opaque_host_wrapper_omit_hints(identity, monkeypatch):
    client, repo, _ = identity
    workflow = Workflow(client, "team", repo)
    for direct_path in (None, "settings.py"):
        server = TaskMCP(workflow, "Task", known_paths=["README.md"], direct_path=direct_path,
                         empty_memory_routing=False)
        if direct_path is None:
            monkeypatch.setattr(server.evidence, "read", lambda *a, **kw: pytest.fail("content read"))
        assert "Path hints:" not in startup(server, monkeypatch)


@pytest.mark.parametrize("revoke", ["first_path", "task_binding"])
def test_revocation_during_validation_withholds_the_entire_hint(identity, monkeypatch, revoke):
    client, repo, _ = identity
    server = TaskMCP(Workflow(client, "team", repo), "Task", known_paths=["README.md", "settings.py"],
                     empty_memory_routing=False)
    checked = []

    def check_file(root, path, **kwargs):
        result = repo_file(root, path, **kwargs)
        checked.append(path)
        if path == "settings.py":
            if revoke == "first_path":
                server.evidence.path_allowed = {"settings.py"}.__contains__
            else:
                client.keyfile.grant.member_id = "different"
        return result

    monkeypatch.setattr("colony.task_reads.repo_file", check_file)
    instructions = startup(server, monkeypatch)
    assert checked == ["README.md", "settings.py"]
    assert "README.md" not in instructions and "settings.py" not in instructions


@pytest.mark.parametrize("revoke", ["first_path", "task_binding"])
def test_revocation_after_stdio_start_before_initialize_withholds_hint(identity, monkeypatch, revoke):
    client, repo, _ = identity
    workflow = Workflow(client, "team", repo)
    ordinary = startup(TaskMCP(workflow, "Task", empty_memory_routing=False), monkeypatch)
    server = TaskMCP(workflow, "Task", known_paths=["README.md", "settings.py"], empty_memory_routing=False)

    def before_initialize():
        if revoke == "first_path":
            server.evidence.path_allowed = {"settings.py"}.__contains__
        else:
            client.keyfile.grant.member_id = "different"

    assert startup(server, monkeypatch, before_initialize) == ordinary
    assert server.instructions == ordinary
    assert server._read_access.admitted == 0 and not server.evidence._observations


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
