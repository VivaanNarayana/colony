import asyncio
import json
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_integration import running_team as running_team
from test_task_routing import call
from test_workflow import identity as identity

from colony.citations import final_citations
from colony.client import Client
from colony.models import ContextBudget
from colony.task_mcp import TaskMCP
from colony.workflow import Workflow


def test_completion_intersects_verified_pages_and_scrubs(identity, monkeypatch):
    client, repo, _ = identity
    (repo / "x").write_text("one\nsecond\nthird\npassword=example\nlast\n")
    requests = []
    monkeypatch.setattr(client, "request", lambda route, body: requests.append((route, body)) or {"stored": True})
    server = TaskMCP(Workflow(client, "team", repo), "password=example", reuse_citations=True)
    server.evidence.read("x", _max_bytes=6)
    server.evidence.read("x", 3, 4)
    result = server.complete(["x:1-5", "never-read:1"])
    assert result["stored"] and requests[0][0] == "/citations/save"
    body = requests[0][1]
    assert body["question"] == "[REDACTED]"
    assert "example" not in json.dumps(body)
    assert [(s["path"], s["start"], s["end"]) for s in body["spans"]] == [("x", 1, 1), ("x", 3, 3)]
    assert not server.complete(["x:1"])["stored"]
    assert len(requests) == 1


@pytest.mark.parametrize("reason", ["disabled", "read_only", "direct", "explicit", "changed", "closed"])
def test_skipped_writes(identity, monkeypatch, reason):
    client, repo, _ = identity
    monkeypatch.setattr(client, "request", lambda *a: pytest.fail("unexpected save"))
    server = TaskMCP(Workflow(client, "team", repo, read_only=reason == "read_only"), "q",
                     reuse_citations=reason != "disabled", direct_path="settings.py" if reason == "direct" else None)
    ref = call(server, "read", {"path": "settings.py"})["evidence_ref"]
    if reason == "explicit":
        monkeypatch.setattr(client, "push", lambda *a, **kw: {"queued": True})
        assert call(server, "publish", {"content": "Timeout", "applicability": "Here", "evidence_refs": [ref]})["queued"]
    if reason == "changed":
        (repo / "settings.py").write_text("changed\n")
    if reason == "closed":
        server.evidence.close()
    assert not server.complete(["settings.py:1"])["stored"]


def test_joined_pages_can_cover_a_line_but_gaps_cannot(identity, monkeypatch):
    client, repo, _ = identity
    (repo / "x").write_text("abcdefghij\n")
    monkeypatch.setattr(client, "request", lambda *a: {"stored": True})
    for offset, expected in [(4, True), (5, False)]:
        server = TaskMCP(Workflow(client, "team", repo), "q", reuse_citations=True)
        server.evidence.read("x", _max_bytes=4)
        server.evidence.read("x", offset=offset)
        assert server.complete(["x:1"])["stored"] is expected


def test_accepts_structured_final_answer_citations_only():
    citations = final_citations(["src/file.py:2–4", {"path": "x", "lines": [1, 2]},
                                {"path": "x", "lines": 3}, {"path": "x", "start": 5, "end": 5},
                                {"path": "y"}, "free text", {"path": "x", "lines": True}])
    assert [(s.path, s.start, s.end) for s in citations] == [
        ("src/file.py", 2, 4), ("x", 1, 2), ("x", 3, 3), ("x", 5, 5)]


@pytest.mark.parametrize("workload_id", ["gateway", "worker"])
def test_catalog_paraphrase_reuses_the_same_verified_ranges(team, workload_id):
    from conftest import auth

    _, http, repo, _, key = team
    catalog = Path(__file__).parent / "token_evaluations" / "fixtures"
    workload = next(w for w in json.loads((catalog / "task-adapter-catalog.json").read_bytes())["workloads"]
                    if w["id"] == workload_id)
    tasks = {t["id"]: t for t in workload["tasks"]}
    cold, later = tasks["cold_learning"], tasks["useful_hit"]
    files = workload["snapshots"][0]["files"]
    for path in files:
        (repo / path).write_bytes((catalog / workload_id / "v1" / path).read_bytes())
    spans = [{**s, "sha256": files[s["path"]]} for s in cold["oracle"]["citations"]]
    payload = {"namespace": "team", "question": cold["question"], "spans": spans}
    result = http.post("/citations/save", json=payload, headers=auth(key))
    assert result.status_code == 200 and result.json()["stored"]
    assert http.post("/citations/save", json=payload, headers=auth(key)).json() == result.json()
    query = {"namespace": "team", "question": later["question"]}
    found = http.post("/citations/lookup", json=query, headers=auth(key)).json()
    assert found == {"id": result.json()["id"], "spans": spans}
    assert http.post("/citations/lookup", json={**query, "question": tasks["miss"]["question"]},
                     headers=auth(key)).json() is None
    assert http.post("/citations/lookup", json={**query, "namespace": "other"}, headers=auth(key)).status_code == 403
    assert http.post("/citations/save", json={**payload, "repository_id": "other"}, headers=auth(key)).status_code == 409
    (repo / spans[0]["path"]).write_text("changed")
    assert http.post("/citations/lookup", json=query, headers=auth(key)).json() is None


def test_final_citation_round_trip(running_team, tmp_path):
    """Two fresh task sessions, a live authenticated HTTP server and a stdio completion relay."""
    import subprocess
    import sys
    import time

    from scripts.token_evaluation.citation_host import completion

    _, homes, repo, _ = running_team
    home_a, home_b = homes["a"], homes["b"]
    path = "build.py"
    (repo / path).write_text("def build():\n    return 42\n")
    question = "How does the build function work?"
    endpoint = tmp_path / "worker.endpoint"
    args = [sys.executable, "-m", "colony.task_mcp", "--home", str(home_a), "--repo", str(repo),
            "--ns", "team", "--question", question, "--reuse-citations", "--completion-channel", str(endpoint)]
    with completion(args, endpoint, None, subprocess.DEVNULL, time.monotonic() + 60) as finish:
        async def worker():
            params = StdioServerParameters(command=sys.executable, args=["-m", "colony.cited_host", str(endpoint)])
            async with stdio_client(params) as streams, ClientSession(*streams) as session:
                await session.initialize()
                assert not (await session.call_tool("read", {"path": path})).isError

        asyncio.run(worker())
        result = finish(json.dumps({"facts": "NEVER STORE THIS PROSE", "sources": [f"{path}:1-2"]}))
        assert result["stored"], result
    reader = Client(home_b)
    assert reader.memory_availability("team", include_citations=True) == "present"
    assert reader.memory_availability("team") == "empty"
    follow = TaskMCP(Workflow(reader, "team", repo, read_only=True), "How does the build function operate?",
                     context_budget=ContextBudget(), reuse_citations=True)
    hit = call(follow, "research", {})
    assert hit.get("origin") == "task_citations", hit
    assert hit["id"] == result["id"] and hit["match_kind"] == "evidence"
    assert hit["current_repository_reads"][0]["content"] == "\n".join((repo / path).read_text().splitlines()[:2])
    assert "NEVER STORE" not in json.dumps(hit)
    assert hit["context_budget"]["used"] <= 3072
    assert not call(follow, "research", {"max_context_tokens": 256})["hit"]
    denied = TaskMCP(Workflow(reader, "team", repo, read_only=True), question,
                     read_path_allowed=lambda _: False, reuse_citations=True)
    assert not call(denied, "research", {})["hit"]
    (repo / path).write_text("changed\n")
    assert not call(follow, "research", {})["hit"]
