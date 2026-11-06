"""Known native components survive incomplete receipts; totals remain unknown."""

import json

import pytest
from test_report import rewrite_events
from test_report import run as run

from scripts.token_evaluation import native, report, runner


@pytest.mark.parametrize('reader', [native.native_usage, report.native_usage])
@pytest.mark.parametrize('kind', ['turn.completed', 'turn.failed'])
@pytest.mark.parametrize('usage,gross,complete', [
    ({'input_tokens': 100, 'cached_input_tokens': 20}, 100, False),
    ({'output_tokens': 7}, 7, False),
    ({'input_tokens': 100, 'output_tokens': 7}, 107, False),
    ({'cached_input_tokens': 20}, 0, False),
    ({}, 0, False),
    ({'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 7,
      'reasoning_output_tokens': 3}, 107, True),
])
def test_known_gross_components_survive_missing_fields(tmp_path, reader, kind, usage, gross, complete):
    path = tmp_path / 'partial.events.jsonl'
    path.write_text('\n'.join(map(json.dumps, [
        {'type': 'turn.started'}, {'type': kind, 'usage': usage},
    ])), encoding='utf-8')
    result = reader(path)
    assert result['known_usage']['gross_tokens'] == gross
    assert result['usage_complete'] is (complete and kind == 'turn.completed')
    assert result['known_usage']['cached_input_tokens'] == usage.get('cached_input_tokens', 0)
    if not complete:
        assert result['known_usage']['uncached_input_plus_output'] == 0


@pytest.mark.parametrize('reader', [native.native_usage, report.native_usage])
def test_valid_multiple_turns_and_auxiliary_usage_are_not_double_counted(tmp_path, reader):
    path = tmp_path / 'turns.events.jsonl'
    path.write_text('\n'.join(map(json.dumps, [
        {'type': 'turn.started'},
        {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'cached_input_tokens': 2,
                                           'output_tokens': 4, 'reasoning_output_tokens': 3}},
        {'type': 'turn.started'},
        {'type': 'turn.completed', 'usage': {'input_tokens': 3, 'cached_input_tokens': 1,
                                           'output_tokens': 1, 'cache_write_tokens': 2}},
    ])), encoding='utf-8')
    result = reader(path)
    assert result['known_usage'] == {'input_tokens': 13, 'cached_input_tokens': 3,
                                    'output_tokens': 5, 'gross_tokens': 18,
                                    'uncached_input_plus_output': 15}
    assert result['usage_complete'] and result['native_finished']


@pytest.mark.parametrize('usage,gross', [
    ({'input_tokens': 100, 'cached_input_tokens': 20}, 100),
    ({'output_tokens': 7}, 7),
])
def test_inventory_retains_partial_failed_cost_without_certifying_it(tmp_path, usage, gross):
    logs = tmp_path / 'logs'
    logs.mkdir()
    (logs / 'partial.events.jsonl').write_text('\n'.join(map(json.dumps, [
        {'type': 'turn.started'}, {'type': 'turn.failed', 'usage': usage},
    ])), encoding='utf-8')
    result = runner.inventory(tmp_path)
    assert result['known_gross_tokens'] == gross
    assert not result['usage_complete']
    assert result['orphan_logs'] == ['logs/partial.events.jsonl']


def test_inventory_keeps_malformed_stream_components_unverified(tmp_path):
    logs = tmp_path / 'logs'
    logs.mkdir()
    (logs / 'malformed.events.jsonl').write_text(
        json.dumps({'type': 'turn.failed', 'usage': {'input_tokens': 11, 'output_tokens': 3}})
        + '\n{"partial', encoding='utf-8')
    result = runner.inventory(tmp_path)
    assert result['known_gross_tokens'] == 14 and not result['usage_complete']
    assert result['native_logs'][0]['usage'] is None
    assert result['native_logs'][0]['unverified_observed_components']['gross_tokens'] == 14


@pytest.mark.parametrize('missing,gross', [('input_tokens', 20), ('output_tokens', 100),
                                         ('cached_input_tokens', 120)])
def test_report_keeps_failed_partial_subtotal_without_total_or_reduction(run, missing, gross):
    root, _, _ = run

    def partial(events):
        events[-1]['type'] = 'turn.failed'
        events[-1]['usage'].pop(missing)

    rewrite_events(root, 'b', partial)
    result = report.report(root, root / 'quality.json', root / 'partial-report.json')
    assert result['arms']['colony']['known_usage']['gross_tokens'] == gross
    assert result['arms']['colony']['total_usage'] is None
    assert result['arms']['colony']['gross_tokens_per_successful_workflow'] is None
    assert result['observed_token_reduction'] is None
    assert not result['quality_complete']
