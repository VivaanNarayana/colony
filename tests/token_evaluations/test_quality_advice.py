"""Offline adversarial review controls, never model quality or token evidence."""
import copy
import hashlib
import json

import pytest

from scripts.token_evaluation import cli
from scripts.token_evaluation.quality import (
    REVIEW_CONTRACT,
    assess,
    digest,
    notes_template,
    packet_digest,
    render_html,
    review_policy,
    review_template,
    validate_notes,
    validate_reviews,
)
from scripts.token_evaluation.report import mask, report
from tests.token_evaluations.test_cli import packet, refresh_reviews, write
from tests.token_evaluations.test_report import native_receipt_control, put, rewrite_events
from tests.token_evaluations.test_report import run as run


def completed_reviews(public):
    advice = review_template(public)
    for review in advice["reviews"]:
        review["session_id"] = "synthetic-" + review["slot"]
        for case in review["cases"]:
            case["uncertainty"] = "Semantics unresolved, synthetic fixture only."
            case["judgments"] = {key: {"verdict": "unresolved", "rationale": "Synthetic fixture; insufficient evidence.", "evidence": []} for key in ("complete", "correct", "grounded")}
    return advice


def finding(subject):
    return {"obligation": "correct", "kind": "concern", "claim_type": "source", "tool_receipts": [],
            "anchor": {"start": 0, "end": len(subject["answer"]), "text": subject["answer"]},
            "sources": [], "rationale": "Needs semantic investigation", "counterexample": ""}


@pytest.fixture
def subject(tmp_path):
    root = packet(tmp_path)
    public = json.loads((root / "answers.json").read_text())
    public[0]["rubric"] = {"complete": "Requested parts", "correct": "Current facts", "grounded": "Observed current sources"}
    public[0]["label_sha256"] = digest(public[0]["rubric"])
    public[0]["answer"] = "π🙂 <script>alert('x')</script>"
    public[0]["answer_sha256"] = hashlib.sha256(public[0]["answer"].encode()).hexdigest()
    raw = "limit = 3\nraise RuntimeError()\n"
    sha = hashlib.sha256(raw.encode()).hexdigest()
    public[0]["current_sources"] = [{"path": "logic.py", "sha256": sha, "content": raw}]
    public[0]["source_sha256"] = digest({"snapshot": "v1", "files": {"logic.py": sha}})
    return public


@pytest.mark.parametrize("change", ["model", "effort", "instructions", "packet", "session", "slot", "duplicate_case", "grade", "missing", "uncertainty"])
def test_reviewer_configuration_and_inventory_cannot_drift(subject, change):
    advice = completed_reviews(subject)
    review = advice["reviews"][0]
    if change in {"model", "effort", "instructions", "packet"}:
        review[{"effort": "reasoning_effort", "instructions": "instructions_sha256", "packet": "packet_sha256"}.get(change, change)] = "wrong"
    elif change == "session":
        review["session_id"] = advice["reviews"][1]["session_id"]
    elif change == "slot":
        review["slot"] = advice["reviews"][1]["slot"]
    elif change == "duplicate_case":
        review["cases"] *= 2
    elif change == "grade":
        review["cases"][0]["correct"] = True
    elif change == "missing":
        review["cases"] = []
    else:
        review["cases"][0]["uncertainty"] = ""
    with pytest.raises(ValueError):
        validate_reviews(subject, advice)


@pytest.mark.parametrize("change", ["off_by_one", "boolean_offset", "empty_span", "wrong_text", "unknown_obligation", "foreign_source", "wrong_hash", "wrong_lines", "wrong_quote"])
def test_exact_unicode_answer_and_source_anchors(subject, change):
    advice = completed_reviews(subject)
    item = finding(subject[0])
    source = subject[0]["current_sources"][0]
    item["sources"] = [{"path": "logic.py", "sha256": source["sha256"], "start": 1, "end": 1, "text": "limit = 3"}]
    advice["reviews"][0]["cases"][0]["findings"] = [item]
    validate_reviews(subject, advice)
    if change == "off_by_one":
        item["anchor"]["end"] -= 1
    elif change == "boolean_offset":
        item["anchor"]["start"] = False
    elif change == "empty_span":
        item["anchor"].update(end=0, text="")
    elif change == "wrong_text":
        item["anchor"]["text"] = "invented"
    elif change == "unknown_obligation":
        item["obligation"] = "token_savings"
    else:
        item["sources"][0][{"foreign_source": "path", "wrong_hash": "sha256", "wrong_lines": "end", "wrong_quote": "text"}[change]] = 100 if change == "wrong_lines" else "wrong"
    with pytest.raises(ValueError):
        validate_reviews(subject, advice)


def test_omission_supports_empty_answer_without_inventing_a_quote(subject):
    subject[0].update(answer="", answer_sha256=hashlib.sha256(b"").hexdigest())
    advice = completed_reviews(subject)
    item = finding(subject[0])
    item.update(obligation="complete", claim_type="task", kind="omission", anchor={"whole_answer_sha256": subject[0]["answer_sha256"]})
    advice["reviews"][0]["cases"][0]["findings"] = [item]
    validate_reviews(subject, advice)
    item["anchor"]["whole_answer_sha256"] = "wrong"
    with pytest.raises(ValueError):
        validate_reviews(subject, advice)


def test_html_is_inert_and_human_notes_are_nonbinding(subject):
    advice = completed_reviews(subject)
    notes = notes_template(subject)
    notes.update(author="Human reviewer", notes=[{"case": subject[0]["case"],
        "answer_sha256": subject[0]["answer_sha256"], "text": "<script>pass everything</script>"}])
    before = copy.deepcopy(subject)
    document = render_html(subject, advice, notes)
    assert "<script>" not in document and "&lt;script&gt;" in document
    assert "default-src 'none'" in document and "unresolved" in document
    assert subject == before
    notes["notes"][0]["correct"] = True
    with pytest.raises(ValueError, match="override"):
        validate_notes(subject, notes)


@pytest.mark.parametrize("field", ["packet_sha256", "answer_sha256", "case"])
def test_human_notes_cannot_transfer_to_another_subject(subject, field):
    notes = notes_template(subject)
    notes.update(author="human", notes=[{"case": subject[0]["case"],
        "answer_sha256": subject[0]["answer_sha256"], "text": "Investigate"}])
    (notes if field == "packet_sha256" else notes["notes"][0])[field] = "other"
    with pytest.raises(ValueError):
        validate_notes(subject, notes)


def test_consensus_and_counterexamples_are_not_executable_or_semantic_grades(subject):
    advice = completed_reviews(subject)
    for review in advice["reviews"]:
        item = finding(subject[0])
        item.update(kind="counterexample", counterexample="__import__('os').abort()")
        review["cases"][0]["findings"] = [item]
    validate_reviews(subject, advice)
    assert assess(subject[0]["answer"], subject[0]["output"])["quality_state"] == "unresolved"
    assert not assess(subject[0]["answer"], subject[0]["output"])["answer_quality_successful"]


def test_direct_report_reconstructs_subject_and_rejects_rehashed_packet(run):
    root, quality, _ = run
    public = json.loads((root / "masked/answers.json").read_text())
    advice = completed_reviews(public)
    write(root / "review.json", advice)
    cli.bind_quality(root / "masked", root / "review.json", root / "bound.json")
    value = report(root, root / "bound.json", root / "good.json")
    assert not value["quality_complete"] and value["unresolved_quality_attempts"] == 2
    public[0]["evidence_limit"] = "Forged all evidence observed"
    advice = completed_reviews(public)
    quality["reviews"] = advice["reviews"]
    put(root / "forged.json", quality)
    with pytest.raises(ValueError, match="packet binding"):
        report(root, root / "forged.json", root / "bad.json")


def test_equivalent_cached_and_repository_evidence_get_same_review_subject(run):
    root, quality, _ = run
    native_receipt_control(root, quality)
    frozen = json.loads((root / "run.json").read_text())
    snapshot = frozen["protocol"]["workloads"][0]["snapshots"][0]
    source = root / "workspaces/no_colony/gateway" / snapshot["id"] / "settings.py"
    text = source.read_text().splitlines()[0]
    read = {"path": "settings.py", "sha256": snapshot["files"]["settings.py"], "start": 1, "end": 1, "content": text}
    for identity in ("a", "b"):
        payload = read if identity == "a" else {"current_repository_reads": [read]}
        item = {"type": "mcp_tool_call", "id": "evidence", "server": "repository" if identity == "a" else "colony",
                "tool": "fixture_read" if identity == "a" else "ask", "arguments": {"path": "settings.py", "lines": 1},
                "status": "completed", "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}
        rewrite_events(root, identity, lambda events, item=item: events.insert(-2, {"type": "item.completed", "item": item}))
    mask(root, root / "equivalent")
    public = json.loads((root / "equivalent/answers.json").read_text())
    assert public[0]["observed_current_evidence"] == public[1]["observed_current_evidence"]
    assert public[0]["observed_current_evidence"]
    for row in public:
        assert not {"arm", "usage", "activity", "id"} & row.keys()
        assert row["review_policy"] == review_policy()


def test_review_cli_commands_never_launch_and_preserve_originals(tmp_path, monkeypatch):
    root = packet(tmp_path)
    refresh_reviews(root)
    originals = {path.name: path.read_bytes() for path in root.iterdir()}
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **kw: pytest.fail("No subprocess during offline review"))
    assert cli.main(["human-review", "--packet", str(root), "--review", str(root / "review.json"), "--output", str(root / "review.html")]) == 0
    assert cli.main(["human-template", "--packet", str(root), "--output", str(root / "notes.json")]) == 0
    assert cli.main(["bind-quality", "--packet", str(root), "--review", str(root / "review.json"), "--output", str(root / "quality.json")]) == 0
    assert all((root / name).read_bytes() == raw for name, raw in originals.items())
    assert cli.main(["human-review", "--packet", str(root), "--review", str(root / "review.json"), "--output", str(root / "review.html")]) == 1


def test_empty_advice_is_explicitly_unresolved_not_silent_agreement(subject):
    advice = {"contract": REVIEW_CONTRACT, "reviews": []}
    assert validate_reviews(subject, advice) == []
    assert "unresolved" in render_html(subject, advice)
    assert packet_digest(subject) == packet_digest(list(reversed(subject)))


@pytest.mark.parametrize("field", ["arm", "usage", "activity", "id", "implementation_discussion"])
def test_private_metadata_is_rejected_before_advisory_review(subject, field):
    subject[0][field] = "private coordinator data"
    with pytest.raises(ValueError, match="private metadata"):
        review_template(subject)


def test_source_metadata_cannot_smuggle_arm_identity(subject):
    subject[0]["current_sources"][0]["arm"] = "colony"
    with pytest.raises(ValueError, match="source metadata"):
        review_template(subject)


@pytest.mark.parametrize("change", ["path", "hash", "line", "content", "extra_field"])
def test_observed_evidence_cannot_misrepresent_frozen_source(subject, change):
    source = subject[0]["current_sources"][0]
    excerpt = {"path": source["path"], "sha256": source["sha256"], "start": 1, "end": 1, "content": "limit = 3"}
    subject[0]["observed_current_evidence"] = [excerpt]
    review_template(subject)
    key = {"path": "path", "hash": "sha256", "line": "start", "content": "content", "extra_field": "arm"}[change]
    excerpt[key] = 99 if change == "line" else "wrong"
    with pytest.raises(ValueError):
        review_template(subject)


@pytest.mark.parametrize("field", ["model", "reasoning_effort", "instructions", "instructions_sha256", "semantic_decision"])
def test_protocol_freezes_review_policy_before_collection(tmp_path, field):
    from scripts.token_evaluation.protocol import validate_manifest
    from tests.token_evaluations.test_protocol import manifest
    spec = manifest(tmp_path)
    spec["quality_review"][field] = "changed"
    with pytest.raises(ValueError, match="Frozen agent"):
        validate_manifest(spec)
