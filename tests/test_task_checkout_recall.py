import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP
from test_compact_delivery import transport as transport

from colony.context_budget import count_response
from colony.models import ContextBudget, Trail
from colony.task_mcp import TaskMCP
from colony.validation import snapshot
from colony.workflow import Workflow


def call(task, tool, **arguments):
    return json.loads(asyncio.run(task.call_tool(tool, arguments))[0].text)


def seed(client, repo, question, paths):
    trail = Trail(id="e" * 32, namespace="team", question=question, content="The configured timeout is 30.",
                  sources=[snapshot(repo, p) for p in paths])
    assert client.push(trail)["stored"]


@pytest.mark.parametrize("adaptive", [False, True])
@pytest.mark.parametrize("change", ["different", "missing", "denied", "same"])
def test_server_validated_hit_is_bound_to_task_checkout(transport, adaptive, change):
    client, server_repo, _, lookups = transport
    checkout = server_repo.parent / "caller"
    checkout.mkdir()
    paths = ["settings.py", "caller.py", "policy.py"]
    for i, path in enumerate(paths):
        source = "TIMEOUT = 30" if i == 0 else f"VALUE = {i}"
        (server_repo / path).write_text(source)
        (checkout / path).write_text(source)
    if change == "different":
        (checkout / "settings.py").write_text("TIMEOUT = 90")
    elif change == "missing":
        (checkout / "policy.py").unlink()
    question = "What timeout is configured?"
    seed(client, server_repo, question, paths)
    allowed = (lambda path: path != "policy.py") if change == "denied" else None
    budget = ContextBudget(encoding="utf8_bytes", max_tokens=3072) if adaptive else None
    task = TaskMCP(Workflow(client, "team", checkout), question, context_budget=budget, read_path_allowed=allowed)
    result = call(task, "research")
    assert len(lookups) == 1 and lookups[0]["status"] == "validated"
    assert task._read_access.admitted == 0
    assert "publish" not in {t.name for t in asyncio.run(task.list_tools())}
    if budget:
        assert result["context_budget"]["used"] == count_response(result, budget) <= budget.max_tokens
    if change == "same":
        assert result["hit"] and result["status"] == "validated"
    else:
        assert result["hit"] is False and "TIMEOUT = 30" not in str(result)
        assert not {"observed_lines", "evidence", "current_repository_reads", "sources", "content"} & result.keys()
        if change == "different":
            page = call(task, "read", path="settings.py")
            assert page["content"] == "TIMEOUT = 90" and page["evidence_ref"]
            assert "lookup_if" not in page
            assert task._read_access.admitted == 1 and len(lookups) == 1


@pytest.mark.parametrize("adaptive", [False, True])
def test_miss_stops_repeat_hint_but_allows_changed_memory_and_explicit_recheck(transport, adaptive):
    client, repo, _, lookups = transport
    question = "Recall the configured timeout and explain the gateway."
    (repo / "settings.py").write_text("TIMEOUT = 30")
    (repo / "gateway.py").write_text("from settings import TIMEOUT")
    task = TaskMCP(Workflow(client, "team", repo), question,
                   context_budget=ContextBudget(encoding="utf8_bytes") if adaptive else None)
    task._availability_enabled = True
    asyncio.run(task.list_tools())
    assert task._availability_state == "empty"
    assert call(task, "research")["reason"] == "no_relevant_trail"
    assert "lookup_if" not in call(task, "read", path="gateway.py")
    assert "lookup_if" not in call(task, "read", batch=[{"path": "settings.py"}])
    assert len(lookups) == 1
    # A caller can still explicitly recheck the original question at any time.
    assert call(task, "research")["reason"] == "no_relevant_trail"
    seed(client, repo, question, ["settings.py"])
    task._availability_until = 0
    assert call(task, "read", path="settings.py")["lookup_if"] == "recall or gap"
    assert len(lookups) == 2 and call(task, "research")["hit"]


def test_larger_budget_can_retrieve_evidence_after_limited_miss(transport):
    client, repo, _, lookups = transport
    question = "Explain the configured timeout."
    (repo / "settings.py").write_text("TIMEOUT = 30")
    seed(client, repo, question, ["settings.py"])
    task = TaskMCP(Workflow(client, "team", repo), question,
                   context_budget=ContextBudget(encoding="utf8_bytes", max_tokens=3072))
    assert call(task, "research", max_context_tokens=256)["reason"] == "budget_limited"
    assert "lookup_if" not in call(task, "read", path="settings.py")
    assert call(task, "research", max_context_tokens=3072)["hit"]
    assert len(lookups) == 2


def test_publication_reopens_lookup_after_miss_and_requires_read_evidence(transport):
    client, repo, _, lookups = transport
    (repo / "worker.py").write_text("def dispatch(job): return job()")
    question = "How does dispatch invoke the job?"
    task = TaskMCP(Workflow(client, "team", repo), question)
    assert call(task, "research")["hit"] is False
    assert "publish" not in {t.name for t in asyncio.run(task.list_tools())}
    page = call(task, "read", path="worker.py")
    assert "lookup_if" not in page and "publication_guidance" in page
    assert call(task, "publish", content="Dispatch invokes the job once.", applicability="worker.dispatch",
                evidence_refs=[page["evidence_ref"]])["stored"]
    assert call(task, "read", path="worker.py")["lookup_if"] == "recall or gap"
    assert len(lookups) == 1 and call(task, "research")["hit"]


def test_lookup_outage_keeps_optional_retry_available(transport, monkeypatch):
    client, repo, _, _ = transport
    (repo / "settings.py").write_text("TIMEOUT = 30")
    task = TaskMCP(Workflow(client, "team", repo), "What is the timeout?")
    monkeypatch.setattr(client, "ask", lambda *a, **kw: {"hit": False, "reason": "server_unavailable"})
    assert call(task, "research")["reason"] == "server_unavailable"
    assert call(task, "read", path="settings.py")["lookup_if"] == "recall or gap"


def test_late_miss_cannot_suppress_lookup_after_publication(transport, monkeypatch):
    client, repo, _, _ = transport
    (repo / "worker.py").write_text("def dispatch(job): return job()")
    task = TaskMCP(Workflow(client, "team", repo), "How does dispatch invoke the job?")
    ref = call(task, "read", path="worker.py")["evidence_ref"]
    original = FastMCP.call_tool

    async def exercise():
        looked_up, published = asyncio.Event(), asyncio.Event()

        async def delay_miss(server, name, arguments):
            result = await original(server, name, arguments)
            if name == "research":
                looked_up.set()
                await published.wait()
            return result

        monkeypatch.setattr(FastMCP, "call_tool", delay_miss)
        lookup = asyncio.create_task(task.call_tool("research", {}))
        try:
            await asyncio.wait_for(looked_up.wait(), timeout=5)
            write = await task.call_tool("publish", {"content": "Dispatch calls the job.",
                                         "applicability": "worker.dispatch", "evidence_refs": [ref]})
            assert json.loads(write[0].text)["stored"]
        finally:
            published.set()
        assert json.loads((await lookup)[0].text)["hit"] is False

    asyncio.run(exercise())
    assert call(task, "read", path="worker.py")["lookup_if"] == "recall or gap"
