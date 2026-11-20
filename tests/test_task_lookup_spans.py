import asyncio
import json
import os

import pytest
from pydantic_core import to_json
from test_compact_delivery import transport as transport

from colony.context_budget import (
    _counter,
    count_response,
    fit_response,
    serialize_response,
    with_reuse_guidance,
    with_task_read_spans,
)
from colony.models import ContextBudget, Trail
from colony.task_mcp import TaskMCP
from colony.validation import snapshot
from colony.workflow import Workflow


@pytest.mark.parametrize("encoding", [None, "utf8_bytes", "o200k_base"])
def test_validated_hit_then_only_the_missing_source_read(transport, monkeypatch, encoding):
    client, repo, _, responses = transport
    if encoding == "o200k_base":
        if not os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER"):
            pytest.skip("Pinned tokenizer unavailable")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", os.environ["COLONY_TEST_CONTEXT_TOKENIZER"])
    files = {
        "client.py": "def fetch(transport):\n    return transport.send()",
        "settings.py": "TIMEOUT = 900",
        "handler.py": "from client import fetch\ndef handle(transport):\n    return fetch(transport)",
    }
    for path, text in files.items():
        (repo / path).write_text(text, encoding="utf8")
    (repo / "transport.py").write_text("class RetryingTransport:\n    attempts = 2", encoding="utf8")
    question = "Explain the configured timeout and the behavior with RetryingTransport from transport.py"
    trail = Trail(id="a" * 32, namespace="team", question=question,
                  content="The timeout is 999 and handle always constructs RetryingTransport.",
                  sources=[snapshot(repo, path) for path in files])
    assert client.push(trail)["stored"]
    budget = ContextBudget(max_tokens=3072, encoding=encoding) if encoding else None
    task = TaskMCP(Workflow(client, "team", repo, read_only=True), question, context_budget=budget)

    def call(name, **args):
        return json.loads(asyncio.run(task.call_tool(name, args))[0].text)

    research = next(t for t in asyncio.run(task.list_tools()) if t.name == "research")
    assert "Use shown spans; read gaps directly." in research.description[:247]
    hit_wire = asyncio.run(task.call_tool("research", {}))[0].text
    hit = json.loads(hit_wire)
    assert hit["hit"] and hit["status"] == "validated"
    if budget:
        assert hit["context_budget"]["used"] == count_response(hit, budget) <= budget.max_tokens
        excerpts = {page["path"]: page["content"] for page in hit["current_repository_reads"]}
        old_cue = with_reuse_guidance({k: v for k, v in hit.items() if k != "observed_lines"}, budget)
        old_wire = serialize_response(old_cue)
        assert count_response(hit, budget) <= count_response(old_cue, budget)
    else:
        excerpts = {page["ref"]: page["text"] for page in hit["evidence"]}
        old_wire = to_json(responses[-1], indent=2).decode()
        assert len(hit_wire.encode()) <= len(old_wire.encode())
    if tokenizer := os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER"):
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", tokenizer)
        count = _counter(ContextBudget(encoding="o200k_base"))
        assert count(hit_wire) <= count(old_wire)
    # The saved assertions conflict with the current value and lack binding
    # evidence. The index describes received source, not proved facts.
    assert excerpts == {path: files[path] for path in excerpts}
    assert "handler.py" in excerpts and "settings.py" in excerpts
    assert sorted(hit["observed_lines"]) == sorted(
        [path, 1, 1 + text.count("\n")] for path, text in excerpts.items())
    assert "999" in hit["content"] and "900" in excerpts["settings.py"]
    assert task._read_access.admitted == 0
    needed = [("client.py", 2, 2), ("settings.py", 1, 1), ("handler.py", 2, 3), ("transport.py", 1, 2)]
    missing = [path for path, first, last in needed if not any(
        seen == path and start <= first <= last <= end for seen, start, end in hit["observed_lines"])]
    # Ordinary lookup can return fewer excerpts than cited files.
    assert missing == (["transport.py"] if "client.py" in excerpts else ["client.py", "transport.py"])
    if len(missing) == 1:
        page = call("read", path=missing[0])
    else:
        page = call("read", batch=[{"path": path} for path in missing])["results"][-1]
    assert "attempts = 2" in page["content"] and page["complete_file"]
    assert task._read_access.admitted == len(missing) and len(responses) == 1
    # Reading the implementation still leaves selection by the injected caller open.
    assert "def handle(transport)" in excerpts["handler.py"]
    (repo / "settings.py").write_text("TIMEOUT = 1200", encoding="utf8")
    current = call("research")
    if current.get("observed_lines"):
        pages = current.get("current_repository_reads", current.get("evidence", []))
        assert all("TIMEOUT = 900" not in p.get("content", p.get("text", "")) for p in pages)
    assert call("read", path="settings.py")["content"] == "TIMEOUT = 1200"


def test_index_only_delivered_spans_and_ignore_upstream_index():
    text = {"ref": "settings.py", "line": 8, "text": "first\nsecond", "end_line": 99}
    result = {"hit": True, "status": "validated", "sources": [{"ref": "settings.py", "content_hash": "a" * 64}],
              "evidence": [text, {**text, "line": 20, "text": "third"}],
              "observed_lines": [["unseen.py", 1, 999]]}
    assert with_task_read_spans(result)["observed_lines"] == [["settings.py", 8, 9], ["settings.py", 20, 20]]
    for changes in ({"hit": False}, {"status": "stale"}, {"status": "unverified"},
                    {"sources": [{"ref": "settings.py"}]}, {"evidence": []},
                    {"evidence": [{**text, "ref": "uncited.py"}]}):
        assert "observed_lines" not in with_task_read_spans({**result, **changes})


@pytest.mark.parametrize("adaptive", [False, True])
def test_partially_observed_caller_can_be_read_past_the_index(transport, monkeypatch, adaptive):
    client, repo, _, _ = transport
    caller = "def handle(send):\n    try: return send()\n    except OSError: return 'offline'"
    (repo / "caller.py").write_text(caller, encoding="utf8")
    hit = {"hit": True, "match_kind": "answer", "status": "validated",
           "content": "The error propagates.", "sources": [snapshot(repo, "caller.py").model_dump()],
           "evidence": [{"ref": "caller.py", "line": 1, "text": "def handle(send):", "complete_file": False}],
           "observed_lines": [["caller.py", 1, 3]]}
    monkeypatch.setattr(client, "ask", lambda *a, **kw: hit.copy())
    monkeypatch.setattr("colony.mcp.Client", lambda home: client)
    budget = ContextBudget(max_tokens=3072, encoding="utf8_bytes") if adaptive else None
    task = TaskMCP(Workflow(client, "team", repo), "Does the error escape handle?", context_budget=budget)
    lookup = json.loads(asyncio.run(task.call_tool("research", {}))[0].text)
    assert lookup["observed_lines"] == [["caller.py", 1, 1]]
    tail = json.loads(asyncio.run(task.call_tool("read", {"path": "caller.py", "start": 2, "end": 3}))[0].text)
    assert tail["content"] == caller.split("\n", 1)[1]
    assert tail["evidence_ref"] and task._read_access.admitted == 1


def test_long_path_index_never_displaces_evidence_or_exceeds_original_cost():
    path = "directory/" * 25 + "caller.py"
    budget = ContextBudget(max_tokens=4096, encoding="utf8_bytes")
    response = {"hit": True, "status": "validated", "sources": [{"ref": path, "content_hash": "a" * 64}],
                "current_repository_reads": [{"path": path, "sha256": "a" * 64, "start_line": 8,
                                              "content": "return injected()", "complete_file": False}]}
    baseline = with_reuse_guidance(fit_response(response, budget), budget)
    assert baseline["current_repository_reads"][0]["content"] == "return injected()"
    assert with_task_read_spans(baseline, budget) == baseline
