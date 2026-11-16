import copy
import io
import json
import os
import asyncio
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_protocol import manifest
from test_workflow import identity as identity

from scripts.native_host_trace import Trace
from scripts.token_evaluation import native
from scripts.token_evaluation.protocol import collector_hashes, validate_contract
from scripts.token_evaluation.publication_diagnostics import observation, observer_path, trace_hash, trace_path


@pytest.mark.parametrize('arm', ['colony', 'main', 'no_colony'])
@pytest.mark.parametrize('phase,direct,remember,eligible', [
    ('learn', False, False, True), ('learn', True, False, False),
    ('learn', True, True, True), ('reuse', False, True, False),
])
def test_wrapper_preserves_child_and_trusted_intent(tmp_path, arm, phase, direct, remember, eligible):
    spec = manifest(tmp_path)
    spec['target']['adapter'] = 'task'
    spec['main_target'] = copy.deepcopy(spec['target'])
    task = {'phase': phase, 'question': 'Read settings.py',
            'host_intent': {'remember': remember, **({'direct_path': 'settings.py'} if direct else {})}}
    args = native._client_args(spec, tmp_path, {'homes': {'a': 'writer', 'b': 'reader'}},
                               task, tmp_path, arm, {'CODEX_HOME': 'private'}, ['settings.py'],
                               client_workspace=tmp_path / 'sessions/attempt-1')
    config = {args[i+1].split('=', 1)[0]: json.loads(args[i+1].split('=', 1)[1])
              for i, v in enumerate(args[:-1]) if v == '-c'}
    if arm == 'no_colony':
        assert 'mcp_servers.colony.args' not in config
        return
    wrapped = config['mcp_servers.colony.args']
    delimiter = wrapped.index('--')
    assert ('--eligible' in wrapped[:delimiter]) == eligible
    assert wrapped[1] == str(observer_path())
    assert wrapped[wrapped.index('--output')+1] == str(trace_path(tmp_path, 'attempt-1'))
    child = wrapped[delimiter+1:]
    assert child[0] == spec['target']['python'] and child[1] == '-I'
    assert 'task_adapter.py' in child[2]
    assert child[child.index('--question')+1] == task['question']
    assert ('--read-only' in child) == (phase == 'reuse')
    assert ('--direct-path' in child) == direct


def test_observer_pinned_and_historical_collectors_required(tmp_path):
    assert str(observer_path()) in collector_hashes()
    for version in (11, 12, 13, 14):
        with pytest.raises(ValueError, match='original pinned collector'):
            validate_contract({**manifest(tmp_path), 'contract': f'colony.token-evaluation/{version}'})


def test_diagnostics_are_bound_and_missing_or_truncated_data_is_not_abstention(tmp_path):
    path = trace_path(tmp_path, 'attempt-1')
    path.parent.mkdir()
    assert observation(tmp_path, 'attempt-1', None)['status'] == 'unavailable'
    stream = io.StringIO()
    trace = Trace(stream, True)
    trace.finish()
    path.write_text(stream.getvalue())
    expected = trace_hash(path)
    assert observation(tmp_path, 'attempt-1', expected)['status'] == 'no_referenced_read'
    path.write_text('SECRET')
    assert observation(tmp_path, 'attempt-1', expected)['status'] == 'unavailable'
    assert observation(tmp_path, 'attempt-1', trace_hash(path))['status'] == 'unavailable'
    value = json.loads(stream.getvalue())
    value['metadata_dropped'] = 1
    path.write_text(json.dumps(value))
    result = observation(tmp_path, 'attempt-1', trace_hash(path))
    assert result['status'] == 'incomplete_metadata' and result['abstention_reason'] == 'unobserved'
    value['status'] = 'SECRET'
    path.write_text(json.dumps(value))
    assert 'SECRET' not in str(observation(tmp_path, 'attempt-1', trace_hash(path)))


def test_real_task_transport_through_observer_preserves_discovery(identity, tmp_path):
    client, repo, _ = identity
    log = tmp_path / 'metadata.jsonl'

    async def check():
        args = [str(observer_path()), '--output', str(log), '--eligible', '--', sys.executable,
                '-m', 'colony.task_mcp', '--home', str(client.home), '--repo', str(repo),
                '--ns', 'team', '--question', 'Explain settings']
        params = StdioServerParameters(command=sys.executable, args=args,
            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            assert 'publish' not in {t.name for t in (await session.list_tools()).tools}
            result = await session.call_tool('read', {'path': 'settings.py'})
            assert not result.isError
            assert json.loads(result.content[0].text)['evidence_ref']
            assert 'publish' in {t.name for t in (await session.list_tools()).tools}
    asyncio.run(check())
    data = log.read_text()
    assert 'TIMEOUT' not in data and 'evidence_ref' not in data
    # The SDK checks transport only; it is not native-client discovery proof.
    assert any(json.loads(line).get('event') == 'tool_catalog'
               and json.loads(line).get('publish_present') for line in data.splitlines())


def test_natural_prompt_defers_to_product_policy_without_forcing_or_capping_answers(tmp_path):
    spec = manifest(tmp_path)
    spec.update(track='natural')
    spec['target']['adapter'] = 'task'
    prompt = native._prompt(spec, {'phase': 'learn', 'question': 'Explain all dependencies',
                                  'expected_use': 'optional'}, 'Task server policy')
    assert 'follow the task server policy for conditional publication' in prompt
    assert 'publish is optional storage' not in prompt
    assert 'publish one' not in prompt and 'publish exactly' not in prompt
    assert 'Complete all requested deliverables' in prompt
    assert 'publication limits never limit the answer' in prompt
