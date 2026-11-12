"""Prospective receipt/completion controls; no model calls or historical regrading."""
import copy
import hashlib
import json

import pytest

from scripts.token_evaluation import cli
from scripts.token_evaluation.protocol import CONTRACT, validate_contract
from scripts.token_evaluation.quality import (
    BINDINGS,
    assess,
    packet_digest,
    review_template,
    validate_packet,
    validate_reviews,
)
from scripts.token_evaluation.report import mask, report
from scripts.token_evaluation.rubrics import DIMENSIONS, prospective_rubric, split_workload_rubrics
from scripts.token_evaluation.tool_receipts import (
    LIMITS,
    MAX_PATHS,
    MAX_RECEIPTS,
    MAX_TEXT,
    collect,
    digest,
    validate,
)
from tests.token_evaluations.test_agent_quality import (
    build_reviewed_run,
    judgments,
    score,
    set_verdict,
)
from tests.token_evaluations.test_quality_advice import subject as subject
from tests.token_evaluations.test_report import put, rewrite_events
from tests.token_evaluations.test_report import run as run


def call(tool, args=None, payload=None, *, status="completed", blocks=None):
    if blocks is None:
        blocks = [] if payload is None else [{"type": "text", "text": json.dumps(payload)}]
    server, name = tool.split(".")
    return {"type": "item.completed", "item": {
        "id": "private-native-item", "type": "mcp_tool_call", "server": server, "tool": name,
        "arguments": args or {}, "status": status, "result": {"content": blocks},
    }}


def final():
    return {"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}}


def project(events, files=None):
    result = collect([*events, final()], files or {})
    validate(result["tool_receipts"], result["tool_receipt_limits"], files or {})
    return result


def bind_projection(subject, projection):
    subject.update(projection, tool_receipts_sha256=digest(projection))


def test_real_outcomes_are_retained_without_promoting_them_to_source_coverage():
    result = project([
        call("colony.ask", {"question": "Any measurement?"}, {"hit": False, "reason": "no_relevant_trail"}),
        call("repository.fixture_files", blocks=[{"type": "text", "text": "one.py\ntwo.py"}]),
        call("repository.fixture_search", {"query": "sample"}),
        call("repository.fixture_read", {"path": "one.py"}, status="failed",
             blocks=[{"type": "text", "text": "Research call limit reached"}]),
    ], {"one.py": "a" * 64, "two.py": "b" * 64})
    receipts = result["tool_receipts"]
    assert receipts[0]["outcome"]["hit"] is False
    assert receipts[0]["outcome"]["reason"] == "no_relevant_trail"
    assert receipts[1]["outcome"]["returned_paths"] == ["one.py", "two.py"]
    assert receipts[2]["arguments"] == {"query": "sample"}
    assert receipts[2]["outcome"]["empty"] is True
    assert receipts[3]["outcome"]["error"] == "research_call_limit"
    assert "observed_current_evidence" not in result
    assert "not semantic proof" in result["tool_receipt_limits"]["meaning"]


def test_projection_is_allowlisted_redacted_bounded_and_before_final_only():
    secret = "sk-proj-" + "A" * 30
    event = call("colony.ask", {
        "question": "key " + secret + " file C:/private/auth.json",
        "namespace": "PRIVATE_ARM",
    }, {"hit": False, "reason": "no_relevant_trail", "content": "UNTRUSTED_SECRET",
        "producer_id": "PRIVATE_PRODUCER", "usage": {"gross_tokens": 999}})
    result = collect([event, final(), call("colony.push", payload={"stored": True})], {})
    text = json.dumps(result)
    assert all(s not in text for s in
               [secret, "C:/private", "PRIVATE_ARM", "PRIVATE_PRODUCER", "UNTRUSTED_SECRET",
                "gross_tokens", "private-native-item"])
    assert "[REDACTED]" in text and "[REDACTED_PATH]" in text
    assert len(result["tool_receipts"]) == 1
    paths = {f"file-{n}.py": "a" * 64 for n in range(MAX_PATHS + 2)}
    limited = project([call("repository.fixture_files", blocks=[
        {"type": "text", "text": "\n".join(paths)}])], paths)
    assert len(limited["tool_receipts"][0]["outcome"]["returned_paths"]) == MAX_PATHS
    assert limited["tool_receipts"][0]["outcome"]["paths_truncated"]
    many = project([call("colony.ask", {"question": "x" * (MAX_TEXT + 1)}, {"hit": False})
                    for _ in range(MAX_RECEIPTS + 2)])
    assert len(many["tool_receipts"]) == MAX_RECEIPTS
    assert many["tool_receipt_limits"]["omitted_calls"] == 2
    assert many["tool_receipts"][0]["outcome"]["arguments_truncated"]


@pytest.mark.parametrize("blocks", [
    [{"type": "text", "text": "not-json"}],
    [{"type": "image", "data": "not-search-evidence"}],
    [{"type": "text", "text": '{"path":"../private/auth.json","matches":[]}'}],
])
def test_unavailable_or_malformed_search_result_is_not_an_empty_result(blocks):
    result = project([call("repository.fixture_search", {"query": "q"}, blocks=blocks)])
    assert not result["tool_receipts"][0]["outcome"]["result_available"]
    assert "empty" not in result["tool_receipts"][0]["outcome"]


@pytest.mark.parametrize("field", ["event_sha256", "arguments", "outcome", "extra"])
def test_receipt_changes_invalidate_subject_binding(subject, field):
    bind_projection(subject[0], project([call("colony.ask", {"question": "q"}, {"hit": False})]))
    receipt = subject[0]["tool_receipts"][0]
    if field == "event_sha256":
        receipt[field] = "a" * 64
    elif field == "arguments":
        receipt[field]["question"] = "changed"
    elif field == "outcome":
        receipt[field]["hit"] = True
    else:
        receipt["private_metadata"] = "forbidden"
    with pytest.raises(ValueError):
        validate_packet(subject)


def procedural_reviews(subject):
    row = subject[0]
    receipt = row["tool_receipts"][0]
    submission = review_template(subject)
    for review in submission["reviews"]:
        review["session_id"] = "synthetic-" + review["slot"]
        case = review["cases"][0]
        case["uncertainty"] = "Synthetic evidence/schema control only."
        for key in ("complete", "correct", "grounded"):
            finding = {
                "obligation": key, "claim_type": "task" if key == "complete" else "tool",
                "kind": "omission" if key == "complete" else "support",
                "anchor": ({"whole_answer_sha256": row["answer_sha256"]} if key == "complete" else
                           {"start": 0, "end": len(row["answer"]), "text": row["answer"]}),
                "sources": [],
                "tool_receipts": [{"id": receipt["id"], "sha256": digest(receipt)}],
                "rationale": "Task left incomplete." if key == "complete" else "Receipt supports the narrow query outcome.",
                "counterexample": "",
            }
            case["judgments"][key] = {
                "verdict": "failed" if key == "complete" else "passed",
                "rationale": finding["rationale"], "evidence": [len(case["findings"])],
            }
            case["findings"].append(finding)
    return submission


def test_honest_incomplete_answer_can_be_grounded_but_still_fails_task(subject):
    row = subject[0]
    row.update(answer="The literal search returned no matches; repository containment remains unknown.")
    row["answer_sha256"] = hashlib.sha256(row["answer"].encode()).hexdigest()
    bind_projection(row, project([call("repository.fixture_search", {"query": "sample"})]))
    submission = procedural_reviews(subject)
    validate_reviews(subject, submission)
    result = assess(row["answer"], row["output"], [r["cases"][0] for r in submission["reviews"]])
    assert result["quality_obligations"] == {
        "complete": "failed", "correct": "passed", "grounded": "passed", "format": "passed"}
    assert result["quality_state"] == "failed" and not result["answer_quality_successful"]


def test_task_omission_cannot_be_reported_as_a_grounding_failure(subject):
    bind_projection(subject[0], project([call("repository.fixture_search", {"query": "sample"})]))
    submission = procedural_reviews(subject)
    case = submission["reviews"][0]["cases"][0]
    case["findings"][0].update(obligation="grounded", claim_type="tool")
    with pytest.raises(ValueError, match="Missing deliverables"):
        validate_reviews(subject, submission)


def test_miss_receipt_cannot_substitute_for_source_support(subject):
    bind_projection(subject[0], project([call("colony.ask", {"question": "q"}, {"hit": False})]))
    submission = procedural_reviews(subject)
    for review in submission["reviews"]:
        review["cases"][0]["findings"][2]["claim_type"] = "source"
    with pytest.raises(ValueError, match="Source claims require"):
        validate_reviews(subject, submission)


@pytest.mark.parametrize("change", ["foreign_id", "wrong_hash"])
def test_procedural_pass_needs_the_exact_recorded_receipt(subject, change):
    bind_projection(subject[0], project([call("colony.ask", {"question": "q"}, {"hit": False})]))
    submission = procedural_reviews(subject)
    reference = submission["reviews"][0]["cases"][0]["findings"][1]["tool_receipts"][0]
    reference["id" if change == "foreign_id" else "sha256"] = "wrong"
    with pytest.raises(ValueError, match="receipt reference"):
        validate_reviews(subject, submission)


def test_end_to_end_receipts_are_bound_and_reconstructed_from_raw_log(run):
    root, public, _ = build_reviewed_run(run)
    # Create a new provider-free subject with actual lookup/search/denial events.
    def insert(events):
        events[1:1] = [
            call("colony.ask", {"question": "Fixture question"}, {"hit": False, "reason": "no_relevant_trail"}),
            call("repository.fixture_search", {"query": "absent"}),
            call("repository.fixture_read", {"path": "settings.py"}, status="failed",
                 blocks=[{"type": "text", "text": "Research call limit reached"}]),
        ]
        for n, e in enumerate(events[1:4]):
            e["item"]["id"] = "observed-" + str(n)
    rewrite_events(root, "b", insert)
    mask(root, root / "new-packet")
    new_public = json.loads((root / "new-packet/answers.json").read_text())
    target = next(row for row in new_public if len(row["tool_receipts"]) == 4)
    assert any(r["outcome"].get("empty") for r in target["tool_receipts"])
    assert any(r["outcome"].get("error") == "research_call_limit" for r in target["tool_receipts"])
    # Forge a receipt and honestly recompute all public/private hashes. Raw-log reconstruction
    # must still reject it; a self-consistent edited packet is not authoritative evidence.
    target["tool_receipts"][0]["outcome"]["hit"] = True
    target["tool_receipts_sha256"] = digest({k: target[k] for k in ("tool_receipts", "tool_receipt_limits")})
    private = json.loads((root / "new-packet/private-map.json").read_text())
    next(r for r in private if r["case"] == target["case"])["tool_receipts_sha256"] = target["tool_receipts_sha256"]
    put(root / "new-packet/answers.json", new_public)
    put(root / "new-packet/private-map.json", private)
    submission = judgments(new_public)
    put(root / "new-reviews.json", submission)
    cli.bind_quality(root / "new-packet", root / "new-reviews.json", root / "forged-quality.json")
    with pytest.raises(ValueError, match="tool_receipts_sha256"):
        report(root, root / "forged-quality.json", root / "must-not-exist.json")
    assert not (root / "must-not-exist.json").exists()


def test_prospective_contract_rejects_legacy_packets_without_upgrading_them(subject):
    assert CONTRACT == "colony.token-evaluation/9"
    before = copy.deepcopy(subject)
    legacy = {"contract": "colony.token-evaluation/8"}
    with pytest.raises(ValueError, match="original pinned collector"):
        validate_contract(legacy)
    advice = review_template(subject)
    advice["contract"] = "colony.quality-review/1"
    with pytest.raises(ValueError, match="Agent review contract"):
        validate_reviews(subject, advice)
    assert subject == before
    assert "tool_receipts_sha256" in BINDINGS
    assert LIMITS["contract"] == "colony.review-tool-receipts/1"


def test_missing_completion_is_not_defaulted_to_a_pass(subject):
    bind_projection(subject[0], project([call("colony.ask", {"question": "q"}, {"hit": False})]))
    advice = procedural_reviews(subject)
    advice["reviews"][0]["cases"][0]["judgments"].pop("complete")
    with pytest.raises(ValueError, match="completion"):
        validate_reviews(subject, advice)


def test_receipt_order_is_part_of_packet_identity(subject):
    projection = project([call("colony.ask", {"question": "first"}, {"hit": False}),
                          call("colony.ask", {"question": "second"}, {"hit": False})])
    bind_projection(subject[0], projection)
    before = packet_digest(subject)
    subject[0]["tool_receipts"].reverse()
    assert packet_digest(subject) != before
    with pytest.raises(ValueError):
        validate_packet(subject)


@pytest.mark.parametrize("flag", ["isError", "is_error"])
def test_error_result_is_not_a_successful_empty_search(flag):
    event = call("repository.fixture_search", {"query": "q"})
    event["item"]["result"][flag] = True
    result = project([event])
    assert result["tool_receipts"][0]["outcome"]["is_error"]
    assert "empty" not in result["tool_receipts"][0]["outcome"]


@pytest.mark.parametrize("field,value", [
    ("tool", []), ("event_sha256", None), ("arguments", {"query": {"usage": 999}}),
    ("outcome", {"returned_paths": [None]}),
])
def test_malformed_public_receipt_is_rejected(subject, field, value):
    projection = project([call("repository.fixture_search", {"query": "q"})])
    projection["tool_receipts"][0][field] = value
    bind_projection(subject[0], projection)
    with pytest.raises(ValueError):
        validate_packet(subject)


def test_rubric_split_is_prospective_and_preserves_workload_and_oracles():
    from pathlib import Path

    path = Path(__file__).parent / "fixtures/catalog.json"
    original_bytes = path.read_bytes()
    original = json.loads(original_bytes)
    prospective = copy.deepcopy(original)
    split_workload_rubrics(prospective["workloads"])
    assert len(prospective["workloads"]) == 2
    for before, after in zip(original["workloads"], prospective["workloads"], strict=True):
        for btask, atask in zip(before["tasks"], after["tasks"], strict=True):
            assert set(btask["rubric"]) == {"correct", "grounded"}
            assert set(atask["rubric"]) == set(DIMENSIONS)
            assert {k: v for k, v in btask.items() if k != "rubric"} == {
                k: v for k, v in atask.items() if k != "rubric"}
        assert before["snapshots"] == after["snapshots"]
    assert path.read_bytes() == original_bytes


def test_incomplete_task_does_not_become_ungrounded_or_qualify_savings(run):
    root, _, submission = build_reviewed_run(run)
    private = json.loads((root / "reviewed/private-map.json").read_text())
    case_id = next(row["case"] for row in private if row["id"] == "b")
    for review in submission["reviews"]:
        case = next(c for c in review["cases"] if c["case"] == case_id)
        set_verdict(case, "complete", "failed")
    result = score(root, submission)
    candidate = next(r for r in result["attempts"] if r["id"] == "b")
    assert candidate["quality_obligations"]["complete"] == "failed"
    assert candidate["quality_obligations"]["correct"] == "passed"
    assert candidate["quality_obligations"]["grounded"] == "passed"
    assert result["correctness_complete"] and result["grounding_complete"]
    assert not result["task_completion_complete"] and not result["quality_complete"]
    assert result["observed_token_reduction"] is None
    assert result["arms"]["colony"]["known_usage"]["gross_tokens"] == 100


def test_rehashed_review_cannot_turn_unknown_history_into_a_new_protocol(subject):
    legacy = copy.deepcopy(subject)
    legacy[0]["review_policy"]["contract"] = "colony.agent-quality/1"
    before = copy.deepcopy(legacy)
    with pytest.raises(ValueError, match="Review policy changed"):
        review_template(legacy)
    assert legacy == before


def test_unknown_custom_rubric_is_not_silently_replaced():
    custom = {"correct": "An additional custom correctness constraint.", "grounded": "Observed evidence."}
    before = copy.deepcopy(custom)
    with pytest.raises(ValueError, match="explicit complete/correct/grounded"):
        prospective_rubric(custom)
    assert custom == before
    explicit = {**custom, "complete": "Address all requested custom deliverables."}
    assert prospective_rubric(explicit) == explicit
