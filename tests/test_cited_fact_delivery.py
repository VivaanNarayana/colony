"""Caller-visible evidence through task MCP, the HTTP client/server and pinned offline models."""
import asyncio
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from test_compact_budget_fallback_onnx import onnx_transport as onnx_transport

from colony.compact import PROFILE
from colony.context_budget import count_response
from colony.models import ContextBudget, Trail
from colony.task_mcp import TaskMCP
from colony.validation import snapshot
from colony.workflow import Workflow


def publish(client, repo, question, content, paths):
    trail = Trail(id=uuid.uuid4().hex, namespace="team", question=question, content=content,
                  sources=[snapshot(repo, path) for path in paths])
    assert client.push(trail)["stored"]
    return trail.id


def research(client, repo, question, limit):
    budget = ContextBudget(max_tokens=limit)
    task = TaskMCP(Workflow(client, "team", repo, read_only=True), question, context_budget=budget)
    try:
        block, = asyncio.run(task.call_tool("research", {}))
    finally:
        task.evidence.close()
    assert len(block.text.encode("utf-8")) <= limit
    value = json.loads(block.text)
    assert value["context_budget"]["used"] == count_response(value, budget) <= limit
    return value


def cited_lines(value, repo):
    """Index only text that actually reached the caller at its verified path and line."""
    assert value["hit"] and value["status"] == "validated" and value["match_kind"] == "evidence"
    hashes = {s["ref"]: s["content_hash"] for s in value["sources"]}
    lines = {}
    for read in value["current_repository_reads"]:
        path = read["path"]
        raw = (repo / path).read_bytes()
        assert read["sha256"] == hashes[path] == hashlib.sha256(raw).hexdigest()
        source = raw.decode().splitlines()
        for n, line in enumerate(read["content"].splitlines(), start=read["start_line"]):
            assert n >= 1 and line == source[n - 1]
            lines[path, n] = line
    return lines


@pytest.mark.parametrize("paragraphs", [1, 8], ids=["copied_file", "copied_file_and_paragraphs"])
def test_two_near_tied_facts_reach_the_caller_with_a_duplicate_file(onnx_transport, paragraphs):
    client, repo, _ = onnx_transport
    timeout = "The request timeout is 1200 milliseconds."
    retry = "The retry limit is four attempts."
    (repo / "v2").mkdir()
    for path in ("primary.txt", "v2/primary.txt"):
        (repo / path).write_text("\n\n".join([timeout] * paragraphs))
    (repo / "secondary.txt").write_text(retry)
    original = publish(client, repo, "Who owns release scheduling?", "The release manager.",
                       ["primary.txt", "secondary.txt", "v2/primary.txt"])
    question = "What are the request timeout and retry limit?"
    value = research(client, repo, question, 1800)
    assert value.get("id") == original
    lines = cited_lines(value, repo)
    assert lines.get(("secondary.txt", 1)) == retry
    assert any(text == timeout and path in {"primary.txt", "v2/primary.txt"}
               for (path, _), text in lines.items())
    assert len(value["current_repository_reads"]) == 2


@pytest.mark.parametrize(("workload", "repeated", "tags"), [
    ("gateway", "Which team collects inventory setup metrics?", "Production inventory request timeout."),
    ("worker", "What is the evidence for the worker setup report in production?", "Priority job backend."),
], ids=["gateway", "worker"])
@pytest.mark.parametrize("conditions", ["repeated_questions", "keyword_index", "combined"])
def test_compound_facts_and_citations_reach_the_caller(onnx_transport, workload, repeated, tags, conditions):
    client, repo, _ = onnx_transport
    fixtures = Path(__file__).parent / "token_evaluations" / "fixtures"
    catalog = json.loads((fixtures / "task-adapter-catalog.json").read_bytes())
    tasks = next(w["tasks"] for w in catalog["workloads"] if w["id"] == workload)
    learn, reuse = (next(t for t in tasks if t["id"] == name)
                    for name in ("cold_learning", "useful_hit"))
    paths = list(learn["publication_sources"])
    for path in paths:
        (repo / path).write_bytes((fixtures / workload / "v1" / path).read_bytes())
    if conditions in {"keyword_index", "combined"}:
        (repo / "index.txt").write_text("Index tags: " + tags)
        paths.append("index.txt")
    if conditions == "combined":
        (repo / "v2").mkdir()
        copy = "v2/" + paths[0]
        (repo / copy).write_bytes((repo / paths[0]).read_bytes())
        paths.append(copy)
    target = publish(client, repo, learn["question"], "See the configuration and execution loop.", paths)
    if conditions in {"repeated_questions", "combined"}:
        for n in range(12):
            content = f"Team ownership is recorded in the release calendar. Revision {n}."
            publish(client, repo, repeated, content, paths)

    upstream = client.ask(reuse["question"], "team", response_profile=PROFILE, context_budget=ContextBudget())
    assert upstream.get("id") == target, upstream.get("reason")
    # Fit the model-facing tool result into exactly the bytes its upstream evidence used.
    value = research(client, repo, reuse["question"], upstream["context_budget"]["used"])
    assert value.get("id") == target and value["evidence_scope"] == "cited_files"
    lines = cited_lines(value, repo)
    for citation in reuse["oracle"]["citations"]:
        path = citation["path"]
        expected = (fixtures / workload / "v1" / path).read_text().splitlines()
        for n in range(citation["start"], citation["end"] + 1):
            assert lines[path, n] == expected[n - 1]
    if workload == "gateway":
        assert '"attempts": 3' in lines["settings.py", 1] and '"timeout_ms": 1500' in lines["settings.py", 2]
    else:
        assert '"attempts": 4, "delay_ms": 250' in lines["policy.py", 2]
        assert "return result" in lines["executor.py", 5] and "sleep(delay_ms)" in lines["executor.py", 6]
    assert research(client, repo, reuse["question"], 256)["reason"] == "budget_limited"
    if conditions == "combined":
        (repo / "index.txt").write_text("Index replaced.")
        assert not research(client, repo, reuse["question"], 3072).get("current_repository_reads")
