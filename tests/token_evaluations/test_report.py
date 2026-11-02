"""Provider-free replay controls; these are not measurements of token savings."""
import hashlib
import json

import pytest

from scripts.token_evaluation.report import activity, digest, extract_answer, mask, report


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def run(tmp_path):
    task = {"id": "trace", "phase": "learn", "expected_use": "optional",
            "oracle": {"facts": {"timeout": 1500}}, "rubric": {"correct": "current facts"}}
    protocol = {"track": "natural", "output": {"answer_policy": "last_agent_message"},
                "workloads": [{"id": "gateway", "snapshots": [], "tasks": [task]}]}
    put(tmp_path / "run.json", {"contract": "colony.token-evaluation-run/1",
                               "protocol": protocol, "protocol_sha256": digest(protocol),
                               "files": {}, "fidelity": {"native_evidence": False}})
    rows = []
    for identity, arm in [("a", "no_colony"), ("b", "colony")]:
        events = [{"type": "turn.started"},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "working"}},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "1500"}},
                  {"type": "turn.completed", "usage": {"input_tokens": 100,
                   "cached_input_tokens": 40, "output_tokens": 20}}]
        path = tmp_path / "logs" / f"{identity}.events.jsonl"
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
        row = {"id": identity, "arm": arm, "workload": "gateway", "task": "trace",
               "state": "completed", "seconds": 1, "events": path.name,
               "events_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        put(tmp_path / "control" / f"{identity}-invocation.json", row)
        rows.append(row)
    (tmp_path / "control" / "attempts.jsonl").write_text("\n".join(map(json.dumps, rows)))
    masked = mask(tmp_path, tmp_path / "masked")
    labels = json.loads((tmp_path / "masked" / "private-map.json").read_text())
    for label in labels:
        label.update(correct=True, grounded=True, format_compliant=True)
    quality = {"protocol_sha256": digest(protocol), "reviewer": "independent fixture control",
               "method": "synthetic labels; harness only", "labels": labels}
    put(tmp_path / "quality.json", quality)
    return tmp_path, quality, masked


def test_mask_and_cached_accounting_no_adoption(run):
    root, _, _ = run
    public = json.loads((root / "masked" / "answers.json").read_text())
    assert all("arm" not in r and "usage" not in r and r["answer"] == "1500" for r in public)
    result = report(root, root / "quality.json", root / "report.json")
    assert result["arms"]["colony"]["known_usage"]["gross_tokens"] == 120
    assert result["arms"]["colony"]["known_usage"]["uncached_input_plus_output"] == 80
    assert result["adoption"] is False
    assert result["observed_token_reduction"] is None
    assert result["certifying"] is False


def test_answer_selection_progress_preserved(run):
    root, _, _ = run
    answer = extract_answer(root / "logs/a.events.jsonl", "all_agent_messages")
    assert answer["text"] == "working\n1500"
    assert not answer["phase_guaranteed"]


@pytest.mark.parametrize("field", ["answer_sha256", "source_sha256", "citation_sha256", "label_sha256"])
def test_corrupted_quality_binding_rejected(run, field):
    root, quality, _ = run
    quality["labels"][0][field] = "0" * 64
    put(root / "bad.json", quality)
    with pytest.raises(ValueError, match="binding changed"):
        report(root, root / "bad.json", root / "report.json")


def test_wrong_or_stale_answer_fails_quality(run):
    root, quality, _ = run
    for label in quality["labels"]:
        label["correct"] = False
    put(root / "wrong.json", quality)
    result = report(root, root / "wrong.json", root / "report.json")
    assert result["arms"]["colony"]["successful_workflows"] == 0
    assert result["arms"]["colony"]["gross_tokens_per_successful_workflow"] is None


def test_unlisted_log_rejected(run):
    root, _, _ = run
    (root / "logs" / "extra.events.jsonl").write_text("unlisted")
    with pytest.raises(ValueError, match="Unlisted"):
        report(root, root / "quality.json", root / "report.json")


def test_all_mcp_blocks_observed(tmp_path):
    path = tmp_path / "events.jsonl"
    events = []
    for tool, payload in [("ask", {"hit": False}), ("push", {"stored": True}),
                          ("ask", {"hit": True, "current_repository_reads": ["evidence"]}),
                          ("ask", {"hit": False, "status": "stale"}),
                          ("ask", {"hit": False, "reason": "no_relevant_trail"})]:
        events.append({"type": "item.completed", "item": {"type": "mcp_tool_call",
                       "server": "colony", "tool": tool, "status": "completed",
                       "result": {"content": [{"type": "text", "text": "non-JSON progress"},
                                               {"type": "text", "text": json.dumps(payload)}]}}})
    path.write_text("\n".join(map(json.dumps, events)))
    observed = activity(path)
    assert observed["stored"] == observed["hits"] == observed["stale"] == 1
    assert observed["misses"] == 3
    assert observed["current_reads"] == 1
    assert observed["insufficient"] == 3


def rewrite_events(root, identity, transform):
    path = root / "logs" / f"{identity}.events.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    transform(events)
    path.write_text("\n".join(map(json.dumps, events)))
    journal = root / "control" / "attempts.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    row = next(r for r in rows if r["id"] == identity)
    row["events_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    journal.write_text("\n".join(map(json.dumps, rows)))


def test_interrupted_unknown_usage_never_zero(run):
    root, _, _ = run
    rewrite_events(root, "b", lambda events: events.pop())
    result = report(root, root / "quality.json", root / "report.json")
    arm = result["arms"]["colony"]
    assert arm["total_usage"] is None
    assert arm["unknown_usage_attempts"] == 1
    assert arm["gross_tokens_per_successful_workflow"] is None
    assert result["billing"] is None


def test_failed_usage_retained_and_learning_included(run):
    root, _, _ = run
    def fail(events):
        events[-1]["type"] = "turn.failed"
    rewrite_events(root, "b", fail)
    result = report(root, root / "quality.json", root / "report.json")
    arm = result["arms"]["colony"]
    assert arm["known_usage"]["gross_tokens"] == 120
    assert arm["total_usage"] is None
    assert arm["successful_workflows"] == 0


def test_asymmetric_controlled_adoption_not_green(run):
    root, quality, _ = run
    frozen = json.loads((root / "run.json").read_text())
    frozen["protocol"]["track"] = "controlled"
    frozen["protocol"]["workloads"][0]["tasks"][0]["expected_use"] = "required_hit"
    frozen["protocol_sha256"] = digest(frozen["protocol"])
    put(root / "run.json", frozen)
    quality["protocol_sha256"] = frozen["protocol_sha256"]
    put(root / "quality.json", quality)
    result = report(root, root / "quality.json", root / "report.json")
    assert result["arms"]["colony"]["successful_workflows"] == 0
    assert result["arms"]["no_colony"]["successful_workflows"] == 1
    assert result["supported_product_win"] == "not_established"
    assert not result["controlled_mechanism_pass"]


def test_synthetic_optional_schedule_cannot_pass_controlled_mechanism(run):
    root, quality, _ = run
    frozen = json.loads((root / "run.json").read_text())
    frozen["protocol"]["track"] = "controlled"
    frozen["protocol_sha256"] = digest(frozen["protocol"])
    put(root / "run.json", frozen)
    quality["protocol_sha256"] = frozen["protocol_sha256"]
    put(root / "quality.json", quality)
    result = report(root, root / "quality.json", root / "report.json")
    assert not result["controlled_mechanism_pass"]
    assert result["observed_token_reduction"] is None


def test_duplicate_quality_and_missing_attempt_rejected(run):
    root, quality, _ = run
    quality["labels"].append(quality["labels"][0])
    put(root / "bad.json", quality)
    with pytest.raises(ValueError, match="Duplicate quality"):
        report(root, root / "bad.json", root / "report.json")
    (root / "control" / "b-invocation.json").unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        report(root, root / "quality.json", root / "report.json")


def native_receipt_control(root, quality):
    """Fabricated receipt schema control only; never native measurements."""
    frozen = json.loads((root / "run.json").read_text())
    protocol = frozen["protocol"]
    protocol["client"] = {"sha256": "c" * 64}
    workload = protocol["workloads"][0]
    workload["snapshots"] = [{"id": "v1", "files": {}}]
    workload["tasks"][0]["snapshot"] = "v1"
    frozen["protocol_sha256"] = digest(protocol)
    put(root / "run.json", frozen)
    quality["protocol_sha256"] = frozen["protocol_sha256"]
    for label in quality["labels"]:
        label["source_sha256"] = digest({"snapshot": "v1", "files": {}})
    put(root / "quality.json", quality)
    put(root / "preflight.json", {"synthetic_schema_control": True, "ok": True,
                                   "native_ready": True, "protocol_sha256": frozen["protocol_sha256"]})
    transitions = []
    for identity, arm in [("a", "no_colony"), ("b", "colony")]:
        invocation_path = root / "control" / f"{identity}-invocation.json"
        invocation = json.loads(invocation_path.read_text())
        invocation.update(native_evidence=True, protocol_sha256=frozen["protocol_sha256"],
                          frozenclient_executable_sha256=protocol["client"]["sha256"])
        put(invocation_path, invocation)
        transitions.append({"arm": arm, "workload": "gateway", "task": "trace",
                            "snapshot": "v1", "files_sha256": digest({})})
    collection = {"native_evidence": True, "status": "complete",
                  "protocol_sha256": frozen["protocol_sha256"],
                  "preflight_sha256": hashlib.sha256((root / "preflight.json").read_bytes()).hexdigest(),
                  "initial_empty_stores": {"no_colony": True, "colony": True},
                  "transitions": transitions}
    put(root / "collection.json", collection)
    return collection


def test_native_receipt_schema_control_descriptive_ratio(run):
    root, quality, _ = run
    native_receipt_control(root, quality)
    def add_call(events):
        events.insert(1, {"type": "item.completed", "item": {"type": "mcp_tool_call",
                          "server": "colony", "tool": "ask", "status": "completed",
                          "result": {"content": [{"type": "text", "text": json.dumps({
                              "hit": True, "status": "validated",
                              "current_repository_reads": [{"path": "settings.py"}]})}]}}})
        events[-1]["usage"]["input_tokens"] = 80
    rewrite_events(root, "b", add_call)
    result = report(root, root / "quality.json", root / "schema-control.json")
    assert result["observed_token_reduction"]["gross_tokens"] == pytest.approx(1 - 100 / 120)
    assert result["supported_product_win"] == "not_established"
    assert result["certifying"] is False


def test_corrupt_transition_rejected_before_comparison(run):
    root, quality, _ = run
    collection = native_receipt_control(root, quality)
    collection["transitions"][0]["files_sha256"] = "0" * 64
    put(root / "collection.json", collection)
    with pytest.raises(ValueError, match="transition proof"):
        report(root, root / "quality.json", root / "report.json")


def test_server_diagnostics_are_not_native_event_inventory(run):
    root, _, _ = run
    (root / "logs" / "server.log").write_text("Provider-free diagnostic", encoding="utf-8")
    assert report(root, root / "quality.json", root / "report.json")["harness_complete"]


def test_failed_started_research_counted_and_unapproved_tools_flagged(tmp_path):
    path = tmp_path / "events.jsonl"
    item = {"id": "r", "type": "mcp_tool_call", "server": "repository", "tool": "fixture_read"}
    events = [{"type": "item.started", "item": item},
              {"type": "item.completed", "item": {**item, "status": "failed"}},
              {"type": "item.completed", "item": {"id": "x", "type": "command_execution"}},
              {"type": "item.completed", "item": {"id": "y", "type": "mcp_tool_call",
               "server": "other", "tool": "read"}},
              {"type": "item.started", "item": {**item, "id": "interrupted"}}]
    path.write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
    observed = activity(path)
    assert observed["research_calls"] == 2
    assert observed["unapproved_calls"] == 2


def test_unready_preflight_rejected(run):
    root, quality, _ = run
    collection = native_receipt_control(root, quality)
    preflight = json.loads((root / "preflight.json").read_text(encoding="utf-8"))
    preflight["native_ready"] = False
    put(root / "preflight.json", preflight)
    collection["preflight_sha256"] = hashlib.sha256((root / "preflight.json").read_bytes()).hexdigest()
    put(root / "collection.json", collection)
    with pytest.raises(ValueError, match="Preflight was not ready"):
        report(root, root / "quality.json", root / "report.json")


def test_research_call_limit_enforced_in_report(run):
    root, quality, _ = run
    frozen = json.loads((root / "run.json").read_text(encoding="utf-8"))
    frozen["protocol"]["research"] = {"max_calls": 1}
    frozen["protocol_sha256"] = digest(frozen["protocol"])
    put(root / "run.json", frozen)
    quality["protocol_sha256"] = frozen["protocol_sha256"]
    put(root / "quality.json", quality)
    def calls(events):
        for identity in ("one", "two"):
            events.insert(1, {"type": "item.completed", "item": {"id": identity,
                             "type": "mcp_tool_call", "server": "repository",
                             "tool": "fixture_read", "status": "failed"}})
    rewrite_events(root, "b", calls)
    result = report(root, root / "quality.json", root / "report.json")
    assert not result["tool_policy_compliant"]
    assert not result["useful_reuse"]


def test_baseline_colony_call_breaks_policy(run):
    root, _, _ = run
    def call(events):
        events.insert(1, {"type": "item.completed", "item": {"id": "leak", "type": "mcp_tool_call",
                          "server": "colony", "tool": "ask", "status": "completed"}})
    rewrite_events(root, "a", call)
    result = report(root, root / "quality.json", root / "report.json")
    assert not result["tool_policy_compliant"]
    assert result["natural_adoption_observed"] is False


def test_quality_binds_selected_snapshot(run):
    root, quality, _ = run
    frozen = json.loads((root / "run.json").read_text())
    workload = frozen["protocol"]["workloads"][0]
    workload["snapshots"] = [{"id": "v1", "files": {"x": "a" * 64}},
                             {"id": "v2", "files": {"x": "b" * 64}}]
    workload["tasks"][0]["snapshot"] = "v2"
    frozen["protocol_sha256"] = digest(frozen["protocol"])
    put(root / "run.json", frozen)
    quality["protocol_sha256"] = frozen["protocol_sha256"]
    for label in quality["labels"]:
        label["source_sha256"] = digest({"snapshot": "v1", "files": workload["snapshots"][0]["files"]})
    put(root / "quality.json", quality)
    with pytest.raises(ValueError, match="source_sha256"):
        report(root, root / "quality.json", root / "report.json")


def test_duplicate_tool_completion_rejected(tmp_path):
    path = tmp_path / "events.jsonl"
    event = {"type": "item.completed", "item": {"id": "same", "type": "mcp_tool_call",
             "server": "colony", "tool": "push", "status": "completed",
             "result": {"content": [{"type": "text", "text": '{"stored": true}'}]}}}
    path.write_text("\n".join([json.dumps(event)] * 2))
    with pytest.raises(ValueError, match="Duplicate completed"):
        activity(path)


def test_mask_exposes_only_selected_current_sources(run):
    root, _, _ = run
    frozen = json.loads((root / "run.json").read_text())
    workload = frozen["protocol"]["workloads"][0]
    workload["snapshots"] = []
    for snapshot, text in [("v1", "old value"), ("v2", "current value")]:
        raw = text.encode()
        source = root / "workspaces" / "no_colony" / "gateway" / snapshot / "settings.py"
        source.parent.mkdir(parents=True)
        source.write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        frozen["files"][source.relative_to(root).as_posix()] = sha
        workload["snapshots"].append({"id": snapshot, "files": {"settings.py": sha}})
    workload["tasks"][0]["snapshot"] = "v2"
    frozen["protocol_sha256"] = digest(frozen["protocol"])
    put(root / "run.json", frozen)
    mask(root, root / "new-mask")
    public = json.loads((root / "new-mask" / "answers.json").read_text(encoding="utf-8"))
    assert all(row["current_sources"][0]["content"] == "current value" for row in public)
