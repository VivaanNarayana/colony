"""Duplicate attempt identities cannot reconcile or permit another dispatch."""

import json

import pytest
from test_native import setup_run

from scripts.token_evaluation import native, runner


def raw_usage(inputs):
    return '\n'.join(map(json.dumps, [
        {'type': 'turn.started'},
        {'type': 'turn.completed', 'usage': {'input_tokens': inputs,
                                           'cached_input_tokens': 0, 'output_tokens': 4}},
    ])).encode()


def test_duplicate_ids_with_distinct_logs_keep_costs_but_fail_reconciliation(tmp_path):
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'control').mkdir()
    (tmp_path / 'logs/one.events.jsonl').write_bytes(raw_usage(10))
    (tmp_path / 'logs/two.events.jsonl').write_bytes(raw_usage(20))
    (tmp_path / 'control/a-invocation.json').write_text('{"id":"a"}')
    (tmp_path / 'control/attempts.jsonl').write_text('\n'.join(map(json.dumps, [
        {'id': 'a', 'events': 'one.events.jsonl'},
        {'id': 'a', 'events': 'two.events.jsonl'},
    ])))
    result = runner.inventory(tmp_path)
    assert result['known_gross_tokens'] == 38
    assert not result['usage_complete']
    assert result['duplicate_attempt_ids'] == ['a']
    assert len(result['native_logs']) == 2


def test_unique_ids_with_distinct_logs_still_reconcile(tmp_path):
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'control').mkdir()
    rows = []
    for identity, inputs in [('a', 10), ('b', 20)]:
        (tmp_path / f'logs/{identity}.events.jsonl').write_bytes(raw_usage(inputs))
        (tmp_path / f'control/{identity}-invocation.json').write_text(json.dumps({'id': identity}))
        rows.append({'id': identity, 'events': identity + '.events.jsonl'})
    (tmp_path / 'control/attempts.jsonl').write_text('\n'.join(map(json.dumps, rows)))
    result = runner.inventory(tmp_path)
    assert result['known_gross_tokens'] == 38 and result['usage_complete']
    assert result.get('duplicate_attempt_ids', []) == []


@pytest.mark.parametrize('identity', [None, '', True, 17, [], {}, '../bad', 'a' * 129])
def test_invalid_identity_retains_cost_and_fails_closed(tmp_path, identity):
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'control').mkdir()
    (tmp_path / 'logs/trial.events.jsonl').write_bytes(raw_usage(10))
    (tmp_path / 'control/attempts.jsonl').write_text(json.dumps({
        'id': identity, 'events': 'trial.events.jsonl',
    }))
    result = runner.inventory(tmp_path)
    assert result['known_gross_tokens'] == 14 and not result['usage_complete']
    assert result['invalid_attempt_ids'] == [1]


@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'nonstring'])
def test_invalid_attempt_ledger_stops_before_next_native_dispatch(tmp_path, monkeypatch, mutation):
    inventory_reader = runner.inventory
    setup_run(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, 'inventory', inventory_reader)
    launches = []

    class Process:
        returncode = 0

        def __init__(self, stdout):
            self.stdout = stdout

        def communicate(self, *args, **kwargs):
            self.stdout.write(raw_usage(10))
            self.stdout.flush()
            invocation = next((tmp_path / 'control').glob('*-invocation.json'))
            duplicate = json.loads(invocation.read_text())
            if mutation == 'missing':
                duplicate.pop('id')
            elif mutation == 'nonstring':
                duplicate['id'] = []
            (tmp_path / 'logs/duplicate.events.jsonl').write_bytes(raw_usage(20))
            duplicate['events'] = 'duplicate.events.jsonl'
            with (tmp_path / 'control/attempts.jsonl').open('a') as stream:
                stream.write(json.dumps(duplicate) + '\n')

    def launch(*args, **kwargs):
        launches.append(args)
        return Process(kwargs['stdout'])

    monkeypatch.setattr(native.subprocess, 'Popen', launch)
    result = native.execute_live(tmp_path, {}, tmp_path / 'auth')
    assert len(launches) == result['attempts'] == 1
    assert result['status'] == 'partial' and 'Unreconciled prior native usage' in result['error']
    assert (result['inventory']['duplicate_attempt_ids']
            or result['inventory']['invalid_attempt_ids'])
    assert result['inventory']['known_gross_tokens'] == 38
