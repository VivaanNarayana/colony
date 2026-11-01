"""Independent metadata/accounting examples; no provider or model execution."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from colony import product_value, report_io, usage_report


def event(n, outcome="answer", operation="lookup", assessment=None, logical_id=None):
    return {
        "event_id": f"{n:032x}",
        "timestamp": float(n),
        "operation": operation,
        "outcome": outcome,
        "duration_ms": float(n),
        "assessment": assessment,
        "logical_id": logical_id,
    }


def journal(monkeypatch, events, **overrides):
    class Journal:
        def __init__(self, home):
            pass

        def events(self, *, since=None, until=None):
            return [
                e
                for e in events
                if (since is None or e["timestamp"] >= since)
                and (until is None or e["timestamp"] < until)
            ]

        def status(self):
            return {
                "enabled": True,
                "retention_days": 30,
                "max_events": 10000,
                "event_count": len(events),
                "health": "healthy",
                "gap": False,
                "warning": None,
                **overrides,
            }

    monkeypatch.setattr(usage_report, "UsageJournal", Journal)


def test_report_denominators_do_not_turn_hits_or_feedback_into_correctness(monkeypatch):
    events = [
        event(1, assessment="useful", logical_id="opaque"),
        event(2, "evidence", assessment="incorrect", logical_id="opaque"),
        event(3),
        event(4, "miss"),
        event(5, "stale"),
        event(6, "unverified"),
        event(7, "unavailable"),
        event(8, "denied"),
        event(9, "error"),
        event(10, "accepted", "feedback"),
    ]
    journal(monkeypatch, events)
    report = usage_report.make_report("unused")
    assert report["activity"]["observed_request_attempts"] == 10
    assert report["activity"]["identified_logical_operations"] == 1
    assert report["activity"]["attempts_without_logical_id"] == 8
    assert report["lookups"]["hit_fraction_of_responses"] == {
        "numerator": 3,
        "denominator": 6,
        "fraction": 0.5,
    }
    assert report["lookups"]["hit_fraction_of_attempts"]["fraction"] == 1 / 3
    quality = report["quality"]
    assert quality["assessed_hits"] == 2 and quality["unassessed_hits"] == 1
    assert quality["self_reported_usefulness"]["fraction"] == 0.5
    assert quality["self_reported_incorrect"]["denominator"] == 2
    assert quality["correctness"] == "not_validated"
    assert report["model_usage"]["token_savings"] is None
    assert report["latency"]["lookup_attempts"]["p95_ms"] is None
    assert usage_report.make_report("unused") == report
    text = usage_report.render_text(report)
    assert "unassessed" in text and "tokens" in text and "unknown" in text
    assert "unused" not in text and "opaque" not in text


def test_empty_report_unknown_quality_and_usage_never_zero(monkeypatch):
    journal(monkeypatch, [], enabled=False)
    report = usage_report.make_report("unused")
    assert report["quality"]["assessment_coverage"]["fraction"] is None
    assert report["quality"]["self_reported_incorrect"]["fraction"] is None
    assert report["lookups"]["hit_fraction_of_attempts"]["fraction"] is None
    assert report["latency"]["all_attempts"]["median_ms"] is None
    assert report["model_usage"]["input_tokens"] is None


def test_dates_are_utc_or_explicit_offsets_and_windows_are_half_open(monkeypatch):
    assert usage_report.parse_time("1970-01-01") == 0
    assert usage_report.parse_time("1970-01-01T01:00:00+01:00") == 0
    assert usage_report.parse_time("1970-01-01T00:00:00.500000Z") == 0.5
    journal(monkeypatch, [event(1), event(2), event(3)])
    report = usage_report.make_report("unused", since=2, until=3)
    assert report["activity"]["observed_request_attempts"] == 1
    assert report["recent_lookup_metadata"][0]["event_id"] == f"{2:032x}"
    with pytest.raises(ValueError):
        usage_report.make_report("unused", since=3, until=2)


@pytest.mark.parametrize(
    "value",
    [
        "2026-02-30",
        "2026-01-01T00:00:00",
        "now",
        "2026-01-01 00:00:00Z",
        "2026-01-01T00:00:00+01:99",
        "2026-01-01T00:00Z",
        True,
        "",
    ],
)
def test_invalid_dates_rejected(value):
    with pytest.raises(ValueError):
        usage_report.parse_time(value)


def test_recent_metadata_latency_and_gap_reporting_are_bounded(monkeypatch):
    journal(
        monkeypatch,
        [event(n) for n in range(1, 102)],
        gap=True,
        health="degraded",
        warning="PRIVATE PATH AND SECRET",
    )
    report = usage_report.make_report("unused")
    assert len(report["recent_lookup_metadata"]) == 50
    assert report["recent_lookup_metadata"][0]["timestamp"] == 101
    assert report["latency"]["lookup_attempts"]["p95_ms"] == 96
    assert report["latency"]["lookup_attempts"]["samples"] == 101
    assert report["coverage"]["gap"] is True
    assert "PRIVATE" not in json.dumps(report)


def test_finite_extreme_latencies_remain_valid_json(monkeypatch):
    events = [event(1), event(2)]
    for row in events:
        row["duration_ms"] = 1e308
    journal(monkeypatch, events)
    report = usage_report.make_report("unused")
    assert report["latency"]["lookup_attempts"]["median_ms"] == 1e308
    json.dumps(report, allow_nan=False)


def save(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def native(path, input_tokens, cached, output):
    events = [
        {"type": "turn.started"},
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": cached,
                "output_tokens": output,
            },
        },
    ]
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def comparison(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    data = {
        "contract": product_value.CONTRACT,
        "protocol": {
            "comparison": "colony_vs_no_colony",
            **{
                k: "externally supplied"
                for k in (
                    "implementation",
                    "corpus",
                    "model",
                    "client",
                    "effort",
                    "instructions_sha256",
                    "budget",
                    "memory_difference",
                    "rubric_sha256",
                    "reviewer",
                    "review_method",
                )
            },
            "workflows": [
                {
                    "id": "one",
                    "tasks": [
                        {"id": "learn", "question": "private manifest question", "phase": "learn"},
                        {"id": "reuse", "question": "private manifest question", "phase": "reuse"},
                    ],
                }
            ],
        },
        "attempts": [],
    }
    for mode, task, counts in [
        ("no_colony", "learn", (100, 25, 10)),
        ("no_colony", "reuse", (100, 25, 10)),
        ("colony", "learn", (200, 20, 15)),
        ("colony", "reuse", (10, 0, 5)),
    ]:
        name = mode + task
        path = logs / (name + ".events.jsonl")
        digest = native(path, *counts)
        data["attempts"].append(
            {
                "id": name,
                "mode": mode,
                "workflow": "one",
                "task": task,
                "events": path.name,
                "events_sha256": digest,
                "state": "completed",
                "seconds": 1,
                "correct": True,
                "grounded": True,
                "answer_sha256": hashlib.sha256(b"").hexdigest(),
            }
        )
    manifest = tmp_path / "manifest.json"
    save(manifest, data)
    return manifest, logs, data


def test_packaged_independent_accounting_negative_reduction_and_quality_coverage(comparison):
    manifest, logs, data = comparison
    data["attempts"][-1]["grounded"] = None
    save(manifest, data)
    report = product_value.scorecard(manifest, logs)
    assert report["arms"]["no_colony"]["total_usage"]["gross_tokens"] == 220
    assert report["arms"]["no_colony"]["total_usage"]["uncached_input_plus_output"] == 170
    assert report["arms"]["colony"]["total_usage"]["gross_tokens"] == 230
    assert report["observed_token_reduction"]["gross_tokens"] == 1 - 230 / 220
    assert report["arms"]["colony"]["grounded_successful_workflows"] == 0
    assert report["arms"]["colony"]["unknown_quality_attempts"] == 1
    text = product_value.render_comparison(report)
    assert "private manifest" not in text
    assert "not a persistent token import" in text
    assert product_value.scorecard(manifest, logs) == report


def test_new_comparison_summary_omits_manifest_and_native_freeform_text(comparison):
    manifest, logs, data = comparison
    secret = "PRIVATE_SENTINEL_DO_NOT_RETAIN"
    data["protocol"]["workflows"][0]["id"] = secret
    data["protocol"]["workflows"][0]["tasks"][0]["question"] = secret
    for row in data["attempts"]:
        row["workflow"] = secret
        row["id"] += secret
        path = logs / row["events"]
        # Native command/body text is ignored while accounting and answer hashes remain bound.
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                "\n"
                + json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "command": secret,
                            "aggregated_output": secret,
                        },
                    }
                )
            )
        row["events_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    save(manifest, data)
    legacy = product_value.scorecard(manifest, logs)
    assert secret in json.dumps(
        legacy
    )  # Frozen historical interface retains explicit manifest metadata.
    report = product_value.comparison_report(manifest, logs)
    assert report["contract"] == "colony.usage-comparison/1"
    assert report["scorecard_contract"] == "colony.product-value/1"
    assert secret not in json.dumps(report)
    assert secret not in product_value.render_comparison(report)
    assert "events.jsonl" not in json.dumps(report)
    assert product_value.comparison_report(manifest, logs) == report


def test_log_tree_inventory_count_is_bounded(tmp_path, monkeypatch):
    class Entries:
        def __enter__(self):
            return iter([type("Entry", (), {"path": str(tmp_path / "ignored")})()] * 4097)

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(report_io.os, "scandir", lambda _: Entries())
    with pytest.raises(ValueError, match="inventory entry limit"):
        list(report_io.files_under(tmp_path))


def test_zero_baseline_is_not_a_savings_claim(comparison):
    manifest, logs, data = comparison
    for row in data["attempts"][:2]:
        row["events_sha256"] = native(logs / row["events"], 0, 0, 0)
    save(manifest, data)
    report = product_value.scorecard(manifest, logs)
    assert report["observed_token_reduction"]["gross_tokens"] is None
    assert report["supported_product_win"] == "not_established"


@pytest.mark.parametrize(
    "corruption", ["duplicate", "missing_baseline", "unsafe_ref", "array", "null"]
)
def test_inventory_or_malformed_manifest_rejected(comparison, corruption):
    manifest, logs, data = comparison
    if corruption == "duplicate":
        data["attempts"].append(data["attempts"][0])
    elif corruption == "missing_baseline":
        row = data["attempts"].pop(0)
        (logs / row["events"]).unlink()
    elif corruption == "unsafe_ref":
        data["attempts"][0]["events"] = "../secret.events.jsonl"
    else:
        data = [] if corruption == "array" else None
    save(manifest, data)
    with pytest.raises(ValueError):
        product_value.scorecard(manifest, logs)


@pytest.mark.parametrize(
    "event",
    [
        [],
        None,
        {"type": []},
        {"type": "provider.response"},
        {"type": "item.completed", "item": "private text"},
    ],
)
def test_public_native_parser_rejects_unsupported_or_malformed_logs(tmp_path, event):
    path = tmp_path / "selected.jsonl"
    save(path, event)
    with pytest.raises(ValueError):
        product_value.native_usage(path)


def test_report_io_bounds_links_paths_and_new_output(tmp_path):
    path = tmp_path / "selected.json"
    save(path, {"safe": 1})
    with pytest.raises(ValueError):
        report_io.bounded_bytes(path, 1)
    with pytest.raises(ValueError):
        report_io.relative_file(tmp_path, "../selected.json")
    with pytest.raises(FileExistsError):
        report_io.put(path, {"replace": True})
    assert json.loads(path.read_text()) == {"safe": 1}
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    with pytest.raises(ValueError):
        report_io.read(link)


def test_manifest_and_aggregate_native_bounds(comparison, monkeypatch):
    manifest, logs, _ = comparison
    monkeypatch.setattr(product_value, "MAX_BYTES", 1)
    with pytest.raises(ValueError, match="Log size limit"):
        product_value.scorecard(manifest, logs)
    manifest.write_bytes(b" " * ((1 << 20) + 1))
    with pytest.raises(ValueError, match="Manifest size limit"):
        product_value.scorecard(manifest, logs)


def test_attempt_inventory_limit_is_checked_before_import(comparison):
    manifest, logs, data = comparison
    data["attempts"] = [data["attempts"][0]] * 129
    save(manifest, data)
    with pytest.raises(ValueError, match="1..128"):
        product_value.scorecard(manifest, logs)


def test_actual_native_byte_budget_survives_growth_after_inventory_stat(comparison, monkeypatch):
    manifest, logs, data = comparison
    path = logs / data["attempts"][0]["events"]
    grown = path.read_bytes() + b" " * 10
    data["attempts"][0]["events_sha256"] = hashlib.sha256(grown).hexdigest()
    save(manifest, data)
    limit = sum(p.stat().st_size for p in logs.iterdir()) + 5
    monkeypatch.setattr(product_value, "MAX_BYTES", limit)
    original = product_value.bounded_bytes

    def grow_after_preflight(source, bound):
        if Path(source) == path:
            path.write_bytes(grown)
        return original(source, bound)

    monkeypatch.setattr(product_value, "bounded_bytes", grow_after_preflight)
    with pytest.raises(ValueError, match="size"):
        product_value.scorecard(manifest, logs)


def test_native_stream_error_preserves_known_cost_but_never_certifies_complete_usage(tmp_path):
    path = tmp_path / "selected.jsonl"
    native(path, 10, 0, 5)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\n" + json.dumps({"type": "error", "message": "PRIVATE ERROR"}))
    parsed = product_value.native_usage(path)
    assert parsed["known_usage"]["gross_tokens"] == 15
    assert parsed["usage_complete"] is False and parsed["native_finished"] is False
    assert "PRIVATE ERROR" not in json.dumps(parsed)


def test_installed_package_import_does_not_depend_on_repository_scripts(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    code = (
        "import sys; from colony.product_value import native_usage,scorecard,main; "
        "assert 'production_delivery_core' not in sys.modules; "
        "assert callable(native_usage) and callable(scorecard) and callable(main)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=tmp_path, env=env, capture_output=True, timeout=20
    )
    assert result.returncode == 0, result.stderr


def test_historical_wrapper_reexports_packaged_functions():
    from importlib.util import module_from_spec, spec_from_file_location

    script = Path(__file__).resolve().parents[1] / "scripts" / "product_value.py"
    spec = spec_from_file_location("historical_product_value", script)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.scorecard is product_value.scorecard
    assert module.native_usage is product_value.native_usage


def test_historical_wrapper_selects_checkout_before_an_older_installed_package(
    comparison, tmp_path
):
    manifest, logs, _ = comparison
    old = tmp_path / "old-package"
    (old / "colony").mkdir(parents=True)
    (old / "colony" / "__init__.py").write_text(
        "raise RuntimeError('Older Colony must not be imported by the source wrapper')\n",
        encoding="utf-8",
    )
    cwd = tmp_path / "unrelated"
    cwd.mkdir()
    output = cwd / "new-report.json"
    script = Path(__file__).resolve().parents[1] / "scripts" / "product_value.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--manifest",
            str(manifest),
            "--logs",
            str(logs),
            "--output",
            str(output),
        ],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(old)},
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["contract"] == "colony.product-value/1"


@pytest.mark.parametrize("target", ["manifest", "native", "numeric", "auxiliary_overflow"])
def test_deep_or_overflowing_explicit_imports_have_safe_cli_errors(comparison, target):
    manifest, logs, data = comparison
    secret = "PRIVATE_NESTED_INPUT_SENTINEL"
    if target == "manifest":
        manifest.write_text(
            "{" + json.dumps(secret) + ":" + "[" * 2000 + "0" + "]" * 2000 + "}", encoding="utf-8"
        )
    else:
        row = data["attempts"][0]
        path = logs / row["events"]
        if target == "native":
            text = (
                '{"type":"turn.started",'
                + json.dumps(secret)
                + ":"
                + "[" * 2000
                + "0"
                + "]" * 2000
                + "}"
            )
        else:
            extra = '"unknown_count":' + ("1e9999" if target == "numeric" else "9" * 400)
            text = (
                '{"type":"turn.started"}\n{"type":"turn.completed","usage":'
                '{"input_tokens":1,"cached_input_tokens":0,"output_tokens":0,' + extra + "}}"
            )
        path.write_text(text, encoding="utf-8")
        row["events_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        save(manifest, data)
    with pytest.raises(ValueError) as exc:
        product_value.comparison_report(manifest, logs)
    assert secret not in str(exc.value)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "colony.cli",
            "report",
            "compare",
            "--manifest",
            str(manifest),
            "--logs",
            str(logs),
            "--format",
            "json",
        ],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 1
    assert b"ValueError" in result.stderr
    assert b"Traceback" not in result.stderr and secret.encode() not in result.stderr
    assert result.stdout == b""


def test_json_nesting_bound_ignores_brackets_and_escaped_quotes_inside_strings():
    original = {"text": '"quoted" \\ ' + "[" * 100 + "]" * 100}
    assert report_io.decode(json.dumps(original)) == original
    assert report_io.decode("[" * 64 + "0" + "]" * 64)
    with pytest.raises(ValueError, match="nesting limit"):
        report_io.decode("[" * 65 + "0" + "]" * 65)
