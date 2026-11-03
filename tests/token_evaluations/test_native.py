import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.token_evaluation import native, runner
from scripts.token_evaluation.protocol import attempt_id


@pytest.mark.parametrize('host,shell,passes', [('true', 'false', True), ('false', 'false', False), ('true', 'true', False)])
def test_metadata_parses_actual_overrides_without_model_or_mcp(tmp_path, monkeypatch, host, shell, passes):
    called = []
    def metadata(args, **kwargs):
        called.append(args)
        return SimpleNamespace(stdout=f'code_mode_host stable {host}\nshell_tool stable {shell}\n')
    monkeypatch.setattr(native.subprocess, 'run', metadata)
    args = ['client', 'exec', '--json', '--disable', 'shell_tool', '-c', 'web_search="disabled"', '-']
    if passes:
        receipt = native._client_metadata(args, {})
        assert receipt['native_sessions'] == 0
    else:
        with pytest.raises(ValueError, match='metadata mismatch'):
            native._client_metadata(args, {})
    assert called == [['client', 'features', 'list', '--disable', 'shell_tool', '-c', 'web_search="disabled"']]


def setup_run(tmp_path, monkeypatch):
    protocol = {'limits': {'wall_seconds': 60, 'max_attempts': 2, 'gross_token_stop': 100, 'session_seconds': 5}, 'workloads': [{'id': 'w', 'snapshots': [{'id': 'v1', 'files': {}}], 'tasks': [{'id': 't', 'snapshot': 'v1', 'phase': 'learn', 'question': 'q'}]}], 'client': {'model': 'm', 'sha256': 'a'}, 'target': {'revision': 'r'}, 'research': {'max_calls': 1}, 'output': {'format': 'json', 'answer_policy': 'last_agent_message'}, 'track': 'controlled'}
    run = {'protocol': protocol, 'protocol_sha256': 'digest'}
    for name in ('control', 'logs', 'auth', 'private'):
        (tmp_path / name).mkdir()
    (tmp_path / 'auth/auth.json').write_text('{}')
    (tmp_path / 'run.json').write_text(json.dumps(run))
    (tmp_path / 'preflight.json').write_text(json.dumps({'ok': True, 'native_ready': True, 'protocol_sha256': 'digest'}))
    monkeypatch.setattr(native, 'validate_authorization', lambda *a: None)
    monkeypatch.setattr(native.runtime, 'environment', lambda *a: {})
    monkeypatch.setattr(native, '_copy_snapshot', lambda *a: None)
    monkeypatch.setattr(native, '_check_snapshot', lambda *a: None)
    monkeypatch.setattr(native.runtime, 'provision', lambda *a: {'id': 'actual-team'})
    events = []
    @contextmanager
    def server(*a):
        events.append('start')
        try:
            yield
        finally:
            events.append('stop')
    monkeypatch.setattr(native.runtime, 'server', server)
    monkeypatch.setattr(native.runtime, 'mcp_preflight', lambda team, *a: events.append(team['id']) or {'ok': True})
    monkeypatch.setattr(native.runtime, 'trail_count', lambda *a: 0)
    monkeypatch.setattr(runner, '_verify', lambda *a: None)
    monkeypatch.setattr(runner, 'inventory', lambda *a: {'known_gross_tokens': 0, 'native_logs': [], 'usage_complete': True})
    monkeypatch.setattr(native, '_resources', lambda *a: None)
    monkeypatch.setattr(native, '_client_args', lambda *a, **kw: ['fake-client'])
    monkeypatch.setattr(native.runtime, 'configure', lambda *a: {'files': []})
    return events


@pytest.mark.parametrize('checks,count', [({'ok': False}, 0), ({'ok': True}, 1)])
def test_actual_team_preflight_blocks_models(tmp_path, monkeypatch, checks, count):
    events = setup_run(tmp_path, monkeypatch)
    monkeypatch.setattr(native.runtime, 'mcp_preflight', lambda *a: checks)
    monkeypatch.setattr(native.runtime, 'trail_count', lambda *a: count)
    launch = Mock(side_effect=AssertionError('model forbidden'))
    monkeypatch.setattr(native.subprocess, 'Popen', launch)
    result = native.execute_live(tmp_path, {}, tmp_path / 'auth')
    assert result['status'] == 'partial'
    assert 'authenticated MCP' in result['error']
    launch.assert_not_called()
    assert events == ['start', 'stop']


def test_interruption_stops_child_and_server_and_retains_invocation(tmp_path, monkeypatch):
    events = setup_run(tmp_path, monkeypatch)
    process = Mock()
    process.communicate.side_effect = KeyboardInterrupt()
    monkeypatch.setattr(native.subprocess, 'Popen', lambda *a, **k: process)
    stopped = []
    monkeypatch.setattr(native.runtime, '_stop', lambda p: stopped.append(p))
    receipt = native.execute_live(tmp_path, {}, tmp_path / 'auth')
    assert receipt['status'] == 'partial'
    assert receipt['error'].startswith('KeyboardInterrupt')
    assert stopped == [process]
    assert events == ['start', 'actual-team', 'stop']
    assert list((tmp_path / 'control').glob('*-invocation.json'))
    invocation = json.loads(next((tmp_path / 'control').glob('*-invocation.json')).read_text())
    assert invocation['id'] == attempt_id('w', 't', 'no_colony')
    assert invocation['client_workspace'].replace('\\', '/').endswith('/sessions/' + invocation['id'])
    with pytest.raises(ValueError, match='no retries'):
        native.execute_live(tmp_path, {}, tmp_path / 'auth')


@pytest.mark.parametrize('field,value', [('ok', False), ('protocol_sha256', 'wrong'), ('native_ready', False)])
def test_matching_successful_preflight_required(tmp_path, monkeypatch, field, value):
    setup_run(tmp_path, monkeypatch)
    receipt = {'ok': True, 'native_ready': True, 'protocol_sha256': 'digest', field: value}
    (tmp_path / 'preflight.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='matching local preflight'):
        native.execute_live(tmp_path, {}, tmp_path / 'auth')


def test_source_drift_rejected_before_copy(tmp_path):
    source = tmp_path / 'frozen/w/v1'
    source.mkdir(parents=True)
    (source / 'a.py').write_text('drift')
    with pytest.raises(ValueError, match='Frozen source drift'):
        native._copy_snapshot({'arms': {'colony': {'workspace': str(tmp_path / 'frozen')}}}, 'colony', {'id': 'w', 'snapshots': [{'files': {'a.py': 'a' * 64}}]}, {'id': 'v1', 'files': {'a.py': 'a' * 64}}, tmp_path / 'active')


@pytest.mark.parametrize('kind', ['attempts', 'tokens', 'unknown'])
def test_usage_and_attempt_stops_before_launch(tmp_path, monkeypatch, kind):
    setup_run(tmp_path, monkeypatch)
    if kind == 'attempts':
        run = json.loads((tmp_path / 'run.json').read_text())
        run['protocol']['limits']['max_attempts'] = 0
        (tmp_path / 'run.json').write_text(json.dumps(run))
    monkeypatch.setattr(runner, 'inventory', lambda *a: {'known_gross_tokens': 100 if kind == 'tokens' else 0, 'native_logs': ['prior'] if kind == 'unknown' else [], 'usage_complete': kind != 'unknown'})
    launch = Mock(side_effect=AssertionError('must not launch'))
    monkeypatch.setattr(native.subprocess, 'Popen', launch)
    result = native.execute_live(tmp_path, {}, tmp_path / 'auth')
    assert result['status'] == 'partial'
    assert 'stop' in result['error']
    launch.assert_not_called()


def test_client_args_isolate_roles_and_repository(tmp_path):
    protocol = {'client': {'executable': 'codex', 'model': 'frozen', 'effort': 'low'}, 'target': {'python': 'python', 'mcp_args': ['--context-budget', '100']}, 'research': {'max_calls': 2}}
    team = {'homes': {'a': 'write-home', 'b': 'read-home'}}
    for phase, role in [('learn', 'write-home'), ('reuse', 'read-home')]:
        args = native._client_args(protocol, tmp_path, team, {'phase': phase}, tmp_path, 'colony', {'CODEX_HOME': 'private'}, ['source.py'])
        settings = dict(a.split('=', 1) for i, a in enumerate(args) if i and args[i - 1] == '-c')
        mcp = json.loads(settings['mcp_servers.colony.args'])
        assert role in mcp
        assert ('--workflow-read-only' in mcp) == (phase == 'reuse')
        assert json.loads(settings['mcp_servers.colony.required']) is True
        assert json.loads(settings['mcp_servers.repository.required']) is True
        assert '--context-budget' in mcp
        assert args[-1] == '-'
        assert '--ignore-user-config' in args and '--ignore-rules' in args
        assert args[args.index('-s') + 1] == 'read-only'
    client_dir = tmp_path / 'isolated-session'
    args = native._client_args(protocol, tmp_path / 'source', team, {'phase': 'reuse'}, tmp_path,
                               'colony', {'CODEX_HOME': 'private'}, ['source.py'], client_workspace=client_dir)
    assert args[args.index('-C') + 1] == str(client_dir)
    settings = dict(a.split('=', 1) for i, a in enumerate(args) if i and args[i - 1] == '-c')
    assert str(tmp_path / 'source') in json.loads(settings['mcp_servers.colony.args'])
    assert str(tmp_path / 'source') in json.loads(settings['mcp_servers.repository.args'])


def test_invalid_attempt_schedule_is_rejected_before_provision_or_launch(tmp_path, monkeypatch):
    from scripts.token_evaluation import protocol
    setup_run(tmp_path, monkeypatch)
    monkeypatch.setattr(protocol, 'attempt_id', lambda *args: 'same-identifier')
    provision = Mock(side_effect=AssertionError('must reject before starting local services'))
    launch = Mock(side_effect=AssertionError('must reject before model dispatch'))
    monkeypatch.setattr(native.runtime, 'provision', provision)
    monkeypatch.setattr(native.subprocess, 'Popen', launch)
    with pytest.raises(ValueError, match='Duplicate generated attempt identity'):
        native.execute_live(tmp_path, {}, tmp_path / 'auth')
    provision.assert_not_called()
    launch.assert_not_called()
    assert not (tmp_path / 'private/client').exists()


def test_same_basename_orphan_stops_before_next_native_dispatch(tmp_path, monkeypatch):
    """Synthetic process output tests dispatch safety, never model-token savings."""
    inventory_reader = runner.inventory
    setup_run(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, 'inventory', inventory_reader)
    launches = []
    raw = '\n'.join(map(json.dumps, [{'type': 'turn.started'}, {'type': 'turn.completed',
        'usage': {'input_tokens': 10, 'cached_input_tokens': 2, 'output_tokens': 4}}])).encode()
    class Process:
        returncode = 0
        def __init__(self, stdout):
            self.stdout = stdout
        def communicate(self, *args, **kwargs):
            self.stdout.write(raw)
            self.stdout.flush()
            from pathlib import Path
            unlisted = tmp_path / 'logs/unlisted' / Path(self.stdout.name).name
            unlisted.parent.mkdir(exist_ok=True)
            unlisted.write_bytes(raw)
    def launch(*args, **kwargs):
        launches.append(args)
        return Process(kwargs['stdout'])
    monkeypatch.setattr(native.subprocess, 'Popen', launch)
    value = native.execute_live(tmp_path, {}, tmp_path / 'auth')
    assert len(launches) == value['attempts'] == 1
    assert value['status'] == 'partial' and 'Unreconciled prior native usage' in value['error']
    assert value['inventory']['known_gross_tokens'] == 28
    assert len(value['inventory']['orphan_logs']) == 1
