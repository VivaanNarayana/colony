"""Caller-visible evidence through task MCP, the HTTP client/server and pinned offline models."""
import asyncio
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_compact_budget_fallback_onnx import onnx_transport as onnx_transport

from colony import store
from colony.compact import PROFILE
from colony.context_budget import count_response, fit_response
from colony.models import ContextBudget, Trail
from colony.task_mcp import TaskMCP
from colony.validation import snapshot
from colony.workflow import Workflow


def publish(client, repo, question, content, paths):
    trail = Trail(id=uuid.uuid4().hex, namespace="team", question=question, content=content,
                  sources=[snapshot(repo, path) for path in paths])
    assert client.push(trail)["stored"]
    return trail.id


def research(client, repo, question, limit, encoding="utf8_bytes"):
    budget = ContextBudget(max_tokens=limit, encoding=encoding)
    task = TaskMCP(Workflow(client, "team", repo, read_only=True), question, context_budget=budget)
    try:
        block, = asyncio.run(task.call_tool("research", {}))
    finally:
        task.evidence.close()
    if encoding == "utf8_bytes":
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


def learning_case(repo, workload):
    fixtures = Path(__file__).parent / "token_evaluations" / "fixtures"
    catalog = json.loads((fixtures / "task-adapter-catalog.json").read_bytes())
    tasks = {t["id"]: t for t in next(w["tasks"] for w in catalog["workloads"] if w["id"] == workload)}
    learn = tasks["cold_learning"]
    snapshot_dir = fixtures / workload / "v1"
    for path in learn["publication_sources"]:
        (repo / path).write_bytes((snapshot_dir / path).read_bytes())
    return snapshot_dir, learn, tasks["useful_hit"]


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


def test_cited_numeric_answer_stays_compact_even_when_it_mentions_another_file(onnx_transport):
    client, repo, _ = onnx_transport
    text = "The request timeout is 1200 milliseconds. See details.txt."
    (repo / "primary.txt").write_text(text)
    (repo / "details.txt").write_text("The release calendar lists the owners.")
    target = publish(client, repo, "Who owns release scheduling?", "The release manager.",
                     ["primary.txt", "details.txt"])
    value = research(client, repo, "Is the request timeout 1200 milliseconds?", 3072)
    assert value["id"] == target
    assert cited_lines(value, repo) == {("primary.txt", 1): text}


@pytest.mark.parametrize(("workload", "repeated", "tags"), [
    ("gateway", "Which team collects inventory setup metrics?", "Production inventory request timeout."),
    ("worker", "What is the evidence for the worker setup report in production?", "Priority job backend."),
], ids=["gateway", "worker"])
@pytest.mark.parametrize("conditions", [
    "repeated_questions", "keyword_index", "pointer", "markdown_link", "topic_list", "combined",
])
def test_compound_facts_and_citations_reach_the_caller(onnx_transport, workload, repeated, tags, conditions):
    client, repo, _ = onnx_transport
    snapshot_dir, learn, reuse = learning_case(repo, workload)
    paths = list(learn["publication_sources"])
    if conditions != "repeated_questions":
        topic = ("Production inventory request count and timeout" if workload == "gateway" else
                 "Priority job backend submissions and wait schedule")
        index = {"pointer": f"{topic}: see {paths[0]} and {paths[1]}.",
                 "markdown_link": f"[{topic}](./{paths[0]}#L1)",
                 "topic_list": topic + "."}.get(conditions, "Index tags: " + tags)
        (repo / "overview.txt").write_text(index)
        paths.append("overview.txt")
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
        expected = (snapshot_dir / path).read_text().splitlines()
        for n in range(citation["start"], citation["end"] + 1):
            assert lines[path, n] == expected[n - 1]
    if workload == "gateway":
        assert '"attempts": 3' in lines["settings.py", 1] and '"timeout_ms": 1500' in lines["settings.py", 2]
    else:
        assert '"attempts": 4, "delay_ms": 250' in lines["policy.py", 2]
        assert "return result" in lines["executor.py", 5] and "sleep(delay_ms)" in lines["executor.py", 6]
    assert research(client, repo, reuse["question"], 256)["reason"] == "budget_limited"
    if conditions == "combined":
        (repo / "overview.txt").write_text("Index replaced.")
        assert not research(client, repo, reuse["question"], 3072).get("current_repository_reads")


@pytest.mark.parametrize("encoding", ["utf8_bytes", "o200k_base"])
def test_shorter_equivalent_citation_keeps_both_facts_at_the_wire_limit(onnx_transport, monkeypatch, encoding):
    client, repo, _ = onnx_transport
    if encoding == "o200k_base":
        pytest.importorskip("tiktoken")
        tokenizer = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
        if not tokenizer:
            pytest.skip("Verified local tokenizer fixture unavailable")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", tokenizer)
    long = "settings/production/gateway/request_timeout.txt"
    timeout = "The request timeout is 1200 milliseconds."
    retry = "The retry limit is four attempts."
    question = "What are the request timeout and retry limit?"
    (repo / long).parent.mkdir(parents=True)
    for path, text in (("a.txt", timeout), (long, timeout), ("secondary.txt", retry)):
        (repo / path).write_text(text)
    target = publish(client, repo, "Who owns release scheduling?", "The release manager.",
                     ["a.txt", long, "secondary.txt"])
    budget = ContextBudget(encoding=encoding)
    upstream = client.ask(question, "team", context_budget=budget, response_profile=PROFILE)
    assert {r["path"] for r in upstream["current_repository_reads"]} == {long, "secondary.txt"}
    # Measure the equivalent packet using the actual encoding and the same hashes.
    # Account for changes to the receipt's own max_tokens and limited fields.
    for read in upstream["current_repository_reads"]:
        if read["path"] == long:
            read["path"] = "a.txt"
    limit = fit_response(upstream, budget)["context_budget"]["used"]
    while limit > 256 and fit_response(upstream, ContextBudget(max_tokens=limit - 1, encoding=encoding))["hit"]:
        limit -= 1
    tight = research(client, repo, question, limit, encoding)
    assert tight.get("id") == target
    assert cited_lines(tight, repo) == {("a.txt", 1): timeout, ("secondary.txt", 1): retry}
    assert research(client, repo, question, limit - 1, encoding)["reason"] == "budget_limited"


def test_repeated_over_budget_bundles_bound_work_under_writer_lock(onnx_transport, monkeypatch):
    client, repo, database = onnx_transport
    _, learn, reuse = learning_case(repo, "gateway")
    paths = learn["publication_sources"]
    ids = [publish(client, repo, learn["question"], "See the gateway configuration and request loop.", paths)
           for _ in range(24)]

    connect, bundle = database.connect, store.complete_source_bundle
    connections, builds = [], []

    @contextmanager
    def traced_connect():
        with connect() as db:
            connections.append(db)
            try:
                yield db
            finally:
                connections.pop()

    def traced_bundle(*args, **kwargs):
        builds.append(connections[-1].in_transaction)
        return bundle(*args, **kwargs)

    monkeypatch.setattr(database, "connect", traced_connect)
    monkeypatch.setattr(store, "complete_source_bundle", traced_bundle)
    assert research(client, repo, reuse["question"], 256)["reason"] == "budget_limited"
    # The shortlist and final selection each need at most one materialization.
    assert 0 < len(builds) <= 2 and not any(builds)
    upstream = client.ask(reuse["question"], "team", context_budget=ContextBudget(), response_profile=PROFILE)
    assert upstream["id"] == ids[-1]
    client.memory_feedback(ids[-1], "team", verdict="incorrect")
    # The newest packet's feedback warning now costs extra bytes. The previous
    # publication has identical sources, different metadata and still fits.
    assert research(client, repo, reuse["question"], upstream["context_budget"]["used"])["id"] == ids[-2]
    assert not any(builds)
