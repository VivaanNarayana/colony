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

from colony.task_mcp import ANSWER_POLICY, PUBLICATION_POLICY, TaskMCP
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
            assert init.instructions.count(PUBLICATION_POLICY) == int(eligible)
            assert "publish" not in {t.name for t in (await session.list_tools()).tools}
            result = await session.call_tool("read", {"path": "settings.py"})
            assert not result.isError
            assert PUBLICATION_POLICY not in result.content[0].text
            tools = (await session.list_tools()).tools
            assert ("publish" in {t.name for t in tools}) == eligible
            if eligible:
                tool = next(t for t in tools if t.name == "publish")
                assert set(tool.inputSchema["required"]) == {"content", "applicability", "evidence_refs"}
                assert "question" not in tool.inputSchema["properties"]
                assert "4,000 characters" in tool.description
                assert "Before final" in tool.description
            # Policy delivery cannot trigger a publication or limit later research.
            assert not (await session.call_tool("read", {"path": "README.md"})).isError
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
    assert "No abstention message" in PUBLICATION_POLICY
    assert "research solely to publish" in PUBLICATION_POLICY
    assert "complete answer" in PUBLICATION_POLICY
