import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.token_evaluation import native, runner
from scripts.token_evaluation.output import JSON_INSTRUCTION, OUTPUT_CONTRACT
from scripts.token_evaluation.protocol import CONTRACT, attempt_id, collector_hashes


@pytest.mark.parametrize('track', ['controlled', 'natural'])
@pytest.mark.parametrize('expected_use', ['optional', 'required_publication'])
@pytest.mark.parametrize('phase', ['learn', 'reuse'])
@pytest.mark.parametrize('max_calls', [1, 6, 12])
def test_prompt_forcing_and_path_hints_respect_track_and_optional_tasks(track, expected_use, phase, max_calls):
    protocol = {'contract': CONTRACT, 'output': {'contract': OUTPUT_CONTRACT,
                'format': 'json', 'answer_policy': 'last_agent_message'},
                'research': {'max_calls': max_calls}, 'track': track}
    task = {'phase': phase, 'expected_use': expected_use, 'question': 'What is the timeout?',
            'memory_query': 'Distinct frozen lookup', 'publication_sources': ['published.py'],
            'research_hints': ['direct.py']}
    baseline = native._prompt(protocol, task, '')
    colony = native._prompt(protocol, task, 'Ordinary configured guidance.')
    controlled = track == 'controlled' and expected_use != 'optional'
    assert ('Controlled mechanism test:' in colony) == controlled
    assert ('Distinct frozen lookup' in colony) == controlled
    assert 'Controlled mechanism test:' not in baseline
    assert 'Distinct frozen lookup' not in baseline
    expected_hints = ['direct.py'] if expected_use == 'optional' else (
        ['published.py'] if controlled and phase == 'learn' else None)
    for prompt in [baseline, colony]:
        assert ('Repository path hints' in prompt) == bool(expected_hints)
        if expected_hints:
            assert ('Repository path hints (paths only, not evidence): '
                    + json.dumps(expected_hints) + '.') in prompt
        assert ('["direct.py"]' in prompt) == (expected_hints == ['direct.py'])
        assert ('["published.py"]' in prompt) == (expected_hints == ['published.py'])
        budget_instruction = (
            f'Repository research budget: {max_calls} calls to '
            'fixture_files, fixture_read and fixture_search; count each call in a batch '
            'and every failed attempt. Colony calls do not consume this repository '
            'allowance; all calls remain charged in token accounting. '
        )
        assert prompt.count(budget_instruction) == 1
        assert 'Question: What is the timeout?' in prompt
    assert ('publish exactly the source files' in colony) == (controlled and phase == 'learn')
    assert 'publish exactly the source files' not in baseline
    if not controlled:
        assert colony.removeprefix('Configured project guidance:\nOrdinary configured guidance.\n\n') == baseline


@pytest.mark.parametrize('track', ['natural', 'controlled'])
def test_catalog_tasks_share_research_contract_with_only_declared_arm_differences(track):
    catalog = json.loads((Path(__file__).parent / 'fixtures/catalog.json').read_text())
    protocol = {'contract': CONTRACT, 'output': {'contract': OUTPUT_CONTRACT,
                'format': 'json', 'answer_policy': 'last_agent_message'},
                'research': {'max_calls': 6}, 'track': track}
    for workload in catalog['workloads']:
        for original in workload['tasks']:
            task = {**original, **({'expected_use': 'optional'} if track == 'natural' else {})}
            baseline = native._prompt(protocol, task, '')
            candidate = native._prompt(protocol, task, 'Installed guidance.').removeprefix(
                'Configured project guidance:\nInstalled guidance.\n\n')
            controlled = track == 'controlled' and task['expected_use'] != 'optional'
            recipe_lines = [line for line in candidate.splitlines(keepends=True)
                            if line.startswith(('For the first Colony ask,', 'Controlled mechanism test:',
                                                'For this controlled learning step,'))]
            assert bool(recipe_lines) == controlled
            for line in recipe_lines:
                candidate = candidate.replace(line, '')
            assert candidate == baseline, (track, workload['id'], task['id'])
            assert baseline.count(JSON_INSTRUCTION) == 1


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
    protocol.update(contract=CONTRACT, collector={'files': collector_hashes()})
    protocol['output']['contract'] = OUTPUT_CONTRACT
    run = {'protocol': protocol, 'protocol_sha256': 'digest', 'run_dir': str(tmp_path.resolve())}
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


def deadline_run(tmp_path, monkeypatch):
    """Real resource/inventory checks with a synthetic clock and native-process output."""
    resources, inventory_reader = native._resources, runner.inventory
    events = setup_run(tmp_path, monkeypatch)
    run = json.loads((tmp_path / 'run.json').read_text())
    run['protocol']['limits'].update(wall_seconds=1, disk_bytes=10000000, min_free_bytes=1)
    (tmp_path / 'run.json').write_text(json.dumps(run))
    monkeypatch.setattr(native, '_resources', resources)
    monkeypatch.setattr(runner, 'inventory', inventory_reader)
    clock = [0.0]
    monkeypatch.setattr(native.time, 'monotonic', lambda: clock[0])
    processes, stopped = [], []
    raw = '\n'.join(map(json.dumps, [{'type': 'turn.started'}, {'type': 'turn.completed',
        'usage': {'input_tokens': 10, 'cached_input_tokens': 2, 'output_tokens': 4}}])).encode()
    class Process:
        returncode = 0
        def __init__(self, stdout):
            self.stdout = stdout
            self.launch_time = clock[0]
            self.inputs = []
            self.timeouts = []
        def communicate(self, prompt, *, timeout):
            self.inputs.append(prompt)
            self.timeouts.append(timeout)
            self.stdout.write(raw)
            self.stdout.flush()
    def launch(*args, **kwargs):
        process = Process(kwargs['stdout'])
        processes.append(process)
        return process
    monkeypatch.setattr(native.subprocess, 'Popen', launch)
    monkeypatch.setattr(native.runtime, '_stop', lambda process: stopped.append(process))
    return clock, processes, stopped, events, launch


def test_setup_expiry_blocks_next_launch_and_retains_prior_usage(tmp_path, monkeypatch):
    clock, processes, stopped, events, _ = deadline_run(tmp_path, monkeypatch)
    def configure(*args):
        clock[0] = 2.0
        return {'files': []}
    monkeypatch.setattr(native.runtime, 'configure', configure)
    value = native.execute_live(tmp_path, {}, tmp_path / 'auth')
    assert len(processes) == 1 and processes[0].launch_time == 0
    assert len(processes[0].inputs) == value['attempts'] == 1
    assert value['status'] == 'partial' and 'wall-time budget exhausted' in value['error']
    assert value['inventory']['known_gross_tokens'] == 14
    assert value['inventory']['usage_complete'] is False
    assert len(value['inventory']['orphan_invocations']) == 1
    assert not stopped and events[-1] == 'stop'


@pytest.mark.parametrize('elapsed', [1.0, 2.0])
def test_expiry_during_invocation_write_blocks_launch(tmp_path, monkeypatch, elapsed):
    clock, processes, _, events, _ = deadline_run(tmp_path, monkeypatch)
    save = native._save
    def save_then_expire(path, value):
        save(path, value)
        if str(path).endswith('-invocation.json'):
            clock[0] = elapsed
    monkeypatch.setattr(native, '_save', save_then_expire)
    value = native.execute_live(tmp_path, {}, tmp_path / 'auth')
    assert processes == []
    assert value['status'] == 'partial' and 'wall-time budget exhausted' in value['error']
    assert value['inventory']['orphan_invocations'] and events[-1] == 'stop'


def test_expiry_during_process_start_stops_child_without_sending_prompt(tmp_path, monkeypatch):
    clock, processes, stopped, events, launch = deadline_run(tmp_path, monkeypatch)
    def slow_launch(*args, **kwargs):
        process = launch(*args, **kwargs)
        clock[0] = 2.0
        return process
    monkeypatch.setattr(native.subprocess, 'Popen', slow_launch)
    value = native.execute_live(tmp_path, {}, tmp_path / 'auth')
    assert len(processes) == 1 and processes[0].inputs == []
    assert stopped == processes and events[-1] == 'stop'
    assert value['status'] == 'partial' and 'wall-time budget exhausted' in value['error']
    assert value['inventory']['orphan_invocations']
    assert value['inventory']['usage_complete'] is False


def test_small_positive_wall_remainder_is_not_inflated(tmp_path, monkeypatch):
    clock, processes, stopped, _, _ = deadline_run(tmp_path, monkeypatch)
    def prepare_args(*args, **kwargs):
        clock[0] = 0.96
        return ['fake-client']
    monkeypatch.setattr(native, '_client_args', prepare_args)
    value = native.execute_live(tmp_path, {}, tmp_path / 'auth')
    assert value['status'] == 'complete' and len(processes) == 2
    assert all(process.timeouts == [pytest.approx(0.04)] for process in processes)
    assert value['inventory']['known_gross_tokens'] == 28
    assert stopped == []


@pytest.mark.parametrize('entrypoint', ['preflight', 'execute'])
@pytest.mark.parametrize('change', ['legacy', 'previous', 'research_v3', 'evidence_v4', 'missing_output_contract', 'wrong_output_contract',
                                  'missing_collector', 'partial_collector', 'wrong_hash', 'extra_file'])
def test_contract_and_collector_rejected_before_native_side_effects(tmp_path, monkeypatch,
                                                                  entrypoint, change):
    setup_run(tmp_path, monkeypatch)
    run = json.loads((tmp_path / 'run.json').read_text())
    protocol = run['protocol']
    if change == 'legacy':
        protocol['contract'] = 'colony.token-evaluation/1'
    elif change == 'previous':
        protocol['contract'] = 'colony.token-evaluation/2'
    elif change == 'research_v3':
        protocol['contract'] = 'colony.token-evaluation/3'
    elif change == 'evidence_v4':
        protocol['contract'] = 'colony.token-evaluation/4'
    elif change == 'missing_output_contract':
        protocol['output'].pop('contract')
    elif change == 'wrong_output_contract':
        protocol['output']['contract'] = 'other'
    elif change == 'missing_collector':
        protocol.pop('collector')
    elif change == 'partial_collector':
        protocol['collector']['files'].pop(next(iter(protocol['collector']['files'])))
    elif change == 'wrong_hash':
        protocol['collector']['files'][next(iter(protocol['collector']['files']))] = '0' * 64
    else:
        protocol['collector']['files'][str(tmp_path / 'other.py')] = '0' * 64
    (tmp_path / 'run.json').write_text(json.dumps(run))
    environment, provision, launch = Mock(), Mock(), Mock()
    monkeypatch.setattr(native.runtime, 'environment', environment)
    monkeypatch.setattr(native.runtime, 'provision', provision)
    monkeypatch.setattr(native.subprocess, 'Popen', launch)
    with pytest.raises(ValueError):
        if entrypoint == 'preflight':
            native.preflight_live(tmp_path)
        else:
            native.execute_live(tmp_path, {}, tmp_path / 'auth')
    environment.assert_not_called()
    provision.assert_not_called()
    launch.assert_not_called()
    assert not (tmp_path / 'private/client').exists()


@pytest.mark.parametrize('track', ['natural', 'controlled'])
def test_grading_metadata_never_becomes_agent_hints(track):
    """Prospective rubric corrections must not supply solutions to either arm."""
    from copy import deepcopy
    catalog = json.loads((Path(__file__).parent / 'fixtures/catalog.json').read_bytes())
    protocol = {'contract': CONTRACT, 'output': {'contract': OUTPUT_CONTRACT,
                'format': 'json', 'answer_policy': 'last_agent_message'},
                'research': {'max_calls': 6}, 'track': track}
    for workload in catalog['workloads']:
        for original in workload['tasks']:
            task = deepcopy(original)
            if track == 'natural':
                task['expected_use'] = 'optional'
            changed = deepcopy(task)
            changed['oracle'] = {'facts': {'sentinel': 'PRIVATE_ORACLE_ANSWER'},
                                 'citations': [{'path': 'PRIVATE_ORACLE_PATH', 'start': 999, 'end': 999}],
                                 'evidence_policy': 'PRIVATE_EVIDENCE_RULE'}
            changed['rubric'] = {'correct': 'PRIVATE_CORRECT_RULE',
                                 'grounded': 'PRIVATE_GROUNDING_RULE'}
            for guidance in ('', 'Installed Colony guidance.'):
                assert native._prompt(protocol, task, guidance) == native._prompt(
                    protocol, changed, guidance), (track, workload['id'], task['id'])
