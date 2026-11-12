"""Synthetic review/receipt controls, not a model evaluation or accuracy claim."""
import copy
import hashlib
import json

import pytest

from scripts.token_evaluation import cli
from scripts.token_evaluation.quality import (
    REVIEW_CONTRACT,
    assess,
    render_html,
    review_template,
    validate_reviews,
)
from scripts.token_evaluation.report import mask, report
from tests.token_evaluations.test_cli import packet, refresh_reviews, write
from tests.token_evaluations.test_quality_advice import subject as subject
from tests.token_evaluations.test_report import native_receipt_control, put, rewrite_events
from tests.token_evaluations.test_report import run as run


def judgments(public):
    """Fabricate fully bound positive controls; this does NOT run a reviewer model."""
    submission = review_template(public)
    subjects = {row["case"]: row for row in public}
    for review in submission["reviews"]:
        review["session_id"] = "synthetic-" + review["slot"]
        for case in review["cases"]:
            row = subjects[case["case"]]
            source = row["current_sources"][0]
            case["uncertainty"] = "Synthetic schema control, not empirical model calibration."
            for key in ("correct", "grounded"):
                case["judgments"][key] = {"verdict": "passed", "rationale": "Current definition supports this fixture answer.",
                                          "evidence": [len(case["findings"])]}
                case["findings"].append({"obligation": key, "kind": "support",
                    "anchor": {"start": 0, "end": len(row["answer"]), "text": row["answer"]},
                    "sources": [{"path": source["path"], "sha256": source["sha256"], "start": 1, "end": 1,
                                 "text": source["content"].splitlines()[0]}],
                    "rationale": "Exact current definition", "counterexample": ""})
    return submission


def set_verdict(case, key, verdict):
    case["judgments"][key]["verdict"] = verdict
    index = case["judgments"][key]["evidence"][0]
    case["findings"][index]["kind"] = "concern" if verdict == "failed" else "support"
    case["judgments"][key]["rationale"] = "Synthetic " + verdict + " control"


@pytest.fixture
def supported_subject(subject):
    row = subject[0]
    source = row["current_sources"][0]
    row["observed_current_evidence"] = [{"path": source["path"], "sha256": source["sha256"],
        "start": 1, "end": 1, "content": source["content"].splitlines()[0]}]
    return subject


@pytest.mark.parametrize("left", ["passed", "failed", "unresolved"])
@pytest.mark.parametrize("right", ["passed", "failed", "unresolved"])
@pytest.mark.parametrize("key", ["correct", "grounded"])
def test_full_independent_judgment_truth_table(supported_subject, left, right, key):
    submission = judgments(supported_subject)
    for review, verdict in zip(submission["reviews"], (left, right), strict=True):
        set_verdict(review["cases"][0], key, verdict)
    validate_reviews(supported_subject, submission)
    cases = [review["cases"][0] for review in submission["reviews"]]
    row = supported_subject[0]
    result = assess(row["answer"], row["output"], cases)
    expected = left if left == right else "unresolved"
    assert result["quality_obligations"][key] == expected
    assert result["answer_quality_successful"] is (left == right == "passed")
    assert result["quality_state"] == expected


@pytest.mark.parametrize("mutation", ["missing_dimension", "no_rationale", "no_evidence", "foreign_index", "boolean_index",
                                     "unobserved", "cross_dimension", "contradiction", "bare_boolean", "fake_verdict"])
def test_approval_requires_complete_evidence_bound_judgments(supported_subject, mutation):
    submission = judgments(supported_subject)
    case = submission["reviews"][0]["cases"][0]
    judgment = case["judgments"]["grounded"]
    if mutation == "missing_dimension":
        case["judgments"].pop("correct")
    elif mutation == "no_rationale":
        judgment["rationale"] = ""
    elif mutation == "no_evidence":
        judgment["evidence"] = []
    elif mutation in {"foreign_index", "boolean_index", "cross_dimension"}:
        judgment["evidence"] = [{"foreign_index": 99, "boolean_index": True, "cross_dimension": 0}[mutation]]
    elif mutation == "unobserved":
        supported_subject[0]["observed_current_evidence"] = []
        # Bind the changed packet honestly, so this tests observation rather than hash rejection.
        from scripts.token_evaluation.quality import packet_digest
        for review in submission["reviews"]:
            review["packet_sha256"] = packet_digest(supported_subject)
    elif mutation == "contradiction":
        case["findings"].append({**copy.deepcopy(case["findings"][1]), "kind": "concern"})
    elif mutation == "bare_boolean":
        case["judgments"]["grounded"] = True
    else:
        judgment["verdict"] = "probably_passed"
    with pytest.raises(ValueError):
        validate_reviews(supported_subject, submission)


def build_reviewed_run(run, alteration=None, *, source_bytes=b"TIMEOUT = 1500\n", extra_sources=None):
    root, quality, _ = run
    native_receipt_control(root, quality, source_bytes=source_bytes, extra_sources=extra_sources)
    source = root / "workspaces/no_colony/gateway/v1/settings.py"
    read = {"path": "settings.py", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "start": 1, "end": 1, "content": "TIMEOUT = 1500"}
    for identity in ("a", "b"):
        payload = read if identity == "a" else {"hit": True, "status": "validated", "current_repository_reads": [read]}
        item = {"id": "observed-source", "type": "mcp_tool_call", "status": "completed",
                "server": "repository" if identity == "a" else "colony",
                "tool": "fixture_read" if identity == "a" else "ask",
                "arguments": {"path": "settings.py", "start": 1, "lines": 1},
                "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}
        def insert(events, item=item, identity=identity):
            events.insert(-2, {"type": "item.completed", "item": item})
            if identity == "b":
                events[-1]["usage"]["input_tokens"] = 80
        rewrite_events(root, identity, insert)
    if alteration:
        alteration(root, quality)
    mask(root, root / "reviewed")
    public = json.loads((root / "reviewed/answers.json").read_text())
    return root, public, judgments(public)


def score(root, submission):
    put(root / "reviewer-responses.json", submission)
    cli.bind_quality(root / "reviewed", root / "reviewer-responses.json", root / "bound.json")
    return report(root, root / "bound.json", root / "graded.json")


def test_good_grounded_answers_can_pass_end_to_end_in_both_arms(run):
    root, public, submission = build_reviewed_run(run)
    result = score(root, submission)
    assert all(row["quality_state"] == "passed" and row["answer_quality_successful"] for row in result["attempts"])
    assert result["quality_complete"] and result["tool_policy_compliant"]
    assert result["observed_token_reduction"]["gross_tokens"] == pytest.approx(1 - 100 / 120)
    assert not result["certifying"] and result["supported_product_win"] == "not_established"
    assert all(row["quality_obligations"] == {"correct": "passed", "grounded": "passed", "format": "passed"}
               for row in result["attempts"])
    assert "both_passed" in render_html(public, submission)


@pytest.mark.parametrize("identity", ["a", "b"])
@pytest.mark.parametrize("key", ["correct", "grounded"])
@pytest.mark.parametrize("verdicts,expected", [(('failed', 'failed'), 'failed'), (('passed', 'failed'), 'unresolved'),
                                              (('passed', 'unresolved'), 'unresolved')])
def test_semantic_failure_or_disagreement_blocks_either_arm_without_hiding_costs(run, identity, key, verdicts, expected):
    root, _, submission = build_reviewed_run(run)
    private = json.loads((root / "reviewed/private-map.json").read_text())
    target = next(row["case"] for row in private if row["id"] == identity)
    for review, verdict in zip(submission["reviews"], verdicts, strict=True):
        set_verdict(next(case for case in review["cases"] if case["case"] == target), key, verdict)
    value = score(root, submission)
    selected = {row["id"]: row for row in value["attempts"]}
    assert selected[identity]["quality_state"] == expected
    assert selected['b' if identity == 'a' else 'a']["answer_quality_successful"]
    assert not value["quality_complete"] and value["observed_token_reduction"] is None
    assert value["arms"]["colony"]["total_usage"]["gross_tokens"] == 100
    assert value["arms"]["no_colony"]["total_usage"]["gross_tokens"] == 120


@pytest.mark.parametrize("failure", ["format", "unfinished", "failed_session", "tool_limit", "forbidden_tool", "baseline_colony_call", "controlled_miss", "unknown_usage"])
def test_unanimous_semantic_approval_cannot_override_strict_mechanics(run, failure):
    def alter(root, quality):
        if failure in {"format", "controlled_miss"}:
            frozen = json.loads((root / "run.json").read_text())
            if failure == "format":
                frozen["protocol"]["output"]["format"] = "json"  # plain '1500' is structurally invalid
            else:
                frozen["protocol"]["track"] = "controlled"
                frozen["protocol"]["workloads"][0]["tasks"][0]["expected_use"] = "required_miss"
            put(root / "run.json", frozen)
            native_receipt_control(root, quality)
        elif failure in {"unfinished", "unknown_usage"}:
            def change(events):
                if failure == "unfinished":
                    events.pop()
                else:
                    events[-1]["usage"].pop("output_tokens")
            rewrite_events(root, "b", change)
        elif failure == "failed_session":
            path = root / "control/attempts.jsonl"
            records = [json.loads(line) for line in path.read_text().splitlines()]
            records[1]["state"] = "failed"
            path.write_text('\n'.join(map(json.dumps, records)))
        else:
            def forbidden(events):
                for n in range(7 if failure == "tool_limit" else 1):
                    events.insert(1, {"type": "item.completed", "item": {"id": "bad-" + str(n), "type": "mcp_tool_call",
                        "server": "colony" if failure == "baseline_colony_call" else "repository",
                        "tool": "fixture_files" if failure == "tool_limit" else ("ask" if failure == "baseline_colony_call" else "exec"),
                        "status": "failed", "result": {"isError": True}}})
            rewrite_events(root, "a" if failure == "baseline_colony_call" else "b", forbidden)
    root, _, submission = build_reviewed_run(run, alter)
    value = score(root, submission)
    assert value["observed_token_reduction"] is None and not value["controlled_mechanism_pass"]
    assert value["arms"]["colony"]["known_usage"]["input_tokens"] == (0 if failure == "unfinished" else 80)
    if failure in {"tool_limit", "forbidden_tool", "baseline_colony_call"}:
        assert not value["tool_policy_compliant"]
    elif failure != "unknown_usage":
        assert not value["quality_complete"]
    else:
        assert value["arms"]["colony"]["total_usage"] is None


@pytest.mark.parametrize("corruption", ["source", "prompt", "collector", "events"])
def test_unanimous_approval_cannot_override_changed_native_subject(run, corruption):
    root, _, submission = build_reviewed_run(run)
    put(root / "responses.json", submission)
    cli.bind_quality(root / "reviewed", root / "responses.json", root / "bound.json")
    paths = {"source": root / "workspaces/no_colony/gateway/v1/settings.py", "prompt": root / "prompts/a.txt",
             "events": root / "logs/a.events.jsonl"}
    if corruption in paths:
        with paths[corruption].open('a') as stream:
            stream.write('altered')
    else:
        from scripts.token_evaluation.protocol import digest
        frozen = json.loads((root / "run.json").read_text())
        frozen["protocol"]["collector"]["files"] = {}
        frozen["protocol_sha256"] = digest(frozen["protocol"])
        put(root / "run.json", frozen)
    with pytest.raises(ValueError):
        report(root, root / "bound.json", root / "refused.json")


def test_independent_responses_combine_without_rewriting_or_launching(tmp_path, monkeypatch):
    root = packet(tmp_path)
    refresh_reviews(root)
    submission = json.loads((root / "review.json").read_text())
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **kw: pytest.fail("No launch"))
    for index, review in enumerate(submission["reviews"]):
        cli.quality_template(root, root / f'template-{index}.json', review["slot"])
        template = json.loads((root / f'template-{index}.json').read_text())
        assert len(template["reviews"]) == 1 and template["reviews"][0]["slot"] == review["slot"]
        write(root / f'reviewer-{index}.json', {"contract": REVIEW_CONTRACT, "reviews": [review]})
    original = [(root / f'reviewer-{i}.json').read_bytes() for i in range(2)]
    assert cli.main(['combine-reviews', '--packet', str(root), '--first', str(root / 'reviewer-0.json'),
                     '--second', str(root / 'reviewer-1.json'), '--output', str(root / 'combined.json')]) == 0
    assert json.loads((root / 'combined.json').read_text()) == submission
    assert original == [(root / f'reviewer-{i}.json').read_bytes() for i in range(2)]
    with pytest.raises(ValueError):
        cli.combine_reviews(root, root / 'reviewer-0.json', root / 'reviewer-0.json', root / 'duplicate.json')


def test_no_concerns_or_filled_identity_does_not_imply_a_pass(supported_subject):
    submission = review_template(supported_subject)
    for review in submission["reviews"]:
        review["session_id"] = "synthetic-" + review["slot"]
        for case in review["cases"]:
            case["uncertainty"] = "None identified"
    with pytest.raises(ValueError, match="Explicit semantic verdict"):
        validate_reviews(supported_subject, submission)


def test_alternative_observed_support_is_not_required_to_match_oracle_path(supported_subject):
    from scripts.token_evaluation.quality import digest
    row = supported_subject[0]
    alternate = {**row["current_sources"][0], "path": "equivalent.py"}
    row["current_sources"].append(alternate)
    row["source_sha256"] = digest({"snapshot": row["snapshot"],
                                  "files": {s["path"]: s["sha256"] for s in row["current_sources"]}})
    row["oracle"] = {"citations": [{"path": "logic.py", "start": 1, "end": 1}]}
    row["citation_sha256"] = digest(row["oracle"])
    row["observed_current_evidence"] = [{**row["observed_current_evidence"][0], "path": "equivalent.py"}]
    submission = judgments(supported_subject)
    for review in submission["reviews"]:
        for finding in review["cases"][0]["findings"]:
            finding["sources"][0]["path"] = "equivalent.py"
    validate_reviews(supported_subject, submission)
    assert assess(row["answer"], row["output"], [r["cases"][0] for r in submission["reviews"]])["quality_state"] == "passed"
