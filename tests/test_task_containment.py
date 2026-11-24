"""Provider-free task-tool regressions using disposable, host-declared inventories."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent
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


def test_cached_host_listing_gets_a_fresh_plan(identity):
    server = create(identity, ["settings.py"], scope_list_tool="inventory", remaining_units=lambda: 1)
    cached = CallToolResult(content=[TextContent(type="text", text="settings.py")])

    @server.tool(structured_output=False)
    def inventory() -> CallToolResult:
        return cached

    first = blocks(server, "inventory")
    action = json.loads(first[1].text)["scope_read_plan"]
    read(server, **action["arguments"])
    second = blocks(server, "inventory")
    assert json.loads(second[1].text)["scope_read_plan"] == {"status": "complete", "listed_files": 1}
    assert len(cached.content) == 1 and "next_tool" in json.loads(first[1].text)["scope_read_plan"]
    server.evidence.close()
    unauthorized = blocks(server, "inventory")
    assert unauthorized[0].text == "settings.py"
    assert json.loads(unauthorized[1].text)["scope_read_plan"]["status"] == "unverified_scope"
    assert not server._scope.listed and not server._scope.covered


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


def test_opaque_quota_does_not_promise_a_full_batch(identity):
    server = create(identity, ["README.md", "settings.py"], read_admission=lambda: True)
    assert plan(server)["status"] == "partial"


def test_multi_batch_listing_counts_pages_after_publication_cache_fills(identity):
    _, repo, _ = identity
    paths = [f"part-{i}.txt" for i in range(10)]
    for name in paths:
        (repo / name).write_text("data")
    server = create(identity, paths)
    server.evidence._capacity = 1
    assert plan(server)["status"] == "partial"
    first = read(server, batch=[{"path": p} for p in paths[:8]])
    assert first["results"][1]["evidence_ref"] is None
    assert first["listed_scope"]["unverified_paths"] == paths[8:]
    action = plan(server)
    assert action["arguments"] == {"batch": [{"path": p} for p in paths[8:]]}
    assert read(server, **action["arguments"])["listed_scope"] == {"status": "complete", "listed_files": 10}


def test_mismatched_target_is_not_credited(identity, monkeypatch):
    server = create(identity, ["settings.py"])
    plan(server)
    actual = server._scope.version("settings.py")
    monkeypatch.setattr(server._scope, "version", lambda path: ("another-target", *actual[1:]))
    result = read(server, path="settings.py")
    assert result["content"] and result["listed_scope"]["status"] == "partial"


def test_only_delivered_trace_pages_count(identity):
    _, repo, _ = identity
    (repo / "caller.py").write_text("import settings")
    server = create(identity, ["caller.py", "settings.py"], trace_reads=True)
    plan(server)
    result = json.loads(blocks(server, "trace_read", paths=["caller.py"])[0].text)
    assert [entry["path"] for entry in result["results"]] == ["caller.py", "settings.py"]
    assert result["listed_scope"]["status"] == "complete"


@pytest.mark.parametrize("listing", ["settings.py", "settings.py\nsettings.py"])
def test_embedded_structured_listing_preserves_payload_and_adds_plan(identity, listing):
    server = create(identity, ["settings.py"], scope_list_tool="files", remaining_units=lambda: 1)

    @server.tool(structured_output=True)
    def files() -> str:
        return listing

    content, structured = asyncio.run(server.call_tool("files", {}))
    assert structured == {"result": listing} and content[0].text == listing
    action = json.loads(content[1].text)["scope_read_plan"]
    assert action["arguments"]["batch"] == [{"path": "settings.py"}]
    receipt = read(server, **action["arguments"])["listed_scope"]
    assert receipt == {"status": "complete", "listed_files": 1}
    content, _ = asyncio.run(server.call_tool("files", {}))
    assert json.loads(content[1].text)["scope_read_plan"] == receipt


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


def test_relisting_plans_only_the_undelivered_suffixes(identity):
    _, repo, _ = identity
    paths = ["README.md", "settings.py"]
    for path in paths:
        (repo / path).write_text("x" * 9500)
    server = create(identity, paths)
    assert plan(server)["status"] == "partial"
    page = read(server, batch=[{"path": p} for p in paths])
    assert page["listed_scope"]["status"] == "partial"
    action = plan(server)
    assert action["arguments"]["batch"] == [{"path": p, "offset": 8000} for p in paths]
    assert read(server, **action["arguments"])["listed_scope"]["status"] == "complete"


@pytest.mark.parametrize("altered", [False, True])
def test_host_copied_pages_need_intact_fields_for_coverage(identity, monkeypatch, altered):
    paths = {"settings.py"}
    server = create(identity, paths, read_path_allowed=paths.__contains__)
    assert plan(server)["next_tool"] == "read"
    original = server.evidence.read
    def copy(*args, **kwargs):
        page = dict(original(*args, **kwargs))
        if altered:
            page["end_line"] += 1
        return page
    monkeypatch.setattr(server.evidence, "read", copy)
    status = read(server, path="settings.py")["listed_scope"]["status"]
    assert status == ("partial" if altered else "complete")


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


@pytest.mark.parametrize("tail_text", ["x" * 26000, "水" * 8500], ids=["ascii", "utf8"])
def test_relisting_keeps_a_redacted_gap_and_plans_only_undelivered_content(identity, tail_text):
    _, repo, _ = identity
    path = repo / "example.env"
    path.write_text('password="example-only"\n' + tail_text, encoding="utf-8")
    other = "settings.py"
    server = create(identity, [path.name, other])
    assert plan(server)["status"] == "partial"
    first = read(server, path=path.name)
    action = plan(server)
    assert action["arguments"]["batch"] == [{"path": path.name, "offset": first["next_offset"]}, {"path": other}]
    tail = read(server, path=path.name, offset=first["next_offset"])
    assert tail["next_offset"] is None and tail["listed_scope"]["status"] == "partial"
    action = plan(server)
    assert action["arguments"]["batch"] == [{"path": other}]
    assert read(server, **action["arguments"])["listed_scope"]["unverified_paths"] == [path.name]
    assert plan(server)["status"] == "partial"
    path.write_text("Public settings")
    action = plan(server)
    assert action["arguments"]["batch"] == [{"path": path.name}]
    assert read(server, **action["arguments"])["listed_scope"]["status"] == "complete"


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
    ("Is the timeout in this repository applied before retry backoff?", None),
    ("Is the timeout in this repository (applied before retry backoff)?", None),
    ("Is the timeout in this repo, answer with details\nincluding the retry order", None),
    ("Where does the project define the timeout?", None),
    ("Where\ndoes the project define the timeout?", None),
    ("Why does this repository include vendored dependencies?", None),
    ("What happens if the project defines a timeout in settings.py?", None),
    ("What should I check if this repository contains a timeout?", None),
    ("Does the script check if the project defines a timeout?", None),
    ("If the project defines a timeout, explain its retry behavior.", None),
    (QUESTION, "settings.py"),
])
def test_ordinary_and_direct_routes_keep_their_tool_catalog(identity, question, direct):
    units = [True]
    server = create(identity, ["settings.py"], question, direct_path=direct,
                    read_admission=lambda: bool(units) and units.pop())
    assert {t.name for t in asyncio.run(server.list_tools())} == {"read", "research"}
    page = read(server, path="settings.py")
    assert "listed_scope" not in page and page["content"] == "TIMEOUT = 30"
    assert not units


@pytest.mark.parametrize("question", [
    "Does the project define a timeout?", "Check if the project defines a timeout.",
    "Can you check if the project defines a timeout?", "Please tell me if the repo contains a timeout.",
    "Read the policy, then verify if this repository documents the default.",
    "Explain the policy. Does this repository include a default?", "Is the timeout in the project?",
    "Is the timeout in this repository, yes or no?", "Is the timeout in this repo (please answer yes/no)?",
    "Is the timeout in this repository (answer with yes or no)?",
])
def test_containment_questions_keep_the_single_batch_route(identity, question):
    server = create(identity, ["settings.py"], question)
    assert plan(server) == {"next_tool": "read", "arguments": {"batch": [{"path": "settings.py"}]}}


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
