"""Complete synthetic native sessions exercise audit failures, not model scores."""

import hashlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path, values):
    path.write_text("\n".join(json.dumps(v) for v in values), encoding="utf-8")


@pytest.fixture
def trial(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tasks = [f"independent-{i}" for i in range(24)]
    write_json(
        tmp_path / "protocol.json",
        {
            "schema_version": 2,
            "tasks": tasks,
            "baseline_commit": revision,
            "feature_commit": revision,
            "task_spec": [
                {
                    "task_id": task,
                    "family": f"topic-{i // 3}",
                    "question": f"What is independent fact {i}?",
                }
                for i, task in enumerate(tasks)
            ],
        },
    )
    write_json(tmp_path / "completed.json", {"sessions": 48})
    write_json(
        tmp_path / "verification.json",
        {"commit": revision, "tests_passed": 1, "tests_failed": 0, "ruff_passed": True},
    )
    write_json(tmp_path / "local.json", {"baseline_commit": revision, "feature_commit": revision})
    source = {
        "ref": "README.md",
        "content_hash": hashlib.sha256(b"Independent fact.\n").hexdigest(),
    }
    for arm in ["main", "feature"]:
        directory = tmp_path / arm
        directory.mkdir()
        write_json(
            directory / "metadata.json",
            {
                "implementation_commit": revision,
                **{
                    key: "same"
                    for key in [
                        "commit",
                        "model",
                        "reasoning_effort",
                        "cli",
                        "task_order",
                        "prompt_sha256",
                    ]
                },
            },
        )
        write_json(directory / "corpus-sha256.json", {source["ref"]: source["content_hash"]})
        rows, reviews = [], {}
        for index, task in enumerate(tasks):
            question = f"What is independent fact {index}?"

            def tool(server, name, arguments, result):
                return {
                    "type": "mcp_tool_call",
                    "server": server,
                    "tool": name,
                    "arguments": arguments,
                    "status": "completed",
                    "result": {"content": [{"text": json.dumps(result)}]},
                }

            ask = tool(
                "colony", "ask", {"question": question, "namespace": "bench"}, {"hit": False}
            )
            calls = [
                ask,
                tool(
                    "repository",
                    "read_file",
                    {"path": "README.md"},
                    {"content": "Independent fact.\n", "source": source},
                ),
                tool(
                    "colony",
                    "push",
                    {"question": question, "namespace": "bench", "sources": [source]},
                    {"stored": True},
                ),
            ]
            usage = {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10}
            row = {
                "task_id": task,
                "family": f"topic-{index // 3}",
                "usage": usage,
                "tokens": 110,
                "seconds": 1,
                "tool_calls": 3,
                "ask_calls": 1,
                "asks": [ask],
                "answer": "Independent fact. [README](README.md)",
            }
            rows.append(row)
            items = [*calls, {"type": "agent_message", "text": row["answer"]}]
            events = [{"type": "item.completed", "item": item} for item in items]
            events.append({"type": "turn.completed", "usage": usage})
            write_jsonl(directory / f"{task}.colony.events.jsonl", events)
            (directory / f"{task}.colony.prompt.txt").write_text("Fixture\nQuestion: " + question)
            reviews[task + ".colony"] = {
                "correct": True,
                "citation_valid": True,
                "relevant_asks": [False],
            }
        write_jsonl(directory / "raw-measurements.jsonl", rows)
        write_json(directory / "reviews.json", reviews)
    return tmp_path


def test_complete_native_fixture_audits_but_equal_costs_and_missing_proofs_block(trial):
    from analyze_lookup_live import compare, read_json

    report = compare(trial, read_json(trial / "local.json"))
    assert report["arms"]["main"]["tasks"] == report["arms"]["feature"]["tasks"] == 24
    assert report["gate"]["correctness_not_reduced"]
    assert report["gate"]["feature_follows_tool_protocol"]
    assert not report["all_gates_passed"]
    assert not report["gate"]["loaded_implementations_verified"]
    assert "merge_review_ready" not in report


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("source-bytes", "returned source bytes"),
        ("push-question", "push changed"),
        ("push-namespace", "push changed"),
        ("cached-tokens", "Cached input exceeds"),
        ("duplicate-task", "distinct task identities"),
        ("pair-family", "frozen task specification"),
    ],
)
def test_corrupt_native_evidence_is_rejected(trial, mutation, message):
    from analyze_lookup_live import compare, read_json

    directory = trial / "feature"
    events_path = directory / "independent-0.colony.events.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    rows_path = directory / "raw-measurements.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
    if mutation == "source-bytes":
        result = events[1]["item"]["result"]["content"][0]
        data = json.loads(result["text"])
        data["content"] = "Forged fact.\n"
        result["text"] = json.dumps(data)
    elif mutation.startswith("push-"):
        events[2]["item"]["arguments"][mutation.removeprefix("push-")] = "changed"
    elif mutation == "cached-tokens":
        rows[0]["usage"]["cached_input_tokens"] = 101
        events[-1]["usage"]["cached_input_tokens"] = 101
    elif mutation == "duplicate-task":
        protocol = read_json(trial / "protocol.json")
        protocol["tasks"][1] = protocol["tasks"][0]
        write_json(trial / "protocol.json", protocol)
    else:
        rows[0]["family"] = "changed"
    write_jsonl(events_path, events)
    write_jsonl(rows_path, rows)
    with pytest.raises(ValueError, match=message):
        compare(trial, read_json(trial / "local.json"))


def test_optimized_python_cannot_disable_integrity_checks_and_failure_is_retained(trial):
    write_json(trial / "completed.json", {"sessions": 47})
    completed = subprocess.run(
        [
            sys.executable,
            "-O",
            str(ROOT / "scripts/analyze_lookup_live.py"),
            "--results",
            str(trial),
            "--local",
            str(trial / "local.json"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 1
    report = json.loads((trial / "comparison.json").read_text())
    assert report["blocking_gates"] == ["evidence_integrity"]
    assert report["integrity_error"] == "Expected 48 completed paired sessions"


def test_overlong_answer_blocks_protocol_gate(trial):
    from analyze_lookup_live import compare, read_json

    directory = trial / "feature"
    rows_path = directory / "raw-measurements.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
    rows[0]["answer"] = "word " * 131
    write_jsonl(rows_path, rows)
    path = directory / "independent-0.colony.events.jsonl"
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events[-2]["item"]["text"] = rows[0]["answer"]
    write_jsonl(path, events)
    report = compare(trial, read_json(trial / "local.json"))
    assert not report["gate"]["feature_follows_tool_protocol"]


def test_identical_source_cannot_be_relabeled_as_optimization(trial):
    from analyze_lookup_live import compare, read_json

    local = read_json(trial / "local.json")
    archive = subprocess.check_output(
        ["git", "archive", "--format=zip", local["baseline_commit"], "src"], cwd=ROOT
    )
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        manifest = {
            name.removeprefix("src/"): hashlib.sha256(z.read(name)).hexdigest()
            for name in z.namelist()
            if name.endswith(".py")
        }
    local.update(
        schema_version=2,
        same_implementation=False,
        quality_passed=True,
        implementation_isolated=True,
        lookup_improves=True,
        latency_reduction_95pct=[0.8, 0.95],
        modes={
            arm: {"lookups": 1, "oracle_passes": 1, "seconds": seconds}
            for arm, seconds in [("main", 100), ("feature", 10)]
        },
        loaded_implementations={
            arm: {
                "source": f"{arm}/src",
                "source_sha256": manifest,
                "module_paths": {
                    "colony." + name: f"{arm}/src/colony/{name}.py"
                    for name in [
                        "store",
                        "context",
                        "retrieval",
                        "models",
                        "security",
                        "validation",
                    ]
                },
            }
            for arm in ["main", "feature"]
        },
    )
    with pytest.raises(ValueError, match="Local A/A identity flag inconsistent"):
        compare(trial, local)


@pytest.mark.parametrize("arm", ["main", "feature"])
@pytest.mark.parametrize("status,relevant", [(None, False), ("stale", False), ("validated", False)])
def test_researched_miss_or_unusable_hit_requires_push(trial, arm, status, relevant):
    from analyze_lookup_live import compare, read_json

    directory = trial / arm
    path = directory / "independent-0.colony.events.jsonl"
    events = [json.loads(s) for s in path.read_text().splitlines()]
    if status:
        events[0]["item"]["result"]["content"][0]["text"] = json.dumps(
            {"hit": True, "status": status}
        )
    events = [e for e in events if e.get("item", {}).get("tool") != "push"]
    write_jsonl(path, events)
    rows_path = directory / "raw-measurements.jsonl"
    rows = [json.loads(s) for s in rows_path.read_text().splitlines()]
    rows[0]["tool_calls"] = 2
    rows[0]["asks"] = [events[0]["item"]]
    write_jsonl(rows_path, rows)
    report = compare(trial, read_json(trial / "local.json"))
    assert report["arms"][arm]["protocol_violations"] == 1
    assert not report["gate"]["feature_follows_tool_protocol"]


def test_relevant_validated_reuse_needs_no_push_or_research(trial):
    from analyze_lookup_live import compare, read_json

    directory = trial / "feature"
    path = directory / "independent-0.colony.events.jsonl"
    events = [json.loads(s) for s in path.read_text().splitlines()]
    events[0]["item"]["result"]["content"][0]["text"] = json.dumps(
        {"hit": True, "status": "validated"}
    )
    events = [events[0], *events[-2:]]
    write_jsonl(path, events)
    rows_path = directory / "raw-measurements.jsonl"
    rows = [json.loads(s) for s in rows_path.read_text().splitlines()]
    rows[0]["tool_calls"] = 1
    rows[0]["asks"] = [events[0]["item"]]
    write_jsonl(rows_path, rows)
    reviews = read_json(directory / "reviews.json")
    reviews["independent-0.colony"]["relevant_asks"] = [True]
    write_json(directory / "reviews.json", reviews)
    assert compare(trial, read_json(trial / "local.json"))["gate"]["feature_follows_tool_protocol"]


@pytest.mark.parametrize("arm", ["main", "feature"])
@pytest.mark.parametrize(
    "receipt,expected",
    [
        ({"stored": True}, True),
        ({"stored": False, "queued": True}, True),
        ({"stored": False}, False),
        ({"stored": False, "queued": False}, False),
        ({"stored": True, "queued": True}, False),
        ({"stored": 1}, False),
        ({"stored": "true"}, False),
        ({"error": "write rejected"}, False),
        ([], False),
        ("Error executing tool push: write failed", False),
    ],
)
def test_researched_push_requires_success_receipt_in_both_arms(trial, arm, receipt, expected):
    from analyze_lookup_live import compare, read_json

    directory = trial / arm
    path = directory / "independent-0.colony.events.jsonl"
    events = [json.loads(s) for s in path.read_text().splitlines()]
    result = events[2]["item"]["result"]
    result["content"][0]["text"] = receipt if isinstance(receipt, str) else json.dumps(receipt)
    write_jsonl(path, events)
    report = compare(trial, read_json(trial / "local.json"))
    assert report["arms"][arm]["protocol_violations"] == int(not expected)
    assert report["gate"]["feature_follows_tool_protocol"] == expected


@pytest.mark.parametrize("arm", ["main", "feature"])
def test_error_envelope_cannot_turn_a_save_receipt_into_success(trial, arm):
    from analyze_lookup_live import compare, read_json

    path = trial / arm / "independent-0.colony.events.jsonl"
    events = [json.loads(s) for s in path.read_text().splitlines()]
    events[2]["item"]["result"]["isError"] = True
    write_jsonl(path, events)
    assert not compare(trial, read_json(trial / "local.json"))["gate"][
        "feature_follows_tool_protocol"
    ]
