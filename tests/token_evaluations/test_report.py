"""Provider-free replay controls; these are not measurements of token savings."""
import hashlib
import json
import sys

import pytest

from scripts.token_evaluation.output import JSON_INSTRUCTION, OUTPUT_CONTRACT, instruction
from scripts.token_evaluation.protocol import CONTRACT, collector_hashes
from scripts.token_evaluation.quality import review_policy
from scripts.token_evaluation.report import activity, digest, extract_answer, mask, report


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def run(tmp_path):
    task = {"id": "trace", "phase": "learn", "expected_use": "optional",
            "question": "Fixture question",
            "oracle": {"facts": {"timeout": 1500},
                       "citations": [{"path": "settings.py", "start": 1, "end": 1}],
                       "evidence_policy": {"kind": "support"},
                       "unsupported_claims_fail": True, "stale_snapshot_fails": True},
            "rubric": {"correct": "current facts", "grounded": "observed current definition"}}
    protocol = {"contract": CONTRACT, "collector": {"files": collector_hashes()},
                "track": "natural", "quality_review": review_policy(), "output": {"contract": OUTPUT_CONTRACT,
                "format": "free_text", "answer_policy": "last_agent_message"},
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
    quality = {"contract": "colony.agent-quality/1",
               "protocol_sha256": digest(protocol), "policy": review_policy(), "labels": labels, "reviews": []}
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
    assert result["answer_quality_complete"] is False
    assert result["mechanism_complete"] is None
    assert result["workflow_success"] is result["quality_complete"] is False
    assert result["controlled_mechanism_pass"] is False
    assert all(row["quality_state"] == "unresolved" and not row["successful"]
               for row in result["attempts"])
    assert all(result["arms"][arm]["total_usage"]["gross_tokens"] == 120
               and result["arms"][arm]["successful_workflows"] == 0
               for arm in ("colony", "no_colony"))


def test_answer_selection_progress_preserved(run):
    root, _, _ = run
    answer = extract_answer(root / "logs/a.events.jsonl", "all_agent_messages")
    assert answer["text"] == "working\n1500"
    assert not answer["phase_guaranteed"]


def test_long_display_names_round_trip_with_generated_attempt_ids(run):
    """Synthetic reader compatibility, not native token evidence."""
    from scripts.token_evaluation.protocol import attempt_id
    root, quality, _ = run
    frozen = json.loads((root / "run.json").read_text())
    workload = frozen["protocol"]["workloads"][0]
    workload["id"] = "w" * 64
    workload["tasks"][0]["id"] = "t" * 64
    frozen["protocol_sha256"] = digest(frozen["protocol"])
    put(root / "run.json", frozen)
    journal = root / "control/attempts.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    for row in rows:
        old_id, old_events = row["id"], row["events"]
        identity = attempt_id(workload["id"], workload["tasks"][0]["id"], row["arm"])
        row.update(id=identity, workload=workload["id"], task=workload["tasks"][0]["id"],
                   events=identity + ".events.jsonl")
        (root / "logs" / old_events).rename(root / "logs" / row["events"])
        put(root / "control" / (identity + "-invocation.json"), row)
        (root / "control" / (old_id + "-invocation.json")).unlink()
        next(label for label in quality["labels"] if label["id"] == old_id)["id"] = identity
    journal.write_text("\n".join(map(json.dumps, rows)))
    quality["protocol_sha256"] = frozen["protocol_sha256"]
    put(root / "quality.json", quality)
    value = report(root, root / "quality.json", root / "long-names-report.json")
    assert value["harness_complete"] and not value["native_evidence"]
    assert {row["id"] for row in value["attempts"]} == {row["id"] for row in rows}
    assert all(len(row["id"]) <= 128 for row in value["attempts"])


@pytest.mark.parametrize("field", ["question_sha256", "answer_sha256", "source_sha256", "citation_sha256", "label_sha256", "output_sha256"])
def test_corrupted_quality_binding_rejected(run, field):
    root, quality, _ = run
    quality["labels"][0][field] = "0" * 64
    put(root / "bad.json", quality)
    with pytest.raises(ValueError, match="binding changed"):
        report(root, root / "bad.json", root / "report.json")


@pytest.mark.parametrize("field", ["correct", "grounded", "format_compliant"])
@pytest.mark.parametrize("value", [True, False])
def test_report_rejects_forged_boolean_quality_labels(run, field, value):
    root, quality, _ = run
    quality["labels"][0][field] = value
    put(root / "forged.json", quality)
    with pytest.raises(ValueError):
        report(root, root / "forged.json", root / "report.json")
    assert not (root / "report.json").exists()


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
        events.append({"type": "item.completed", "item": {"id": f"blocks-{len(events)}", "type": "mcp_tool_call",
                       "server": "colony", "tool": tool, "status": "completed",
                       "result": {"content": [{"type": "text", "text": "non-JSON progress"},
                                               {"type": "text", "text": json.dumps(payload)}]}}})
    path.write_text("\n".join(map(json.dumps, events)))
    observed = activity(path)
    assert observed["stored"] == observed["hits"] == observed["stale"] == 1
    assert observed["misses"] == 3
    assert observed["claimed_current_reads"] == 1
    assert observed["miss_or_research_advice"] == 3


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


def json_answer_control(run, answer):
    """Synthetic full report controls; every altered answer gets an honest new binding."""
    root, quality, _ = run
    frozen = json.loads((root / "run.json").read_text())
    frozen["protocol"]["output"]["format"] = "json"
    frozen["protocol_sha256"] = digest(frozen["protocol"])
    put(root / "run.json", frozen)
    quality["protocol_sha256"] = frozen["protocol_sha256"]
    output_binding = digest({"output": frozen["protocol"]["output"],
                             "output_instruction": instruction(frozen["protocol"]["output"])})
    for identity in ("a", "b"):
        def replace(events):
            events[-2]["item"]["text"] = answer
        rewrite_events(root, identity, replace)
        label = next(label for label in quality["labels"] if label["id"] == identity)
        label.update(answer_sha256=hashlib.sha256(answer.encode()).hexdigest(),
                     output_sha256=output_binding)
    put(root / "quality.json", quality)
    return root


@pytest.mark.parametrize("answer,structure", [
    ('{"facts":{},"sources":[]}', True),
    ('{"facts":{"sources":[]}}', False),
    ('```json\n{"facts":{},"sources":[]}\n```', False),
    ('{"facts":{"claim":"unsupported"},"sources":[]}', True),
])
def test_structural_checks_do_not_resolve_semantics(run, answer, structure):
    root = json_answer_control(run, answer)
    result = report(root, root / "quality.json", root / "report.json")
    assert result["contract"] == CONTRACT and result["output"]["contract"] == OUTPUT_CONTRACT
    for row in result["attempts"]:
        assert row["structural_format_compliant"] is structure
        assert row["format_compliant"] is structure
        assert row["quality_state"] == ("unresolved" if structure else "failed")
        assert row["quality_obligations"]["correct"] == "unresolved"
        assert row["quality_obligations"]["grounded"] == "unresolved"
        assert row["answer_quality_successful"] is False
        assert row["successful"] is False
    assert result["quality_complete"] is False
    assert result["answer_quality_complete"] is False
    assert result["workflow_success"] is False
    assert result["observed_token_reduction"] is None
    assert result["mechanism_complete"] is None
    assert all(result["arms"][arm]["known_usage"]["gross_tokens"] == 120
               for arm in ("no_colony", "colony"))
    mask(root, root / "remasked")
    packet = json.loads((root / "remasked" / "answers.json").read_text())
    assert all(row["output_instruction"] == JSON_INSTRUCTION
               and row["output"]["answer_policy"] == "last_agent_message" for row in packet)


def test_free_text_semantics_remain_unresolved(run):
    root, _, _ = run
    result = report(root, root / "quality.json", root / "report.json")
    assert all(row["structural_format_compliant"] and row["format_compliant"]
               and row["quality_state"] == "unresolved" and not row["successful"]
               for row in result["attempts"])


def test_parser_failure_keeps_complete_usage_in_report(run):
    root = json_answer_control(run, '{"facts":' + '[' * 2000 + '0' + ']' * 1999 + ',"sources":[]}')
    result = report(root, root / "quality.json", root / "report.json")
    assert all(not row["structural_format_compliant"] for row in result["attempts"])
    assert all(result["arms"][arm]["total_usage"]["gross_tokens"] == 120
               for arm in ("no_colony", "colony"))


@pytest.mark.parametrize("change", ["legacy", "previous", "research_v3", "evidence_v4", "adjudicated_v5", "advisory_v6", "missing_version", "wrong_version", "missing_format"])
def test_report_and_mask_refuse_unbound_or_historical_contract_without_regrading(run, change):
    root, _, _ = run
    frozen = json.loads((root / "run.json").read_text())
    if change == "legacy":
        frozen["protocol"]["contract"] = "colony.token-evaluation/1"
    elif change == "previous":
        frozen["protocol"]["contract"] = "colony.token-evaluation/2"
    elif change == "research_v3":
        frozen["protocol"]["contract"] = "colony.token-evaluation/3"
    elif change == "evidence_v4":
        frozen["protocol"]["contract"] = "colony.token-evaluation/4"
    elif change == "adjudicated_v5":
        frozen["protocol"]["contract"] = "colony.token-evaluation/5"
    elif change == "advisory_v6":
        frozen["protocol"]["contract"] = "colony.token-evaluation/6"
    elif change == "missing_version":
        frozen["protocol"]["output"].pop("contract")
    elif change == "wrong_version":
        frozen["protocol"]["output"]["contract"] = "other"
    else:
        frozen["protocol"]["output"].pop("format")
    frozen["protocol_sha256"] = digest(frozen["protocol"])
    put(root / "run.json", frozen)
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    for action in [lambda: report(root, root / "quality.json", root / "refused.json"),
                   lambda: mask(root, root / "refused-mask")]:
        with pytest.raises(ValueError):
            action()
    assert before == {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert not (root / "refused-mask").exists()


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
    assert result["arms"]["no_colony"]["successful_workflows"] == 0
    assert result["supported_product_win"] == "not_established"
    assert not result["controlled_mechanism_pass"]
    assert result["answer_quality_complete"] is False
    assert result["mechanism_complete"] is False
    assert result["workflow_success"] is result["quality_complete"] is False
    assert result["observed_token_reduction"] is None


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
    from scripts.token_evaluation.native import _client_args, _prompt
    from scripts.token_evaluation.runtime import environment

    protocol["client"] = {"sha256": "c" * 64, "executable": str(root / "client.exe"),
                          "model": "fixture-model", "effort": "low"}
    protocol.setdefault("target", {"python": sys.executable, "mcp_args": []})
    protocol.setdefault("research", {"max_calls": 6})
    frozen["run_dir"] = str(root.resolve())
    workload = protocol["workloads"][0]
    source_bytes = b"TIMEOUT = 1500\n"
    source_files = {"settings.py": hashlib.sha256(source_bytes).hexdigest()}
    for arm in ("no_colony", "colony"):
        source = root / "workspaces" / arm / "gateway/v1/settings.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(source_bytes)
        frozen["files"][source.relative_to(root).as_posix()] = source_files["settings.py"]
    workload["snapshots"] = [{"id": "v1", "files": source_files}]
    workload["tasks"][0]["snapshot"] = "v1"
    task = workload["tasks"][0]
    task.setdefault("question", "Fixture question")
    frozen["protocol_sha256"] = digest(protocol)
    put(root / "run.json", frozen)
    quality["protocol_sha256"] = frozen["protocol_sha256"]
    for label in quality["labels"]:
        label["source_sha256"] = digest({"snapshot": "v1", "files": source_files})
    put(root / "quality.json", quality)
    put(root / "preflight.json", {"synthetic_schema_control": True, "ok": True,
                                   "native_ready": True, "protocol_sha256": frozen["protocol_sha256"]})
    transitions = []
    env = environment(root)
    env["CODEX_HOME"] = str(root / "private/client")
    team = {"homes": {role: str(root / "private/gateway/team" / ("agent-" + role))
                      for role in ("a", "b")}}
    for identity, arm in [("a", "no_colony"), ("b", "colony")]:
        invocation_path = root / "control" / f"{identity}-invocation.json"
        invocation = json.loads(invocation_path.read_text())
        guidance = "Fixture installed guidance." if arm == "colony" else ""
        prompt = _prompt(protocol, task, guidance).encode()
        prompt_path = root / "prompts" / (identity + ".txt")
        prompt_path.parent.mkdir(exist_ok=True)
        prompt_path.write_bytes(prompt)
        workspace = root / "sessions" / identity
        invocation.update(native_evidence=True, protocol_sha256=frozen["protocol_sha256"],
                          frozenclient_executable_sha256=protocol["client"]["sha256"],
                          client_workspace=str(workspace),
                          prompt_sha256=hashlib.sha256(prompt).hexdigest(),
                          guidance_sha256=hashlib.sha256(guidance.encode()).hexdigest(),
                          configuration=({"mode": "managed", "role": "a" if task["phase"] == "learn" else "b",
                                          "files": []} if arm == "colony" else None),
                          args=_client_args(protocol, root / "active" / arm / "gateway", team,
                                            task, root, arm, env, source_files, client_workspace=workspace))
        put(invocation_path, invocation)
        transitions.append({"arm": arm, "workload": "gateway", "task": "trace",
                            "snapshot": "v1", "files_sha256": digest(source_files)})
    collection = {"native_evidence": True, "status": "complete",
                  "protocol_sha256": frozen["protocol_sha256"],
                  "preflight_sha256": hashlib.sha256((root / "preflight.json").read_bytes()).hexdigest(),
                  "initial_empty_stores": {"no_colony": True, "colony": True},
                  "transitions": transitions}
    put(root / "collection.json", collection)
    return collection


@pytest.mark.parametrize("entrypoint", ["mask", "report"])
def test_native_scoring_rejects_changed_collector_before_new_artifact(run, entrypoint):
    root, quality, _ = run
    native_receipt_control(root, quality)
    frozen = json.loads((root / "run.json").read_text())
    files = frozen["protocol"]["collector"]["files"]
    files[next(iter(files))] = "0" * 64
    frozen["protocol_sha256"] = digest(frozen["protocol"])
    put(root / "run.json", frozen)
    with pytest.raises(ValueError, match="complete active collector"):
        if entrypoint == "mask":
            mask(root, root / "refused-mask")
        else:
            report(root, root / "quality.json", root / "refused.json")
    assert not (root / "refused-mask").exists() and not (root / "refused.json").exists()


def test_native_receipt_accounting_does_not_publish_unresolved_ratio(run):
    root, quality, _ = run
    native_receipt_control(root, quality)
    def add_call(events):
        events.insert(1, {"type": "item.completed", "item": {"id": "native-call", "type": "mcp_tool_call",
                          "server": "colony", "tool": "ask", "status": "completed",
                          "result": {"content": [{"type": "text", "text": json.dumps({
                              "hit": True, "status": "validated",
                              "current_repository_reads": [{"path": "settings.py"}]})}]}}})
        events[-1]["usage"]["input_tokens"] = 80
    rewrite_events(root, "b", add_call)
    result = report(root, root / "quality.json", root / "schema-control.json")
    assert result["observed_token_reduction"] is None
    assert result["arms"]["colony"]["total_usage"]["gross_tokens"] == 100
    assert result["arms"]["no_colony"]["total_usage"]["gross_tokens"] == 120
    assert result["supported_product_win"] == "not_established"
    assert result["certifying"] is False
    assert result["answer_quality_complete"] is False
    assert result["mechanism_complete"] is None
    assert result["workflow_success"] is result["quality_complete"] is False


@pytest.mark.parametrize("structure_pass,mechanism_pass", [
    (True, True), (True, False), (False, True), (False, False),
])
def test_answer_and_mechanism_diagnostics_preserve_strict_native_gate(
    run, structure_pass, mechanism_pass,
):
    """Fabricated native receipts test report logic, never product performance."""
    root, quality, _ = run
    frozen = json.loads((root / "run.json").read_text())
    frozen["protocol"]["track"] = "controlled"
    frozen["protocol"]["workloads"][0]["tasks"][0]["expected_use"] = "required_miss"
    put(root / "run.json", frozen)
    answer = '{"facts":{},"sources":[]}' if structure_pass else '{"facts":{}}'
    json_answer_control(run, answer)
    native_receipt_control(root, quality)

    def ask(events):
        reason = "no_relevant_trail" if mechanism_pass else "budget_limited"
        events.insert(1, {"type": "item.completed", "item": {
            "id": "actual-miss-call", "type": "mcp_tool_call", "server": "colony",
            "tool": "ask", "arguments": {"question": "Fixture question"},
            "status": "completed", "result": {"content": [{
                "type": "text", "text": json.dumps({"hit": False, "reason": reason}),
            }]},
        }})

    rewrite_events(root, "b", ask)
    value = report(root, root / "quality.json", root / "diagnostics-report.json")
    candidate = next(row for row in value["attempts"] if row["arm"] == "colony")
    assert candidate["structural_format_compliant"] is structure_pass
    assert candidate["quality_state"] == ("unresolved" if structure_pass else "failed")
    assert candidate["answer_quality_successful"] is False
    assert candidate["expected_use_verified"] is mechanism_pass
    assert candidate["successful"] is False
    assert value["answer_quality_complete"] is False
    assert value["mechanism_complete"] is mechanism_pass
    assert value["quality_complete"] is value["workflow_success"] is False
    assert value["controlled_mechanism_pass"] is False
    assert value["observed_token_reduction"] is None
    assert value["useful_reuse"] is False
    assert value["tool_policy_compliant"] is True
    assert all(value["arms"][arm]["total_usage"]["gross_tokens"] == 120
               for arm in ("no_colony", "colony"))


def test_marker_only_publication_cannot_pass_report_mechanism(run):
    """A fabricated receipt is a negative scorer control, never product evidence.

    The old count-based report accepted stored=True plus a failed research call.
    The complete report path must now require the byte-bound publication proof.
    """
    root, quality, _ = run
    frozen = json.loads((root / "run.json").read_text())
    frozen["protocol"]["track"] = "controlled"
    task = frozen["protocol"]["workloads"][0]["tasks"][0]
    task.update(scenario="cold_learning", expected_use="required_publication")
    put(root / "run.json", frozen)
    native_receipt_control(root, quality)
    def markers(events):
        calls = [
            {"id": "failed-read", "server": "repository", "tool": "fixture_read", "status": "failed"},
            {"id": "miss", "server": "colony", "tool": "ask", "status": "completed",
             "arguments": {"question": "Fixture question"},
             "result": {"content": [{"type": "text", "text": '{"hit":false,"reason":"no_relevant_trail"}'}]}},
            {"id": "push", "server": "colony", "tool": "push", "status": "completed",
             "result": {"content": [{"type": "text", "text": '{"stored":true,"id":"made-up"}'}]}},
        ]
        events[1:1] = [{"type": "item.completed", "item": {"type": "mcp_tool_call", **call}} for call in calls]
    rewrite_events(root, "b", markers)
    value = report(root, root / "quality.json", root / "marker-report.json")
    candidate = next(row for row in value["attempts"] if row["arm"] == "colony")
    assert candidate["activity"]["stored"] == 1 and candidate["activity"]["research_calls"] == 1
    assert not candidate["expected_use_verified"]
    assert not candidate["mechanism_proof"]["publication"]
    assert not value["controlled_mechanism_pass"]
    assert candidate["answer_quality_successful"] is False and not candidate["successful"]
    assert value["answer_quality_complete"] is False
    assert value["mechanism_complete"] is False
    assert value["workflow_success"] is value["quality_complete"] is False
    assert value["observed_token_reduction"] is None
    assert value["arms"]["colony"]["total_usage"]["gross_tokens"] == 120


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


@pytest.mark.parametrize("identity", ["a", "b"])
def test_structural_failure_and_unresolved_semantics_block_either_arm_without_dropping_cost(run, identity):
    """Neither a mechanical failure nor unresolved semantics establishes success."""
    root, quality, _ = run
    json_answer_control(run, '{"facts":{},"sources":[]}')
    invalid = '{"facts":{}}'
    def invalidate(events):
        events[-2]["item"]["text"] = invalid
    rewrite_events(root, identity, invalidate)
    next(label for label in quality["labels"] if label["id"] == identity)["answer_sha256"] = (
        hashlib.sha256(invalid.encode()).hexdigest())
    native_receipt_control(root, quality)

    def ask(events):
        events.insert(1, {"type": "item.completed", "item": {
            "id": "charged-lookup", "type": "mcp_tool_call", "server": "colony",
            "tool": "ask", "arguments": {"question": "Fixture question"},
            "status": "completed", "result": {"content": [{"type": "text",
                "text": '{"hit": false, "reason": "no_relevant_trail"}'}]},
        }})
    rewrite_events(root, "b", ask)
    result = report(root, root / "quality.json", root / "symmetric-unresolved.json")
    selected = {row["id"]: row for row in result["attempts"]}
    assert selected[identity]["quality_state"] == "failed"
    assert selected["b" if identity == "a" else "a"]["quality_state"] == "unresolved"
    assert all(row["answer_quality_successful"] is False for row in selected.values())
    assert result["adoption"] and result["tool_policy_compliant"]
    assert not result["answer_quality_complete"] and not result["workflow_success"]
    assert result["observed_token_reduction"] is None
    assert all(result["arms"][arm]["total_usage"]["gross_tokens"] == 120
               and result["arms"][arm]["successful_workflows"] == 0
               and result["arms"][arm]["gross_tokens_per_successful_workflow"] is None
               for arm in ("colony", "no_colony"))
