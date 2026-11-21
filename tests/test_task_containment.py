"""Provider-free task-tool regressions using disposable, host-declared inventories."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_workflow import identity as identity

from colony.task_mcp import TaskMCP
from colony.workflow import Workflow

QUESTION = "State whether this repository contains the pressure measurement."


def blocks(server, tool, **args):
    result = asyncio.run(server.call_tool(tool, args))
    return result.content if hasattr(result, "content") else result


def read(server, **args):
    return json.loads(blocks(server, "read", **args)[0].text)


def plan(server):
    return json.loads(blocks(server, "source_files")[1].text)["scope_read_plan"]


def create(identity, paths, question=QUESTION, **options):
    client, repo, _ = identity
    return TaskMCP(Workflow(client, "team", repo, read_only=True), question,
                   scope_files=paths, **options)


@pytest.mark.parametrize("last", ["The gauge registered ninety kilopascals.", "No sensor attached."])
def test_five_files_fit_in_one_charged_batch_including_last_file(identity, last):
    _, repo, _ = identity
    paths = [f"part{i}.txt" for i in range(5)]
    for path in paths:
        (repo / path).write_text("Equipment inventory.")
    (repo / paths[-1]).write_text(last)
    units = list(range(6))
    server = create(identity, paths, read_admission=lambda: bool(units) and units.pop() >= 0,
                    remaining_units=lambda: len(units))
    action = plan(server)
    assert action == {"next_tool": "read", "arguments": {"batch": [{"path": p} for p in paths]}}
    result = read(server, **action["arguments"])
    assert result["listed_scope"] == {"status": "complete", "listed_files": 5}
    assert result["results"][-1]["content"] == last
    assert all(entry["evidence_ref"] for entry in result["results"])
    assert not units


def test_allowance_denial_preserves_gaps_and_research_can_find_positive_evidence(identity, monkeypatch):
    client, _, _ = identity
    paths = ["README.md", "settings.py"]
    units = [True, True]
    server = create(identity, paths, read_admission=lambda: bool(units) and units.pop(),
                    remaining_units=lambda: len(units))
    monkeypatch.setattr(client, "ask", lambda *a, **k: {"hit": True, "answer": "current pointer"})
    assert json.loads(blocks(server, "research")[0].text)["hit"]
    assert plan(server)["status"] == "partial"
    result = read(server, batch=[{"path": p} for p in paths])
    assert result["results"][1]["error"]
    assert result["listed_scope"]["unverified_paths"] == ["settings.py"]


def test_coverage_only_joins_contiguous_pages_of_the_same_file_version(identity):
    _, repo, _ = identity
    path = repo / "long.txt"
    path.write_text("a" * 17000)
    server = create(identity, [path.name])
    assert plan(server)["status"] == "partial"
    page = read(server, path=path.name)
    assert page["listed_scope"]["status"] == "partial"
    path.write_text("b" * 17001)
    tail = read(server, path=path.name, offset=page["next_offset"])
    assert tail["listed_scope"]["status"] == "partial"
    page = read(server, path=path.name)
    tail = read(server, path=path.name, offset=page["next_offset"])
    assert tail["listed_scope"]["status"] == "complete"


def test_redaction_revocation_and_changes_invalidate_coverage(identity):
    _, repo, _ = identity
    paths = ["README.md", "settings.py"]
    allowed = set(paths)
    server = create(identity, paths, read_path_allowed=allowed.__contains__)
    assert read(server, **plan(server)["arguments"])["listed_scope"]["status"] == "complete"
    (repo / paths[0]).write_text('password="never-disclose"')
    result = read(server, path=paths[0])
    assert "never-disclose" not in str(result)
    assert result["listed_scope"]["unverified_paths"] == [paths[0]]
    allowed.remove(paths[0])
    result = read(server, path=paths[1])
    assert result["listed_scope"] == {
        "status": "partial", "listed_files": 2, "unverified_count": 1, "unverified_paths": [],
    }
    assert plan(server)["status"] == "unverified_scope"


def test_incomplete_inventory_and_range_do_not_establish_scope_coverage(identity):
    _, repo, _ = identity
    paths = ["README.md", "settings.py"]
    server = create(identity, [*paths, "missing"])
    assert plan(server)["status"] == "unverified_scope"
    server = create(identity, paths)
    (repo / paths[0]).write_text("one\ntwo\nthree")
    plan(server)
    assert read(server, path=paths[0], start=2)["listed_scope"]["unverified_paths"] == paths
    assert read(server, path=paths[1])["listed_scope"]["unverified_paths"] == [paths[0]]


@pytest.mark.parametrize("question,direct", [
    ("Explain how the limit is applied.", None), ("What is the timeout in this repo?", None),
    (QUESTION, "settings.py"),
])
def test_ordinary_and_direct_routes_keep_their_tool_catalog(identity, question, direct):
    server = create(identity, ["settings.py"], question, direct_path=direct)
    assert {t.name for t in asyncio.run(server.list_tools())} == {"read", "research"}
    assert "listed_scope" not in read(server, path="settings.py")


def test_cli_inventory_runs_over_stdio_and_restricts_unlisted_reads(identity):
    client, repo, _ = identity

    async def exercise():
        args = ["-m", "colony.task_mcp", "--home", str(client.home), "--repo", str(repo),
                "--ns", "team", "--question", QUESTION, "--scope-file", "settings.py",
                "--read-unit-limit", "3", "--no-empty-memory-routing", "--read-only"]
        params = StdioServerParameters(command=sys.executable, args=args, env={
            **os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        })
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            assert "source_files" in (await session.initialize()).instructions
            listed = await session.call_tool("source_files", {})
            action = json.loads(listed.content[1].text)["scope_read_plan"]
            reply = await session.call_tool(action["next_tool"], action["arguments"])
            assert json.loads(reply.content[0].text)["listed_scope"]["status"] == "complete"
            assert (await session.call_tool("read", {"path": "README.md"})).isError

    asyncio.run(exercise())
