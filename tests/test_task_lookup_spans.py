import asyncio
import hashlib
import json
import os

import pytest
from pydantic_core import to_json
from test_compact_delivery import transport as transport

from colony.context_budget import (
    _counter,
    _ordinary_read_spans,
    count_response,
    fit_response,
    render_ordinary_task_lookup,
    serialize_response,
    with_reuse_guidance,
)
from colony.mcp import create_mcp
from colony.models import ContextBudget, Trail
from colony.task_mcp import TaskMCP
from colony.validation import snapshot
from colony.workflow import Workflow


@pytest.mark.parametrize("encoding", [None, "utf8_bytes", "o200k_base"])
def test_validated_hit_then_only_the_missing_source_read(transport, monkeypatch, encoding):
    client, repo, _, responses = transport
    tokenizer = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
    if tokenizer:
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", tokenizer)
    elif encoding == "o200k_base":
        pytest.skip("Pinned tokenizer unavailable")
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
        assert "observed_lines" not in hit
        spans = [[p["path"], p["start_line"], p["end_line"]] for p in hit["current_repository_reads"]]
        old_cue = with_reuse_guidance(hit, budget)
        old_wire = serialize_response(old_cue)
        assert count_response(hit, budget) <= count_response(old_cue, budget)
    else:
        excerpts = {page["ref"]: page["text"] for page in hit["evidence"]}
        spans = hit.get("observed_lines", [])
        old_wire = to_json(responses[-1], indent=2).decode()
        assert len(hit_wire.encode()) <= len(old_wire.encode())
    if tokenizer:
        count = _counter(ContextBudget(encoding="o200k_base"))
        assert count(hit_wire) <= count(old_wire)
    # The saved assertions conflict with the current value and lack binding
    # evidence. The index describes received source, not proved facts.
    assert excerpts == {path: files[path] for path in excerpts}
    assert "handler.py" in excerpts and "settings.py" in excerpts
    if budget or tokenizer:
        assert sorted(spans) == sorted([path, 1, 1 + text.count("\n")] for path, text in excerpts.items())
    assert "999" in hit["content"] and "900" in excerpts["settings.py"]
    assert task._read_access.admitted == 0
    needed = [("client.py", 2, 2), ("settings.py", 1, 1), ("handler.py", 2, 3), ("transport.py", 1, 2)]
    # Ordinary evidence remains usable even when there is no optional index.
    if not spans:
        # The complete fixture texts above establish these particular lines.
        spans = [[path, 1, len(text.splitlines())] for path, text in excerpts.items()]
    missing = [path for path, first, last in needed if not any(
        seen == path and start <= first <= last <= end for seen, start, end in spans)]
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
    if current.get("hit") and current.get("status") == "validated":
        pages = current.get("current_repository_reads", current.get("evidence", []))
        assert all("TIMEOUT = 900" not in p.get("content", p.get("text", "")) for p in pages)
    assert call("read", path="settings.py")["content"] == "TIMEOUT = 1200"


def test_index_only_delivered_spans_and_ignore_upstream_index():
    text = {"ref": "settings.py", "line": 8, "text": "first\nsecond", "end_line": 99}
    result = {"hit": True, "status": "validated", "sources": [{"ref": "settings.py", "content_hash": "a" * 64}],
              "evidence": [text, {**text, "line": 20, "text": "third"}],
              "observed_lines": [["unseen.py", 1, 999]]}
    assert _ordinary_read_spans(result) == [["settings.py", 8, 8]]
    for changes in ({"hit": False}, {"status": "stale"}, {"status": "unverified"},
                    {"sources": [{"ref": "settings.py"}]}, {"evidence": []},
                    {"evidence": [{**text, "ref": "uncited.py"}]}):
        assert _ordinary_read_spans({**result, **changes}) == []


@pytest.mark.parametrize("raw", [b"A\nB", b"A\nB\n", b"A\r\nB", b"A\r\nB\r\n", b"A\n\n"])
def test_ordinary_whole_file_hash_confirms_last_canonical_line(raw):
    lines = raw.decode().splitlines()
    response = {"hit": True, "status": "validated",
                "sources": [{"ref": "settings", "content_hash": hashlib.sha256(raw).hexdigest()}],
                "evidence": [{"ref": "settings", "line": 1, "text": "\n".join(lines), "complete_file": False}]}
    assert _ordinary_read_spans(response) == [["settings", 1, len(lines)]]


@pytest.mark.parametrize("counter", ["unset", "invalid", "pinned"])
def test_ordinary_index_keeps_original_when_token_comparison_fails(monkeypatch, tmp_path, counter):
    path = "migrations/" + ("e9f3b6a2" * 4)[:27] + ".sql"
    response = {"hit": True, "status": "validated", "sources": [{"ref": path, "content_hash": "a" * 64}],
                "evidence": [{"ref": path, "line": 1, "text": "select 1", "complete_file": True}]}
    candidate = to_json({"observed_lines": [[path, 1, 1]], **response})
    original = to_json(response, indent=2)
    assert len(candidate) < len(original)
    if counter == "pinned":
        tokenizer = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
        if not tokenizer:
            pytest.skip("Pinned tokenizer unavailable")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", tokenizer)
        count = _counter(ContextBudget(encoding="o200k_base"))
        assert count(candidate.decode()) > count(original.decode())
    elif counter == "invalid":
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", str(tmp_path / "missing.tokenizer"))
    else:
        monkeypatch.delenv("COLONY_CONTEXT_TOKENIZER", raising=False)
    assert render_ordinary_task_lookup({**response, "observed_lines": [["forged", 1, 999]]}) == response


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
    assert "observed_lines" not in lookup
    page = lookup["current_repository_reads" if adaptive else "evidence"][0]
    assert page["content" if adaptive else "text"] == "def handle(send):"
    assert page["complete_file"] is False
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
    task = with_reuse_guidance(baseline, budget, task_lookup=True)
    assert task["current_repository_reads"] == baseline["current_repository_reads"]
    assert "observed_lines" not in task and count_response(task, budget) <= count_response(baseline, budget)


@pytest.mark.parametrize("prefix", ["", "MODE = 'live'\n"], ids=["only-line", "after-complete-line"])
def test_clipped_ordinary_hit_leaves_decisive_suffix_for_targeted_read(transport, monkeypatch, prefix):
    client, repo, _, responses = transport
    tokenizer = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
    if tokenizer:
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", tokenizer)
    question = "Is gateway_retry enabled?"
    line = "FEATURES = {" + " " * 2500 + "'gateway_retry': True}"
    (repo / "config.py").write_text(prefix + line, encoding="utf8")
    assert client.push(Trail(id="c" * 32, namespace="team", question=question,
                             content="Inspect the configured features.", sources=[snapshot(repo, "config.py")]))["stored"]
    task = TaskMCP(Workflow(client, "team", repo, read_only=True), question)
    hit = json.loads(asyncio.run(task.call_tool("research", {}))[0].text)
    assert hit["status"] == "validated"
    excerpt, = hit["evidence"]
    assert excerpt["text"] == (prefix + line)[:2400] and excerpt["complete_file"] is False
    assert "gateway_retry" not in excerpt["text"]
    expected = [["config.py", 1, 1]] if prefix else []
    assert _ordinary_read_spans(responses[-1]) == expected
    assert hit.get("observed_lines", []) == (expected if tokenizer else [])
    target_line = 2 if prefix else 1
    page = json.loads(asyncio.run(task.call_tool("read", {
        "path": "config.py", "start": target_line, "end": target_line,
    }))[0].text)
    assert page["content"] == line and page["evidence_ref"]
    assert task._read_access.admitted == 1 and len(responses) == 1


@pytest.mark.parametrize("encoding", ["utf8_bytes", "o200k_base"])
def test_six_file_task_hit_keeps_evidence_within_main_wire_cost(transport, monkeypatch, encoding):
    client, repo, _, _ = transport
    tokenizer = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
    if encoding == "utf8_bytes":
        monkeypatch.delenv("COLONY_CONTEXT_TOKENIZER", raising=False)
    elif tokenizer:
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", tokenizer)
    else:
        pytest.skip("Pinned tokenizer unavailable")
    question = "What are the configured retry settings?"
    paths = ["client.py", "settings.py", "handler.py", "retry.py", "gateway.py", "policy.py"]
    for path in paths:
        (repo / path).write_text("RETRIES = 1", encoding="utf8")
    assert client.push(Trail(id="d" * 32, namespace="team", question=question,
                             content="Settings control gateway retries.", sources=[snapshot(repo, p) for p in paths]))["stored"]
    budget = ContextBudget(max_tokens=3072, encoding=encoding)
    baseline = create_mcp(client.home, workflow_namespace="team", workflow_repo=repo,
                          workflow_read_only=True, context_budget=budget)
    task = TaskMCP(Workflow(client, "team", repo, read_only=True), question, context_budget=budget)
    before = asyncio.run(baseline.call_tool("ask", {"question": question}))[0].text
    after = asyncio.run(task.call_tool("research", {}))[0].text
    original, hit = json.loads(before), json.loads(after)
    assert hit["hit"] and hit["status"] == "validated" and "observed_lines" not in hit
    assert hit["sources"] == original["sources"] and hit["content"] == original["content"]
    assert hit["current_repository_reads"] == original["current_repository_reads"]
    assert sorted((r["path"], r["start_line"], r["end_line"]) for r in hit["current_repository_reads"]) == [
        (p, 1, 1) for p in sorted(paths)]
    assert count_response(hit, budget) == hit["context_budget"]["used"] <= original["context_budget"]["used"]
    assert len(after.encode()) <= len(before.encode())
    if tokenizer:
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", tokenizer)
        count = _counter(ContextBudget(encoding="o200k_base"))
        assert count(after) <= count(before)
    assert task._read_access.admitted == 0
