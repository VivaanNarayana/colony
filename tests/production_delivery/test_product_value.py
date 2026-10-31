"""Fake native-event accounting only; no agents/models/performance collection."""

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import product_value as value  # noqa: E402
from production_delivery_core import put, sha  # noqa: E402


def save(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


def events(input_tokens=100, cached=20, output=10):
    return [
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "FAKE answer"}},
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": cached,
                "output_tokens": output,
                "cache_write_tokens": 7,
            },
        },
    ]


@pytest.fixture
def evidence(tmp_path):
    logs = tmp_path / "FAKE-native-logs"
    logs.mkdir()
    data = {
        "contract": value.CONTRACT,
        "protocol": {
            "comparison": "colony_vs_no_colony",
            "implementation": "1" * 40,
            "corpus": "2" * 40,
            "model": "FAKE",
            "client": "FAKE-event-format",
            "effort": "fixed",
            "instructions_sha256": "3" * 64,
            "budget": {"research_calls": 6},
            "memory_difference": "Only Colony retains sourced trails; fresh task contexts in both arms",
            "rubric_sha256": "4" * 64,
            "reviewer": "FAKE reviewer",
            "review_method": "FAKE labels",
            "workflows": [
                {
                    "id": "repo-a",
                    "tasks": [
                        {"id": "learn", "question": "Find the setting", "phase": "learn"},
                        {"id": "reuse", "question": "Use the setting", "phase": "reuse"},
                    ],
                }
            ],
        },
        "attempts": [],
    }
    for mode in value.MODES:
        for task in ["learn", "reuse"]:
            name = mode + "-" + task
            if mode == "no_colony":
                native = events()
            else:
                native = events(150, 0, 15) if task == "learn" else events(20, 5, 5)
            file = logs / (name + ".events.jsonl")
            file.write_text("\n".join(json.dumps(e) for e in native) + "\n", encoding="utf-8")
            data["attempts"].append(
                {
                    "id": name,
                    "mode": mode,
                    "workflow": "repo-a",
                    "task": task,
                    "events": file.name,
                    "events_sha256": sha(file),
                    "state": "completed",
                    "seconds": 1.0,
                    "correct": True,
                    "grounded": True,
                    "answer_sha256": hashlib.sha256(b"FAKE answer").hexdigest(),
                }
            )
    manifest = tmp_path / "manifest.json"
    save(manifest, data)
    return manifest, logs, data


def rewrite_native(logs, row, native):
    path = logs / row["events"]
    path.write_text("\n".join(json.dumps(e) for e in native) + "\n", encoding="utf-8")
    row["events_sha256"] = sha(path)


def test_full_costs_include_learning_and_cache_components_without_claiming_win(evidence):
    manifest, logs, _ = evidence
    report = value.scorecard(manifest, logs)
    a, b = [report["arms"][m] for m in value.MODES]
    assert a["total_usage"]["gross_tokens"] == 220
    assert b["total_usage"]["gross_tokens"] == 190
    assert b["phase_known_gross_tokens"] == {"learn": 165, "reuse": 25}
    assert a["total_usage"]["uncached_input_plus_output"] == 180
    assert b["total_usage"]["uncached_input_plus_output"] == 185
    assert report["observed_token_reduction"]["gross_tokens"] > 0
    assert report["observed_token_reduction"]["uncached_input_plus_output"] < 0
    assert b["grounded_successful_workflows"] == 1 and b["grounded_tasks"] == 2
    assert b["session_seconds_sum"] == 2.0
    assert report["attempts"][0]["auxiliary_usage_not_added"] == [{"cache_write_tokens": 7}]
    assert report["supported_product_win"] == "not_established"
    assert report["production_readiness"] == "not_evaluated"


def test_retry_counts_all_costs_but_success_once_and_wrong_answers_never_count(evidence):
    manifest, logs, data = evidence
    row = data["attempts"][-1]
    row["correct"] = False
    retry = {**row, "id": "retry", "events": "retry.events.jsonl", "correct": True}
    rewrite_native(logs, retry, events(30, 0, 5))
    data["attempts"].append(retry)
    save(manifest, data)
    report = value.scorecard(manifest, logs)["arms"]["colony"]
    assert report["attempts"] == 3 and report["grounded_successful_workflows"] == 1
    assert report["gross_tokens_per_successful_workflow"] == 225
    retry["grounded"] = False
    save(manifest, data)
    report = value.scorecard(manifest, logs)["arms"]["colony"]
    assert report["grounded_successful_workflows"] == 0
    assert report["gross_tokens_per_successful_workflow"] is None


@pytest.mark.parametrize(
    "failure", ["timeout", "failed", "missing_usage", "unfinished", "missing_time"]
)
def test_incomplete_observations_are_unknown_and_retained(evidence, failure):
    manifest, logs, data = evidence
    row = data["attempts"][2]  # Learning overhead cannot disappear.
    native = events(150, 0, 15)
    if failure in {"timeout", "failed"}:
        row["state"] = failure
        native += [{"type": "turn.started"}, {"type": "turn.failed", "error": "FAKE failure"}]
    elif failure == "missing_usage":
        native[-1].pop("usage")
    elif failure == "unfinished":
        native.append({"type": "turn.started"})
    else:
        row["seconds"] = None
    rewrite_native(logs, row, native)
    save(manifest, data)
    report = value.scorecard(manifest, logs)
    arm = report["arms"]["colony"]
    assert len(report["attempts"]) == 4
    if failure == "missing_time":
        assert arm["session_seconds_sum"] is None
    else:
        assert arm["total_usage"] is None and arm["unknown_usage_attempts"] == 1
        assert report["observed_token_reduction"]["gross_tokens"] is None
        assert arm["gross_tokens_per_successful_workflow"] is None
    if failure == "missing_usage":
        assert (
            arm["grounded_successful_workflows"] == 1
        )  # Quality is independent of usage coverage.


@pytest.mark.parametrize("bad", [True, -1, 1.5, float("nan")])
def test_malformed_native_accounting_is_rejected(evidence, bad):
    manifest, logs, data = evidence
    rewrite_native(logs, data["attempts"][0], events(bad))
    save(manifest, data)
    with pytest.raises(ValueError):
        value.scorecard(manifest, logs)


@pytest.mark.parametrize("missing", ["cached_input_tokens", "output_tokens"])
def test_partial_usage_keeps_known_components_without_fabricating_complete_totals(
    evidence, missing
):
    manifest, logs, data = evidence
    native = events()
    del native[-1]["usage"][missing]
    rewrite_native(logs, data["attempts"][0], native)
    save(manifest, data)
    arm = value.scorecard(manifest, logs)["arms"]["no_colony"]
    assert arm["total_usage"] is None and arm["unknown_usage_attempts"] == 1
    assert arm["known_usage"]["input_tokens"] == 200
    assert arm["known_usage"]["uncached_input_plus_output"] == 90
    assert arm["known_usage"]["gross_tokens"] == (220 if missing == "cached_input_tokens" else 110)


def test_failed_turn_usage_is_retained_in_known_costs(evidence):
    manifest, logs, data = evidence
    native = events(200, 10, 20)
    native[-1]["type"] = "turn.failed"
    rewrite_native(logs, data["attempts"][2], native)
    data["attempts"][2]["state"] = "failed"
    save(manifest, data)
    arm = value.scorecard(manifest, logs)["arms"]["colony"]
    assert arm["failed_attempts"] == 1 and arm["total_usage"] is None
    assert arm["known_usage"]["gross_tokens"] == 245
    assert arm["known_usage"]["uncached_input_plus_output"] == 230
    assert arm["grounded_successful_workflows"] == 0


@pytest.mark.parametrize("bad_order", ["attempts", "plan", "late_retry"])
def test_learning_and_retry_sequence_cannot_be_reordered(evidence, bad_order):
    manifest, logs, data = evidence
    if bad_order == "attempts":
        data["attempts"][0], data["attempts"][1] = data["attempts"][1], data["attempts"][0]
    elif bad_order == "plan":
        data["protocol"]["workflows"][0]["tasks"].reverse()
    else:
        retry = {**data["attempts"][0], "id": "late", "events": "late.events.jsonl"}
        rewrite_native(logs, retry, events())
        data["attempts"].append(retry)
    save(manifest, data)
    with pytest.raises(ValueError):
        value.scorecard(manifest, logs)


@pytest.mark.parametrize(
    "corruption",
    [
        "cache",
        "duplicate_turn",
        "missing",
        "duplicate",
        "extra_log",
        "hash",
        "answer",
        "question",
        "comparison",
        "label",
        "time",
    ],
)
def test_inventory_identity_and_review_controls(evidence, corruption):
    manifest, logs, data = evidence
    row = data["attempts"][0]
    if corruption == "cache":
        rewrite_native(logs, row, events(1, 2))
    elif corruption == "duplicate_turn":
        native = events()
        rewrite_native(logs, row, native + [native[-1]])
    elif corruption == "missing":
        data["attempts"].pop()
    elif corruption == "duplicate":
        data["attempts"].append(copy.deepcopy(row))
    elif corruption == "extra_log":
        (logs / "unlisted-failure.events.jsonl").write_text("")
    elif corruption in {"hash", "answer"}:
        row["events_sha256" if corruption == "hash" else "answer_sha256"] = "0" * 64
    elif corruption == "question":
        data["protocol"]["workflows"][0]["tasks"][0]["question"] = ""
    elif corruption == "comparison":
        data["protocol"]["comparison"] = "mvp_vs_feature"
    elif corruption == "label":
        row["correct"] = 1
    else:
        row["seconds"] = -1
    save(manifest, data)
    with pytest.raises(ValueError):
        value.scorecard(manifest, logs)


def test_cli_writes_new_report_only_and_guards_survive_optimized_python(evidence, tmp_path):
    manifest, logs, data = evidence
    output = tmp_path / "new-report.json"
    args = [
        sys.executable,
        "-B",
        "-O",
        str(SCRIPTS / "product_value.py"),
        "--manifest",
        str(manifest),
        "--logs",
        str(logs),
        "--output",
        str(output),
    ]
    first = subprocess.run(args, capture_output=True, timeout=20)
    assert first.returncode == 0, first.stderr
    original = sha(output)
    assert subprocess.run(args, capture_output=True, timeout=20).returncode != 0
    assert sha(output) == original
    data["attempts"][0]["correct"] = 1
    save(manifest, data)
    assert (
        subprocess.run(
            args[:-1] + [str(tmp_path / "rejected.json")], capture_output=True, timeout=20
        ).returncode
        != 0
    )


def test_multiple_turns_are_summed_and_AA_cannot_become_a_supported_win(evidence):
    manifest, logs, data = evidence
    for row in data["attempts"]:
        native = events() + events()
        rewrite_native(logs, row, native)
        row["answer_sha256"] = hashlib.sha256(b"FAKE answer\nFAKE answer").hexdigest()
    save(manifest, data)
    result = value.scorecard(manifest, logs)
    assert result["arms"]["colony"]["total_usage"]["gross_tokens"] == 440
    assert result["observed_token_reduction"]["gross_tokens"] == 0
    assert result["supported_product_win"] == "not_established"


def test_unknown_labels_are_not_successes_and_report_write_is_non_destructive(evidence, tmp_path):
    manifest, logs, data = evidence
    data["attempts"][-1]["correct"] = None
    save(manifest, data)
    result = value.scorecard(manifest, logs)
    assert result["arms"]["colony"]["grounded_successful_workflows"] == 0
    assert result["arms"]["colony"]["unknown_quality_attempts"] == 1
    put(tmp_path / "new.json", result)
    assert manifest.exists() and len(list(logs.iterdir())) == 4
