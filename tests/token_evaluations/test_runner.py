import json

import pytest

from scripts.token_evaluation.runner import execute, inventory


def fixture_run(tmp_path, **limits):
    run = {"arms": {"baseline": {}, "colony": {}}, "protocol": {
        "limits": {"max_attempts": 4, "wall_seconds": 60, "gross_token_stop": 100, **limits},
        "workloads": [{"id": "w", "tasks": [{"id": "t"}]}]}}
    (tmp_path / "run.json").write_text(json.dumps(run))
    return tmp_path


def test_native_refused_before_launch(tmp_path):
    root = fixture_run(tmp_path)
    called = []
    with pytest.raises(ValueError, match="requires authorization_path"):
        execute(root, execute_native=True, launch_fixture=lambda c: called.append(c))
    assert called == []
    assert not (root / "journal.jsonl").exists()


def test_attempt_cap_and_noncertifying(tmp_path):
    result = execute(fixture_run(tmp_path, max_attempts=1), launch_fixture=lambda c: {"gross_tokens": 10})
    assert result["status"] == "stopped"
    assert result["attempts"] == 1
    assert result["certifying"] is False
    assert inventory(tmp_path)["orphan_attempts"] == []


def test_unknown_usage_retained_and_no_retry(tmp_path):
    result = execute(fixture_run(tmp_path), launch_fixture=lambda c: {"gross_tokens": None})
    assert result["status"] == "partial"
    assert result["attempts"] == 1
    assert result["billing"] is None
    assert inventory(tmp_path)["records"][-1]["result"]["gross_tokens"] is None


def test_failure_records_invocation_and_error(tmp_path):
    def failure(context):
        raise TimeoutError("private diagnostic")
    result = execute(fixture_run(tmp_path), launch_fixture=failure)
    assert result["status"] == "partial"
    assert [r["event"] for r in inventory(tmp_path)["records"]] == ["invocation", "error"]


def test_orphan_is_not_discarded(tmp_path):
    fixture_run(tmp_path)
    (tmp_path / "journal.jsonl").write_text('{"attempt": 1, "event": "invocation", "usage": null}\n')
    assert inventory(tmp_path)["orphan_attempts"] == [1]


def test_inventory_includes_orphan_and_partial_log_tokens(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    completed = '\n'.join([json.dumps({"type": "turn.started"}), json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 4}})])
    (logs / "orphan.events.jsonl").write_text(completed)
    (logs / "broken.events.jsonl").write_text(completed + '\n{"broken')
    result = inventory(tmp_path)
    assert result["known_gross_tokens"] == 28
    assert len(result["orphan_logs"]) == 2
    assert result["usage_complete"] is False
    assert result["billing"] is None


def test_runtime_inventory_rejects_partial_module_manifest(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    from scripts.token_evaluation import runner

    protocol = {"workloads": [], "target": {"python": "target", "runtime_hashes": {"colony/__init__.py": "a" * 64}}, "client": {}}
    run = {"protocol": protocol, "arms": {}, "protocol_sha256": hashlib.sha256(json.dumps(protocol, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()}
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=json.dumps({"__init__.py": "a" * 64, "omitted.py": "b" * 64})))
    with pytest.raises(ValueError, match="runtime drift"):
        runner._verify(run)


def test_inventory_malformed_journal_retains_native_cost(tmp_path):
    (tmp_path / "control").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "journal.jsonl").write_text('{"attempt": 1, "event": "invocation"}\n{"partial')
    (tmp_path / "control" / "attempts.jsonl").write_text('null\n{"partial')
    (tmp_path / "logs" / "orphan.events.jsonl").write_text('\n'.join([json.dumps({"type": "turn.started"}), json.dumps({"type": "turn.completed", "usage": {"input_tokens": 7, "cached_input_tokens": 0, "output_tokens": 3}})]))
    result = inventory(tmp_path)
    assert result["known_gross_tokens"] == 10
    assert result["usage_complete"] is False
    assert len(result["parse_errors"]) == 3


def test_inventory_empty_or_simulated_is_not_complete(tmp_path):
    assert inventory(tmp_path)["usage_complete"] is False
    execute(fixture_run(tmp_path), launch_fixture=lambda c: {"gross_tokens": 1})
    assert inventory(tmp_path)["usage_complete"] is False


def test_collector_source_drift_blocks_verification(tmp_path):
    from scripts.token_evaluation.report import digest
    from scripts.token_evaluation.runner import _verify
    source = tmp_path / "collector.py"
    source.write_text("changed")
    protocol = {"collector": {"files": {str(source): "0" * 64}}}
    with pytest.raises(ValueError, match="collector source drift"):
        _verify({"protocol": protocol, "protocol_sha256": digest(protocol)})


def test_inventory_malformed_failed_turn_retains_cost(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    failed = {"type": "turn.failed", "usage": {"input_tokens": 7, "cached_input_tokens": 0, "output_tokens": 3}}
    (logs / "failed.events.jsonl").write_text(json.dumps(failed) + '\n{"partial')
    result = inventory(tmp_path)
    assert result["known_gross_tokens"] == 10
    assert not result["usage_complete"]
