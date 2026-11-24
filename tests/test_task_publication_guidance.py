import asyncio
import inspect
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_workflow import identity as identity

from colony.task_mcp import (
    ANSWER_POLICY,
    PUBLICATION_CUE,
    PUBLICATION_POLICY,
    READ_ROUTING,
    TaskMCP,
)
from colony.workflow import Workflow


@pytest.mark.parametrize("adaptive", [False] + (
    [True] if "context_budget" in inspect.signature(TaskMCP).parameters else []))
@pytest.mark.parametrize("direct,remember,readonly,eligible", [
    (False, False, False, True), (True, False, False, False),
    (True, True, False, True), (False, True, True, False),
])
def test_policy_delivered_once_over_stdio(identity, direct, remember, readonly, eligible, adaptive):
    client, repo, _ = identity

    async def check():
        args = ["-m", "colony.task_mcp", "--home", str(client.home), "--repo", str(repo),
                "--ns", "team", "--question", "Explain settings"]
        if adaptive:
            args += ["--context-budget", "3072", "--context-encoding", "utf8_bytes"]
        if direct:
            args += ["--direct-path", "settings.py"]
        if remember:
            args += ["--remember"]
        if readonly:
            args += ["--read-only"]
        params = StdioServerParameters(command=sys.executable, args=args,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            init = await session.initialize()
            assert init.instructions.count(ANSWER_POLICY) == 1
            assert "Include session evidence_refs in answers only on request" in init.instructions
            assert "When the required answer is one JSON object or array" in init.instructions
            assert "stop at the matching closing delimiter" in init.instructions
            assert init.instructions.count(READ_ROUTING) == 1
            assert init.instructions.count(PUBLICATION_POLICY) == int(eligible)
            assert "publish" not in {t.name for t in (await session.list_tools()).tools}
            result = await session.call_tool("read", {"path": "settings.py"})
            assert not result.isError
            assert PUBLICATION_POLICY not in result.content[0].text
            page = json.loads(result.content[0].text)
            assert page.get("publication_guidance") == (PUBLICATION_CUE if eligible else None)
            tools = (await session.list_tools()).tools
            read = next(t for t in tools if t.name == "read")
            assert "Failed entries and partial pages leave coverage gaps" in read.description
            assert ("publish" in {t.name for t in tools}) == eligible
            if eligible:
                tool = next(t for t in tools if t.name == "publish")
                assert set(tool.inputSchema["required"]) == {"content", "applicability", "evidence_refs"}
                assert "question" not in tool.inputSchema["properties"]
                assert "4,000 characters" in tool.description
                assert "Before final" in tool.description
            # Policy delivery cannot trigger a publication or limit later research.
            later = await session.call_tool("read", {"path": "README.md"})
            assert not later.isError
            assert "publication_guidance" not in json.loads(later.content[0].text)
    asyncio.run(check())


def test_example_supports_multiple_sources_and_never_caps_reads(identity, monkeypatch):
    client, repo, _ = identity
    sent = []
    monkeypatch.setattr(client, "push", lambda trail, **kw: sent.append(trail) or {"stored": True})
    server = TaskMCP(Workflow(client, "team", repo), "Original question")
    def call(name, args):
        return json.loads(asyncio.run(server.call_tool(name, args))[0].text)
    refs = [call("read", {"path": p})["evidence_ref"] for p in ("settings.py", "README.md")]
    assert not sent
    result = call("publish", {"content": "A scoped finding", "applicability": "Repository settings",
                              "evidence_refs": refs, "kind": "behavior"})
    assert result["stored"] and len(sent) == 1
    assert {s.ref for s in sent[0].sources} == {"settings.py", "README.md"}
    assert sent[0].question == "Original question" and sent[0].tainted
    assert call("read", {"path": "settings.py"})["content"]


def test_guidance_has_bounded_once_per_eligible_task_cost():
    assert len(PUBLICATION_POLICY.encode("utf-8")) < 1000
    assert "omit unsolicited abstention reports" in PUBLICATION_POLICY
    assert "research solely to publish" in PUBLICATION_POLICY
    assert "complete answer" in PUBLICATION_POLICY


@pytest.mark.parametrize("change", [False, True])
def test_publication_pins_named_delivered_scope_files(identity, monkeypatch, change):
    client, repo, _ = identity
    sent = []
    monkeypatch.setattr(client, "push", lambda trail, **kw: sent.append(trail) or {"stored": True})
    server = TaskMCP(Workflow(client, "team", repo), "Trace the configured behavior")
    def call(name, **args):
        return json.loads(asyncio.run(server.call_tool(name, args))[0].text)

    (repo / "notes.md").write_text("Other background")
    (repo / "unseen.py").write_text("Not observed")
    pages = call("read", batch=[{"path": p} for p in ("settings.py", "README.md", "notes.md")])["results"]
    if change:
        (repo / "settings.py").write_text("TIMEOUT = 80")
    result = call("publish", applicability="settings.py, unseen.py and sub/notes.md",
                  content="Behavior of the configured application.", evidence_refs=[pages[1]["evidence_ref"]])
    assert result["stored"] is (not change)
    if change:
        assert not sent
    else:
        assert {s.ref for s in sent[0].sources} == {"settings.py", "README.md"}


@pytest.mark.parametrize("scope", [
    "`docs/release notes.md`", "`docs/release notes.md`,settings.py",
    "settings.py;`docs/release notes.md`", "(`docs/release notes.md`),`settings.py`",
])
def test_quoted_path_does_not_pin_an_unrelated_suffix(identity, monkeypatch, scope):
    client, repo, _ = identity
    sent = []
    monkeypatch.setattr(client, "push", lambda trail, **kw: sent.append(trail) or {"stored": True})
    server = TaskMCP(Workflow(client, "team", repo), "Trace configuration")
    (repo / "docs").mkdir()
    for path in ("docs/release notes.md", "notes.md"):
        (repo / path).write_text("Current notes")
    async def check():
        paths = ["settings.py", "docs/release notes.md", "notes.md"]
        reply = await server.call_tool("read", {"batch": [{"path": p} for p in paths]})
        ref = json.loads(reply[0].text)["results"][0]["evidence_ref"]
        (repo / "notes.md").write_text("Unrelated change")
        reply = await server.call_tool("publish", {"content": "finding", "applicability": scope,
                                                   "evidence_refs": [ref]})
        assert json.loads(reply[0].text)["stored"]
    asyncio.run(check())
    assert {s.ref for s in sent[0].sources} == {"settings.py", "docs/release notes.md"}


def test_scope_markup_cannot_attach_a_delivered_prefix_or_closing_punctuation():
    from colony.evidence_refs import _scope_paths

    observed = {"release notes.md", "`release", ")", "settings.py"}
    for scope in ("`release notes.md`, settings.py", "(`release notes.md`,); settings.py"):
        assert _scope_paths(scope, observed, set()) & observed == {"release notes.md", "settings.py"}
    assert _scope_paths("`)`", observed, set()) & observed == {")"}


@pytest.mark.parametrize("unavailable", ["full", "expired"])
def test_named_delivered_dependency_requires_a_live_handle(identity, monkeypatch, unavailable):
    from dataclasses import replace

    client, repo, _ = identity
    monkeypatch.setattr(client, "push", lambda *a, **k: pytest.fail("incomplete dependency set"))
    server = TaskMCP(Workflow(client, "team", repo), "Trace configuration")
    server.evidence._capacity = 1 if unavailable == "full" else 2
    async def read(path):
        reply = await server.call_tool("read", {"path": path})
        return json.loads(reply[0].text)["evidence_ref"]
    async def check():
        if unavailable == "expired":
            old = await read("README.md")
            obs = server.evidence._observations
            obs[old] = replace(obs[old], expires=0)
        ref = await read("settings.py")
        if unavailable == "full":
            assert await read("README.md") is None
        else:
            assert old not in server.evidence._observations
        (repo / "README.md").write_text("Changed dependency")
        reply = await server.call_tool("publish", {"content": "finding", "applicability": "README.md",
                                                   "evidence_refs": [ref]})
        assert not json.loads(reply[0].text)["stored"]
    asyncio.run(check())


@pytest.mark.parametrize("filename", ["Makefile", "retry~legacy.py", "release`notes`.md", "`notes`.md"])
def test_copied_page_pins_exact_bare_name(identity, monkeypatch, filename):
    client, repo, _ = identity
    (repo / filename).write_text("configured")
    sent = []
    monkeypatch.setattr(client, "push", lambda trail, **kw: sent.append(trail) or {"stored": True})
    server = TaskMCP(Workflow(client, "team", repo), "Explain behavior")
    read = server.evidence.read
    monkeypatch.setattr(server.evidence, "read", lambda *a, **kw: dict(read(*a, **kw)))
    async def check():
        await server.call_tool("read", {"path": filename})
        reply = await server.call_tool("read", {"path": "settings.py"})
        ref = json.loads(reply[0].text)["evidence_ref"]
        reply = await server.call_tool("publish", {"content": "finding", "applicability": f"Under {filename}.",
                                                   "evidence_refs": [ref]})
        assert json.loads(reply[0].text)["stored"]
    asyncio.run(check())
    assert {s.ref for s in sent[0].sources} == {"settings.py", filename}


def test_handleless_version_conflict_survives_restoration(identity, monkeypatch):
    client, repo, _ = identity
    server = TaskMCP(Workflow(client, "team", repo), "Explain behavior")
    server.evidence._capacity = 2
    monkeypatch.setattr(client, "push", lambda *a, **kw: pytest.fail("ambiguous dependency"))
    async def read(path):
        reply = await server.call_tool("read", {"path": path})
        return json.loads(reply[0].text)["evidence_ref"]
    async def check():
        file = repo / "README.md"
        file.write_text("version A")
        ref = await read("settings.py")
        assert await read("README.md")
        file.write_text("version B")
        assert await read("README.md") is None
        file.write_text("version A")
        reply = await server.call_tool("publish", {"content": "finding", "applicability": "README.md",
                                                   "evidence_refs": [ref]})
        assert not json.loads(reply[0].text)["stored"]
    asyncio.run(check())


@pytest.mark.parametrize("failure", ["missing_evidence", "unknown_publication_outcome"])
def test_requested_status_explanations_survive_failure_without_extra_calls(identity, monkeypatch, failure):
    from mcp.server.fastmcp.exceptions import ToolError

    client, repo, _ = identity
    attempts = []

    def unavailable(trail, **kwargs):
        attempts.append(trail)
        raise TimeoutError("private transport detail")

    monkeypatch.setattr(client, "push", unavailable)
    server = TaskMCP(Workflow(client, "team", repo),
                     "Remember the timeout; explain whether it was saved and why if not.",
                     direct_path="settings.py", remember=True)

    def call(name, args):
        return json.loads(asyncio.run(server.call_tool(name, args))[0].text)

    assert "Explain requested publication status and failures truthfully" in server.instructions
    assert "omit unsolicited abstention reports" in server.instructions
    assert "No abstention message" not in server.instructions
    assert "No decision call, automatic retry or publication-success gate" in server.instructions
    assert server.instructions.count(ANSWER_POLICY) == 1
    if failure == "missing_evidence":
        with pytest.raises(ToolError):
            call("read", {"path": "missing.py"})
        assert not attempts
    else:
        reference = call("read", {})["evidence_ref"]
        result = call("publish", {"content": "The timeout is 30.", "applicability": "settings.py",
                                  "evidence_refs": [reference], "kind": "value"})
        assert result["answer_unblocked"]
        assert result["reason"] == "publication_failed_or_outcome_unknown"
        assert "private transport detail" not in str(result)
        assert len(attempts) == 1
    # Necessary research remains usable; neither guidance nor failure starts a retry.
    assert call("read", {"path": "README.md"})["content"]
    assert len(attempts) == int(failure == "unknown_publication_outcome")
