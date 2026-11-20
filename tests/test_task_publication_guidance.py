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
            assert init.instructions.count(READ_ROUTING) == 1
            assert init.instructions.count(PUBLICATION_POLICY) == int(eligible)
            assert "publish" not in {t.name for t in (await session.list_tools()).tools}
            result = await session.call_tool("read", {"path": "settings.py"})
            assert not result.isError
            assert PUBLICATION_POLICY not in result.content[0].text
            page = json.loads(result.content[0].text)
            assert page.get("publication_guidance") == (PUBLICATION_CUE if eligible else None)
            if direct and remember and eligible:
                cue = page["publication_guidance"]
                assert 'When asked to remember a value, publish it as kind="value"' in cue
                assert "For behavior, skip restatements" in cue
            tools = (await session.list_tools()).tools
            read = next(t for t in tools if t.name == "read")
            assert "next_offset" in read.description and "denied" in read.description
            assert ("publish" in {t.name for t in tools}) == eligible
            if eligible:
                tool = next(t for t in tools if t.name == "publish")
                assert set(tool.inputSchema["required"]) == {"content", "applicability", "evidence_refs"}
                assert "question" not in tool.inputSchema["properties"]
                assert "4,000 characters" in tool.description
                assert "supporting evidence_refs from successful reads" in tool.description
                assert 'kind="value"' in tool.description
            # Policy delivery cannot trigger a publication or limit later research.
            later = await session.call_tool("read", {"path": "README.md"})
            assert not later.isError
            assert "publication_guidance" not in json.loads(later.content[0].text)
    asyncio.run(check())


def test_publish_can_store_condition_and_resolved_behavior_from_multiple_reads(identity, monkeypatch):
    client, repo, _ = identity
    (repo / "sender.py").write_text("from settings import TIMEOUT\ndef send(client):\n    client.flush(TIMEOUT)")
    sent = []
    monkeypatch.setattr(client, "push", lambda trail, **kw: sent.append(trail) or {"stored": True})
    server = TaskMCP(Workflow(client, "team", repo), "Original question")
    def call(name, args):
        return json.loads(asyncio.run(server.call_tool(name, args))[0].text)
    read = call("read", {"batch": [{"path": "settings.py"}, {"path": "sender.py"}]})
    assert "condition and concrete outcome" in read["publication_guidance"]
    assert "resolved settings" in read["publication_guidance"]
    refs = [entry["evidence_ref"] for entry in read["results"]]
    assert not sent
    result = call("publish", {"content": "The sender passes 30 to flush.", "applicability": "When send(client) runs",
                              "evidence_refs": refs, "kind": "behavior"})
    assert result["stored"] and len(sent) == 1
    assert sent[0].content == "When send(client) runs\nThe sender passes 30 to flush."
    assert {s.ref for s in sent[0].sources} == {"settings.py", "sender.py"}
    assert sent[0].question == "Original question" and sent[0].tainted
    assert call("read", {"path": "settings.py"})["content"]


def test_guidance_has_bounded_once_per_eligible_task_cost():
    assert len(PUBLICATION_POLICY.encode("utf-8")) < 1000
    assert "omit unsolicited abstention reports" in PUBLICATION_POLICY
    assert "research solely to publish" in PUBLICATION_POLICY
    assert "complete answer" in PUBLICATION_POLICY


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
