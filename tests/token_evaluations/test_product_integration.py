"""Provider-free real product integration; these tests do not measure model tokens.

Only HTTP transport is adapted to ASGI. Authentication, MCP, Client, Store,
SQLite vector indexes and the pinned offline ONNX models are the product code.
"""
import ast
import asyncio
import copy
import hashlib
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.types import CallToolResult

from colony.client import Client
from colony.mcp import create_mcp
from colony.models import Grant, Scope
from colony.security import generate_keys, issue, token
from colony.server import create_app
from scripts.token_evaluation.research import create_server

FIXTURES = Path(__file__).parent / "fixtures"


def source_facts(root, workload):
    """Independent typed oracle from source literals and executed fixture functions.

    Catalog answers and stored answers are never input to this oracle. Injected
    backends record only local calls; no network, provider or model is involved.
    """
    config_name = "settings" if workload == "gateway" else "policy"
    values = {}
    for statement in ast.parse((root / f"{config_name}.py").read_text()).body:
        if isinstance(statement, ast.Assign):
            values[statement.targets[0].id] = ast.literal_eval(statement.value)
    namespace = {}
    runtime_name = "client" if workload == "gateway" else "executor"
    exec(compile((root / f"{runtime_name}.py").read_text(), runtime_name, "exec"), namespace)
    calls, waits = [], []
    class Backend:
        def get(self, path, **kwargs):
            calls.append((path, kwargs))
            return type("Response", (), {"status": 503})()
        def submit(self, job):
            calls.append(job)
            return type("Result", (), {"retryable": True})()
    if workload == "gateway":
        options = values["DEFAULTS"] | values["ENVIRONMENTS"]["production"]
        namespace["fetch"](Backend(), options["timeout_ms"], options["attempts"])
        return {"timeout_ms": calls[0][1]["timeout_ms"], "max_requests": len(calls)}
    options = values["DEFAULT_POLICY"] | values["QUEUE_POLICY"]["priority"]
    namespace["execute"](Backend(), "fixture-job", options["attempts"], options["delay_ms"], waits.append)
    assert len(set(waits)) == 1
    return {"max_submissions": len(calls), "wait_count": len(waits), "delay_ms": waits[0]}


def source_gap_facts(root, workload):
    """Derive concrete exception behavior by executing the cited source chain."""
    is_gateway = workload == "gateway"
    config = "settings.py" if is_gateway else "policy.py"
    defaults = ast.literal_eval(ast.parse((root / config).read_text()).body[0].value)
    wrapper_name = "client.py" if is_gateway else "executor.py"
    wrapper_tree = ast.parse((root / wrapper_name).read_text())
    assert not any(isinstance(node, ast.Try) for node in ast.walk(wrapper_tree))
    wrapper, injected = {}, {}
    exec(compile(wrapper_tree, wrapper_name, "exec"), wrapper)
    concrete = "transport.py" if is_gateway else "backend.py"
    exec(compile((root / concrete).read_text(), concrete, "exec"), injected)
    sends = []
    def send(*args):
        sends.append(args)
        raise ConnectionError("fixture connection failure")
    adapter = injected["RetryingTransport" if is_gateway else "RetryingBackend"](send)
    with pytest.raises(ConnectionError, match="fixture connection failure"):
        if is_gateway:
            wrapper["fetch"](adapter, defaults["timeout_ms"], defaults["attempts"])
        else:
            wrapper["execute"](adapter, "job", defaults["attempts"], defaults["delay_ms"], lambda _: None)
    default = ({"default_timeout_ms": defaults["timeout_ms"]} if is_gateway
               else {"default_delay_ms": defaults["delay_ms"]})
    return dict(default, wrapper_exception_retries=0, wire_attempts=len(sends))


def assert_evidence(root, result, citations):
    assert result.get("hit") is True and result.get("status") == "validated", "warm reuse requires a validated product hit"
    reads = result.get("current_repository_reads", [])
    sources = {s["ref"]: s["content_hash"] for s in result["sources"]}
    for citation in citations:
        path = citation["path"]
        raw = (root / path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        lines = raw.decode().splitlines()
        candidates = [r for r in reads if r["path"] == path]
        assert candidates, f"missing required evidence: {path}"
        supported = False
        for read in candidates:
            assert read["sha256"] == sources[path] == digest
            start = read["start_line"]
            text = read["content"].splitlines()
            assert text == lines[start - 1:start - 1 + len(text)]
            supported |= start <= citation["start"] and start + len(text) - 1 >= citation["end"]
        assert supported, f"required source span absent: {citation}"


def assert_publication(receipt, count_before, count_after):
    assert receipt.get("stored") is True, "publication requires a stored acknowledgment"
    assert count_after == count_before + 1, "publication must persist one actual Store trail"


def assert_stale(result, prior_id):
    assert result.get("id") == prior_id
    assert result.get("status") == "stale"
    assert not result.get("current_repository_reads")
    assert result.get("recommendation") == "research"


def assert_permissions(auth_status, readonly_status, tools):
    assert auth_status == 401
    assert readonly_status == 403
    assert tools == {"ask"}


@pytest.mark.parametrize("workload", ["gateway", "worker"])
def test_authenticated_product_scenarios(tmp_path, monkeypatch, workload):
    # Explicit snapshots are required; never silently download or use a fake model.
    for name in ("COLONY_EMBED_MODEL_DIR", "COLONY_RANK_MODEL_DIR"):
        if not os.environ.get(name):
            pytest.skip(f"real offline integration requires pinned {name}")
        assert Path(os.environ[name]).is_dir()
    if os.name == "nt":
        assert tmp_path.drive.upper() == "D:", "integration artifacts must remain on D"
    monkeypatch.setenv("COLONY_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("COLONY_HOME", str(tmp_path / "unused-home"))
    manifest = json.loads((FIXTURES / "catalog.json").read_text())
    catalog = next(w for w in manifest["workloads"] if w["id"] == workload)
    tasks = {t["id"]: t for t in catalog["tasks"]}
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / workload / "v1", repo)
    admin = tmp_path / "admin"
    generate_keys(admin)
    app = create_app(tmp_path / "store", repo, admin / "issuer.pub")
    http = TestClient(app)
    requests = []
    def post(url, **kwargs):
        path = urlsplit(str(url)).path
        response = http.post(path, json=kwargs.get("json"), headers=kwargs.get("headers"))
        requests.append({"path": path, "status": response.status_code})
        return response
    monkeypatch.setattr(httpx, "post", post)  # transport seam only
    keys = {}
    for member in ("a", "b"):
        grant = Grant(member_id=member, name=member, server="http://127.0.0.1:7331", expires_at=int(time.time()) + 3600,
                      scopes=[Scope(namespace="bench", pull=True, push=member == "a")])
        key = issue(admin / "issuer.pem", grant)
        keypath = tmp_path / f"{member}-invitation.json"
        keypath.write_text(key.model_dump_json())
        Client.join(tmp_path / member, keypath, admin / "issuer.pub")
        keys[member] = key
    def connection(member):
        return create_mcp(tmp_path / member, evidence_files=True, workflow_namespace="bench", workflow_repo=repo, workflow_read_only=member == "b")
    a = connection("a")
    research_sessions = {}
    traces = {}
    evidence = tmp_path / "product-evidence"
    evidence.mkdir()
    def call(server, tool, arguments, scenario, origin="colony"):
        blocks = asyncio.run(server.call_tool(tool, arguments))
        if origin == "repository":
            assert isinstance(blocks, CallToolResult)
            assert blocks.isError is False and blocks.structuredContent is None
            blocks = blocks.content
        elif isinstance(blocks, tuple):
            blocks = blocks[0]
        text = blocks[0].text
        try:
            result = json.loads(text)
        except ValueError:
            result = text
        event = {"type": "item.completed", "item": {"id": f"{scenario}-{len(traces.get(scenario, []))}", "type": "mcp_tool_call", "server": origin, "tool": tool,
                 "arguments": arguments, "status": "completed", "result": {"content": [{"type": "text", "text": b.text} for b in blocks]}}}
        traces.setdefault(scenario, []).append(event)
        with (evidence / f"{scenario}.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(event) + "\n")
        return result
    def read_sources(scenario, paths):
        research = research_sessions.setdefault(scenario, create_server(repo, max_calls=6, files=catalog["snapshots"][0]["files"]))
        names = call(research, "fixture_files", {}, scenario, "repository").splitlines()
        assert set(paths) <= set(names)
        found = []
        for path in paths:
            read = call(research, "fixture_read", {"path": path}, scenario, "repository")
            assert read["sha256"] == hashlib.sha256((repo / path).read_bytes()).hexdigest()
            found.append(read)
        return found
    def count():
        with sqlite3.connect(tmp_path / "store" / "colony.db") as db:
            return db.execute("SELECT count(*) FROM trails").fetchone()[0]
    def publish(question, reads, scenario, facts):
        before = count()
        receipt = call(a, "push", {"question": question, "content": json.dumps(facts, sort_keys=True),
                       "sources": [{"ref": r["path"], "content_hash": r["sha256"]} for r in reads]}, scenario)
        assert_publication(receipt, before, count())
        return receipt
    question = tasks["cold_learning"]["question"]
    assert call(a, "ask", {"question": question}, "cold_learning")["hit"] is False
    paths = list(dict.fromkeys(c["path"] for c in tasks["cold_learning"]["oracle"]["citations"]))
    reads = read_sources("cold_learning", paths)
    facts = source_facts(repo, workload)
    assert facts == tasks["cold_learning"]["oracle"]["facts"]
    published = publish(question, reads, "cold_learning", facts)
    # Second declared learning slot publishes only the narrow configuration.
    narrow = tasks["partial_learning"]
    partial_question = narrow["question"]
    call(a, "ask", {"question": partial_question}, "partial_learning")
    narrow_reads = read_sources("partial_learning", narrow["publication_sources"])
    config_tree = ast.parse(narrow_reads[0]["content"])
    default_values = ast.literal_eval(config_tree.body[0].value)
    narrow_facts = ({"default_timeout_ms": default_values["timeout_ms"]} if workload == "gateway"
                    else {"default_delay_ms": default_values["delay_ms"]})
    assert narrow_facts == narrow["oracle"]["facts"]
    publish(partial_question, narrow_reads, "partial_learning", narrow_facts)
    # B is freshly constructed after both real A publications; warm reuse only asks.
    b = connection("b")
    before = count()
    question = tasks["useful_hit"]["memory_query"]
    hit = call(b, "ask", {"question": question}, "useful_hit")
    assert_evidence(repo, hit, tasks["useful_hit"]["oracle"]["citations"])
    assert json.loads(hit["content"]) == source_facts(repo, workload)
    assert count() == before
    assert len(traces["useful_hit"]) == 1
    miss = call(b, "ask", {"question": tasks["miss"]["question"]}, "miss")
    assert miss["hit"] is False
    read_sources("miss", paths)
    # Change the same checkout, retaining both publications and the exact query.
    for path in catalog["snapshots"][1]["files"]:
        shutil.copyfile(FIXTURES / workload / "v2" / path, repo / path)
    stale = call(connection("b"), "ask", {"question": tasks["stale"]["memory_query"]}, "stale")
    assert_stale(stale, hit["id"])
    current_reads = read_sources("stale", paths)
    assert source_facts(repo, workload) == tasks["stale"]["oracle"]["facts"]
    assert current_reads[0]["sha256"] != reads[0]["sha256"]
    for path in catalog["snapshots"][0]["files"]:
        shutil.copyfile(FIXTURES / workload / "v1" / path, repo / path)
    partial = call(connection("b"), "ask", {"question": tasks["insufficient"]["memory_query"]}, "insufficient")
    assert partial["hit"] is True and partial["status"] == "validated"
    assert_evidence(repo, partial, narrow["oracle"]["citations"])
    with pytest.raises(AssertionError):
        assert_evidence(repo, partial, tasks["insufficient"]["oracle"]["citations"])
    gap_paths = list(dict.fromkeys(c["path"] for c in tasks["insufficient"]["oracle"]["citations"] if c["path"] not in narrow["publication_sources"]))
    gap_reads = read_sources("insufficient", gap_paths)
    assert gap_reads
    assert source_gap_facts(repo, workload) == tasks["insufficient"]["oracle"]["facts"]
    direct = read_sources("direct_read", [tasks["direct_read"]["oracle"]["citations"][0]["path"]])
    assert direct[0]["content"]
    assert narrow_facts == tasks["direct_read"]["oracle"]["facts"]
    # Real authorization checks: unsigned request and pull-only token mutation.
    count_before_denial = count()
    denied = http.post("/ask", json={"question": question, "namespace": "bench"})
    push_payload = {"id": "d" * 32, "namespace": "bench", "question": question, "content": "forbidden", "sources": []}
    forbidden = http.post("/trails", json=push_payload, headers={"Authorization": "Bearer " + token(keys["b"])})
    tools = {t.name for t in asyncio.run(b.list_tools())}
    assert_permissions(denied.status_code, forbidden.status_code, tools)
    assert count() == count_before_denial
    # Mutation controls operate on captured values only. Each must make the
    # same integration assertions fail; no product method is replaced.
    for mutation in (dict(hit, hit=False), dict(hit, current_repository_reads=[])):
        with pytest.raises(AssertionError):
            assert_evidence(repo, mutation, tasks["useful_hit"]["oracle"]["citations"])
    wrong_content = copy.deepcopy(hit)
    wrong_content["current_repository_reads"][0]["content"] = "unsupported answer text"
    with pytest.raises(AssertionError):
        assert_evidence(repo, wrong_content, tasks["useful_hit"]["oracle"]["citations"])
    wrong_hash = copy.deepcopy(hit)
    wrong_hash["current_repository_reads"][0]["sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        assert_evidence(FIXTURES / workload / "v1", wrong_hash, tasks["useful_hit"]["oracle"]["citations"])
    for receipt, after in ((dict(published, stored=False), before), (published, before)):
        with pytest.raises(AssertionError):
            assert_publication(receipt, before, after)
    with pytest.raises(AssertionError):
        assert_stale(dict(stale, status="validated"), hit["id"])
    for statuses in ((200, 403, tools), (401, 200, tools), (401, 403, tools | {"push"})):
        with pytest.raises(AssertionError):
            assert_permissions(*statuses)
    assert any(r == {"path": "/trails", "status": 200} for r in requests)
    assert any(r == {"path": "/ask", "status": 200} for r in requests)
    for scenario, events in traces.items():
        (evidence / f"{scenario}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    # The same strict report proof consumes actual MCP events, not manufactured
    # hit/push results. Retain immutable per-task snapshots for byte verification.
    from scripts.token_evaluation.evidence import evaluate_mechanisms
    for snapshot in catalog["snapshots"]:
        shutil.copytree(FIXTURES / workload / snapshot["id"], evidence / "workspaces" / "colony" / workload / snapshot["id"])
    rows = [{"id": scenario, "arm": "colony", "workload": workload, "task": scenario,
             "events_path": str(evidence / f"{scenario}.jsonl")} for scenario in traces]
    decisions = evaluate_mechanisms(evidence, manifest, rows)
    (evidence / "mechanisms.json").write_text(json.dumps(decisions, indent=2))
    for scenario, mechanism in (("cold_learning", "publication"), ("partial_learning", "publication"),
                                ("useful_hit", "useful_reuse"), ("miss", "miss"),
                                ("stale", "stale"), ("insufficient", "insufficient")):
        assert decisions[scenario][mechanism], (scenario, decisions[scenario])
    # Exercise the report assertions with mutations of real captured events.
    # No mutation participates in the live product campaign.
    controls = []
    for scenario, tool, field, value, expected in (
        ("cold_learning", "push", "stored", False, "publication"),
        ("useful_hit", "ask", "hit", False, "useful_reuse"),
        ("stale", "ask", "status", "validated", "stale"),
        ("useful_hit", "ask", "current_repository_reads", [], "useful_reuse"),
    ):
        changed = copy.deepcopy(traces[scenario])
        item = next(event["item"] for event in changed if event["item"]["tool"] == tool)
        payload = json.loads(item["result"]["content"][0]["text"])
        payload[field] = value
        item["result"]["content"][0]["text"] = json.dumps(payload)
        mutation_path = evidence / f"mutation-{scenario}-{field}.jsonl"
        mutation_path.write_text("\n".join(json.dumps(e) for e in changed) + "\n")
        mutation_rows = copy.deepcopy(rows)
        next(row for row in mutation_rows if row["id"] == scenario)["events_path"] = str(mutation_path)
        result = evaluate_mechanisms(evidence, manifest, mutation_rows)
        with pytest.raises(AssertionError):
            assert result[scenario][expected]
        controls.append({"scenario": scenario, "mutation": field, "rejected": True})
    (evidence / "mutation-controls.json").write_text(json.dumps(controls, indent=2))

    # The literal astronomy miss does not replace the harder semantic negative.
    # Probe the original bank question after the complete declared journey in a
    # separate control journal, with no research or publication credited to it.
    # Retrieval may return either a miss or an irrelevant hit; neither earns
    # useful-reuse credit without the independently required source evidence.
    bank_question = ("Which bank settlement retry deadline does this service enforce? State whether the repository supports an answer."
                     if workload == "gateway" else
                     "Which bank settlement retry deadline does this queue worker enforce? State whether the repository supports an answer.")
    frozen_files = catalog["snapshots"][0]["files"]
    for path, expected_hash in frozen_files.items():
        raw = (repo / path).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected_hash
        text = raw.decode("utf-8")
        # This is a closed, authored fixture catalog. Check every frozen file,
        # including prose and concrete transport implementations, for the absent
        # domain; never derive answerability from the retrieved saved answer.
        domain_mentions = [line for line in text.splitlines()
                           if "settlement" in line.casefold() or "bank" in line.casefold()]
        if path.endswith(".py"):
            assert not domain_mentions, "fixture unexpectedly defines bank-settlement behavior"
        else:
            assert all(line == "Payment settlement policy is outside this repository."
                       for line in domain_mentions), "fixture prose unexpectedly supplies bank-settlement policy"
    bank_result = call(connection("b"), "ask", {"question": bank_question}, "bank_challenge")
    challenge_events = traces.pop("bank_challenge")
    control_dir = evidence / "controls"
    control_dir.mkdir()
    challenge_path = control_dir / "bank-challenge.jsonl"
    (evidence / "bank_challenge.jsonl").replace(challenge_path)
    challenge_manifest = copy.deepcopy(manifest)
    challenge_workload = next(w for w in challenge_manifest["workloads"] if w["id"] == workload)
    challenge_workload["tasks"].append({
        "id": "bank_challenge", "question": bank_question, "memory_query": bank_question,
        "memory_from": "cold_learning", "snapshot": "v1", "phase": "reuse",
        "oracle": {"facts": {"answerable": False}, "citations": [],
                   "evidence_policy": {"kind": "scoped_absence", "scope": sorted(frozen_files)}},
    })
    challenge_rows = rows + [{"id": "bank_challenge", "arm": "colony", "workload": workload,
                              "task": "bank_challenge", "events_path": str(challenge_path)}]
    challenge_decisions = evaluate_mechanisms(evidence, challenge_manifest, challenge_rows)
    challenge = challenge_decisions["bank_challenge"]
    assert challenge["errors"] == []
    assert not challenge["hit"] and not challenge["useful_reuse"]
    assert not challenge["insufficient"] and challenge["successful_research"] == 0
    assert len(challenge_events) == 1 and challenge_events[0]["item"]["tool"] == "ask"
    (control_dir / "bank-challenge.json").write_text(json.dumps({
        "actual_response": bank_result, "mechanism_decision": challenge,
        "oracle": {"facts": {"answerable": False}, "citations": [],
                   "evidence_policy": {"kind": "scoped_absence", "scope": sorted(frozen_files)},
                   "files": frozen_files},
        "native_evidence": False, "model_tokens": None, "provider_calls": 0,
        "declared_journey_unchanged": True,
    }, indent=2))

    (evidence / "scope.json").write_text(json.dumps({"native_evidence": False, "model_tokens": None, "provider_calls": 0, "requests": requests, "lineage": workload, "observed_limitations": ["Earlier bank-settlement retry query returned an irrelevant semantic worker hit after publication; not counted as a miss. Frozen miss fixture now uses unrelated astronomy."]}))



@pytest.mark.parametrize("mutation,expected_failure", [
    ("push_noop", "publication must persist one actual Store trail"),
    ("always_miss", "warm reuse requires a validated product hit"),
])
def test_real_runtime_memory_mutations_are_detected(tmp_path, monkeypatch, mutation, expected_failure):
    """Run the identical integration against deliberately broken memory methods.

    These are negative runtime controls only. The positive campaign above never
    patches product methods. Matching the contract assertion prevents unrelated
    setup, authentication, model, or transport errors from satisfying the test.
    """
    from colony.store import Store

    if mutation == "push_noop":
        def noop(self, trail, grant):
            return {"stored": True, "id": trail.id}
        monkeypatch.setattr(Store, "push", noop)
    else:
        def always_miss(self, *args, **kwargs):
            return {"hit": False, "reason": "no_relevant_trail"}
        monkeypatch.setattr(Store, "ask", always_miss)
    with pytest.raises(AssertionError, match=expected_failure) as observed:
        test_authenticated_product_scenarios(tmp_path, monkeypatch, "gateway")
    (tmp_path / "runtime-mutation-control.json").write_text(json.dumps({
        "mutation": mutation, "expected_contract_failure": expected_failure,
        "observed_assertion": str(observed.value), "native_evidence": False,
        "model_tokens": None, "provider_calls": 0,
    }, indent=2))
