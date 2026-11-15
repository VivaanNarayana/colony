import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_integration import running_team as running_team
from test_task_routing import call
from test_workflow import identity as identity

from colony.client import Client
from colony.task_mcp import TaskMCP
from colony.workflow import Workflow


def names(server):
    return {t.name for t in asyncio.run(server.list_tools())}


@pytest.mark.parametrize("direct,remember,readonly,eligible", [
    (True, False, False, False), (True, True, False, True),
    (False, False, False, True), (False, True, True, False),
])
def test_discovery_and_invocation_are_both_gated(identity, monkeypatch, direct, remember, readonly, eligible):
    client, repo, _ = identity
    sent = []
    monkeypatch.setattr(client, "push", lambda t, **kw: sent.append(t) or {"stored": True})
    server = TaskMCP(Workflow(client, "team", repo, read_only=readonly), "Question",
                     direct_path="settings.py" if direct else None, remember=remember)
    args = {"content": "The default is 30", "applicability": "This repository", "evidence_refs": []}
    assert "publish" not in names(server)
    assert not call(server, "publish", args)["stored"]
    read = call(server, "read", {"path": "settings.py"})
    assert ("publish" in names(server)) == eligible
    args["evidence_refs"] = [read["evidence_ref"]]
    assert call(server, "publish", args)["stored"] == eligible
    assert len(sent) == int(eligible)
    assert "publish" not in names(server)
    assert call(server, "read", {"path": "README.md"})["content"]


def test_unchanged_lookup_does_not_unlock_publication(identity, monkeypatch):
    client, repo, _ = identity
    monkeypatch.setattr(client, "ask", lambda *a, **kw: {"hit": True, "content": "answer"})
    server = TaskMCP(Workflow(client, "team", repo), "Question")
    assert call(server, "research", {})["hit"]
    assert "publish" not in names(server)


def test_value_needs_explicit_remember_and_failure_never_blocks_reads(identity, monkeypatch):
    client, repo, _ = identity
    server = TaskMCP(Workflow(client, "team", repo), "Question")
    ref = call(server, "read", {"path": "settings.py"})["evidence_ref"]
    args = {"content": "30", "applicability": "settings", "evidence_refs": [ref], "kind": "value"}
    assert call(server, "publish", args)["reason"] == "publication_not_eligible"
    monkeypatch.setattr(client, "push", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("private")))
    args["kind"] = "behavior"
    result = call(server, "publish", args)
    assert result["answer_unblocked"] and "private" not in str(result)
    assert "publish" not in names(server)
    assert call(server, "read", {"path": "settings.py"})["content"]
    assert call(server, "publish", args)["reason"] == "publication_not_eligible"


def test_changed_evidence_and_invented_references_never_reach_storage(identity, monkeypatch):
    client, repo, _ = identity
    sent = []
    monkeypatch.setattr(client, "push", lambda *a, **kw: sent.append(a))
    server = TaskMCP(Workflow(client, "team", repo), "Question")
    ref = call(server, "read", {"path": "settings.py"})["evidence_ref"]
    (repo / "settings.py").write_text("TIMEOUT = 90\n")
    assert call(server, "publish", {"content": "30", "applicability": "settings",
                                   "evidence_refs": [ref]})["answer_unblocked"]
    assert not sent


def test_concurrent_publication_attempts_send_once(identity, monkeypatch):
    client, repo, _ = identity
    sent = []
    monkeypatch.setattr(client, "push", lambda t, **kw: sent.append(t) or {"stored": True})
    server = TaskMCP(Workflow(client, "team", repo), "Trace behavior")
    ref = call(server, "read", {"path": "settings.py"})["evidence_ref"]
    args = {"content": "The configuration supplies the timeout.", "applicability": "settings.py",
            "evidence_refs": [ref]}
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: call(server, "publish", args), range(8)))
    assert len(sent) == sum(r["stored"] for r in replies) == 1


def test_failed_read_and_foreign_reference_cannot_publish(identity, monkeypatch):
    from mcp.server.fastmcp.exceptions import ToolError

    client, repo, _ = identity
    sent = []
    monkeypatch.setattr(client, "push", lambda t, **kw: sent.append(t))
    first = TaskMCP(Workflow(client, "team", repo), "Task one")
    second = TaskMCP(Workflow(client, "team", repo), "Task two")
    foreign = call(first, "read", {"path": "settings.py"})["evidence_ref"]
    with pytest.raises(ToolError):
        call(second, "read", {"path": "missing.py"})
    assert "publish" not in names(second)
    call(second, "read", {"path": "README.md"})
    result = call(second, "publish", {"content": "claim", "applicability": "scope",
                                      "evidence_refs": [foreign]})
    assert not result["stored"] and not sent


def test_direct_catalog_is_smaller_and_has_no_publication_policy(identity):
    client, repo, _ = identity
    direct = TaskMCP(Workflow(client, "team", repo), "Read", direct_path="settings.py")
    full = TaskMCP(Workflow(client, "team", repo), "Trace behavior")
    call(direct, "read", {})
    call(full, "read", {"path": "settings.py"})
    def catalog(s):
        return json.dumps([t.model_dump() for t in asyncio.run(s.list_tools())])
    assert len(catalog(direct).encode()) < len(catalog(full).encode())
    assert "evidence_refs" not in catalog(direct)
    assert "Complete every requested deliverable" in direct.instructions


def test_real_stdio_discovery_reference_publication_and_lookup(running_team):
    _, homes, repo, _ = running_team

    async def exercise():
        parameters = StdioServerParameters(command=sys.executable, args=[
            "-m", "colony.task_mcp", "--home", str(homes["a"]), "--repo", str(repo),
            "--ns", "team", "--question", "Remember the timeout", "--direct-path", "settings.py",
            "--remember",
        ], env=dict(os.environ))
        async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as s:
            init = await s.initialize()
            assert init.capabilities.tools.listChanged
            assert {t.name for t in (await s.list_tools()).tools} == {"read", "research"}
            result = await s.call_tool("read", {})
            observed = json.loads(result.content[0].text)
            assert not result.isError and observed["content"] == "TIMEOUT = 30"
            assert {t.name for t in (await s.list_tools()).tools} == {"read", "research", "publish"}
            result = await s.call_tool("publish", {"content": "The timeout is 30.",
                "applicability": "This settings.py snapshot", "kind": "value",
                "evidence_refs": [observed["evidence_ref"]]})
            assert not result.isError and json.loads(result.content[0].text)["stored"]
            assert {t.name for t in (await s.list_tools()).tools} == {"read", "research"}

    asyncio.run(exercise())
    saved = Client(homes["b"]).ask("Remember the timeout", "team")
    assert saved["hit"] and saved["status"] == "validated" and saved["tainted"]
    assert saved["sources"][0]["ref"] == "settings.py"
