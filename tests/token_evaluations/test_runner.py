import json

import pytest

from scripts.token_evaluation.quality import review_policy
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
    from scripts.token_evaluation.output import OUTPUT_CONTRACT
    from scripts.token_evaluation.protocol import CONTRACT, collector_hashes

    protocol = {"contract": CONTRACT, "quality_review": review_policy(), "output": {"contract": OUTPUT_CONTRACT,
                "format": "free_text", "answer_policy": "last_agent_message"},
                "collector": {"files": collector_hashes()},
                "workloads": [], "target": {"python": "target", "runtime_hashes": {"colony/__init__.py": "a" * 64}}, "client": {}}
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
    from scripts.token_evaluation.output import OUTPUT_CONTRACT
    from scripts.token_evaluation.protocol import CONTRACT
    from scripts.token_evaluation.report import digest
    from scripts.token_evaluation.runner import _verify
    source = tmp_path / "collector.py"
    source.write_text("changed")
    protocol = {"contract": CONTRACT, "quality_review": review_policy(), "output": {"contract": OUTPUT_CONTRACT,
                "format": "free_text", "answer_policy": "last_agent_message"},
                "collector": {"files": {str(source): "0" * 64}}}
    with pytest.raises(ValueError, match="complete active collector"):
        _verify({"protocol": protocol, "protocol_sha256": digest(protocol)})


def test_inventory_malformed_failed_turn_retains_cost(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    failed = {"type": "turn.failed", "usage": {"input_tokens": 7, "cached_input_tokens": 0, "output_tokens": 3}}
    (logs / "failed.events.jsonl").write_text(json.dumps(failed) + '\n{"partial')
    result = inventory(tmp_path)
    assert result["known_gross_tokens"] == 10
    assert not result["usage_complete"]


def native_inventory_fixture(root, references, paths):
    """Synthetic accounting controls only; no measured model usage."""
    (root / 'control').mkdir()
    raw = '\n'.join(map(json.dumps, [{'type': 'turn.started'}, {'type': 'turn.completed',
        'usage': {'input_tokens': 10, 'cached_input_tokens': 2, 'output_tokens': 4}}])).encode()
    for relative in paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    rows = []
    for identity, reference in references:
        (root / 'control' / (identity + '-invocation.json')).write_text(json.dumps({'id': identity}))
        rows.append({'id': identity, 'events': reference})
    (root / 'control/attempts.jsonl').write_text('\n'.join(map(json.dumps, rows)))


@pytest.mark.parametrize('root_present', [True, False])
def test_nested_same_basename_cannot_hide_orphan_or_missing_log(tmp_path, root_present):
    paths = ['logs/unlisted/trial.events.jsonl']
    if root_present:
        paths.append('logs/trial.events.jsonl')
    native_inventory_fixture(tmp_path, [('trial', 'trial.events.jsonl')], paths)
    value = inventory(tmp_path)
    assert value['orphan_logs'] == ['logs/unlisted/trial.events.jsonl']
    assert value['missing_logs'] == ([] if root_present else ['trial'])
    assert value['known_gross_tokens'] == 14 * len(paths)
    assert not value['usage_complete']


@pytest.mark.parametrize('reference,path', [
    ('trial.events.jsonl', 'logs/trial.events.jsonl'),
    ('logs/trial.events.jsonl', 'logs/trial.events.jsonl'),
    ('logs/nested/trial.events.jsonl', 'logs/nested/trial.events.jsonl'),
])
def test_explicit_log_paths_reconcile_only_the_named_file(tmp_path, reference, path):
    native_inventory_fixture(tmp_path, [('trial', reference)], [path])
    value = inventory(tmp_path)
    assert value['usage_complete'] and value['known_gross_tokens'] == 14
    assert value['orphan_logs'] == value['missing_logs'] == []


@pytest.mark.parametrize('reference', [None, '', [], {}, '../trial.events.jsonl',
    'logs/../trial.events.jsonl', 'nested/trial.events.jsonl', 'logs\\trial.events.jsonl',
    'D:/trial.events.jsonl'])
def test_invalid_references_retain_known_cost_without_claiming_reconciliation(tmp_path, reference):
    native_inventory_fixture(tmp_path, [('trial', reference)], ['logs/trial.events.jsonl'])
    value = inventory(tmp_path)
    assert value['known_gross_tokens'] == 14
    assert value['invalid_log_references'] == value['missing_logs'] == ['trial']
    assert value['orphan_logs'] == ['logs/trial.events.jsonl']
    assert not value['usage_complete']


def test_aliases_cannot_reference_the_same_log_twice(tmp_path):
    native_inventory_fixture(tmp_path, [('a', 'trial.events.jsonl'), ('b', 'logs/trial.events.jsonl')],
                             ['logs/trial.events.jsonl'])
    value = inventory(tmp_path)
    assert value['duplicate_log_references'] == ['logs/trial.events.jsonl']
    assert value['known_gross_tokens'] == 14
    assert not value['usage_complete']
