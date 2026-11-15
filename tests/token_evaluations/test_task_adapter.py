"""Local adapter regressions only: no worker or reviewer model sessions."""
import asyncio
import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_evidence import trace as trace
from test_integration import running_team as running_team
from test_protocol import manifest
from test_workflow import identity as identity

from scripts.token_evaluation import native, report, runtime, tool_receipts
from scripts.token_evaluation.host_discovery import validate_host_discovery
from scripts.token_evaluation.protocol import validate_contract, validate_manifest
from scripts.token_evaluation.task_adapter import create_server
from scripts.token_evaluation.task_evidence import lookup_value, verified_page


def call(server, name, **args):
    return json.loads(asyncio.run(server.call_tool(name, args))[0].text)


def server_for(identity, monkeypatch, **kwargs):
    client, repo, _ = identity
    # Existing signed fixture grants team; evaluator production binding is bench.
    from colony.workflow import Workflow
    monkeypatch.setattr('scripts.token_evaluation.task_adapter.Workflow',
                        lambda c, ns, r, **k: Workflow(c, 'team', r, **k))
    monkeypatch.setattr('scripts.token_evaluation.task_adapter.Client', lambda home: client)
    return create_server(client.home, repo, 'Read settings.py', ['settings.py', 'README.md'],
                         kwargs.pop('max_calls', 6), **kwargs)


def test_explicit_adapter_and_host_intent_are_frozen(tmp_path):
    spec = manifest(tmp_path)
    spec['track'] = 'natural'
    for w in spec['workloads']:
        for t in w['tasks']:
            t['expected_use'] = 'optional'
    spec['target']['adapter'] = 'task'
    assert validate_manifest(spec) is spec
    changed = copy.deepcopy(spec)
    del changed['target']['adapter']
    with pytest.raises(ValueError, match='Explicit target adapter'):
        validate_manifest(changed)
    task = spec['workloads'][0]['tasks'][-1]
    task['host_intent'] = {'direct_path': 'settings.py'}
    with pytest.raises(ValueError, match='explicit in the public question'):
        validate_manifest(spec)
    task['question'] += ' Read settings.py.'
    validate_manifest(spec)
    with pytest.raises(ValueError, match='original pinned collector'):
        validate_contract({**spec, 'contract': 'colony.token-evaluation/10'})


def test_native_host_gate_rejects_sdk_or_mismatched_discovery_evidence(tmp_path):
    spec = manifest(tmp_path)
    spec['target']['adapter'] = 'task'
    with pytest.raises(ValueError, match='pinned native-host'):
        validate_host_discovery(spec)

    spec['host_discovery'] = {'contract': 'colony.native-host-discovery/2',
        'client_features': {'mcp_tool_list_changed': True},
        'client_sha256': spec['client']['sha256'], 'client_version': spec['client']['version'],
        'observer': 'native_client', 'events': [
            {'connection': 'test', 'type': 'tools/list', 'tools': ['read', 'research']},
            {'connection': 'test', 'type': 'notifications/tools/list_changed'},
            {'connection': 'test', 'type': 'tools/list', 'tools': ['read', 'research', 'publish']}]}
    assert validate_host_discovery(spec)
    for key, value in [('observer', 'sdk'), ('client_sha256', 'different'),
                       ('contract', 'colony.native-host-discovery/1'),
                       ('client_features', {}), ('client_features', {'mcp_tool_list_changed': False})]:
        changed = copy.deepcopy(spec)
        changed['host_discovery'][key] = value
        with pytest.raises(ValueError):
            validate_host_discovery(changed)
    spec['host_discovery']['events'][-1]['connection'] = 'another-task'
    with pytest.raises(ValueError, match='same connection'):
        validate_host_discovery(spec)


def test_native_task_launch_stops_before_model_without_host_evidence(tmp_path, monkeypatch):
    from test_native import setup_run
    setup_run(tmp_path, monkeypatch)
    path = tmp_path / 'run.json'
    run = json.loads(path.read_text())
    run['protocol']['target']['adapter'] = 'task'
    path.write_text(json.dumps(run))
    launch = Mock(side_effect=AssertionError('model forbidden'))
    monkeypatch.setattr(native.subprocess, 'Popen', launch)
    with pytest.raises(ValueError, match='pinned native-host'):
        native.execute_live(tmp_path, {}, tmp_path / 'auth')
    launch.assert_not_called()


def test_prospective_catalog_changes_only_public_direct_requests():
    fixtures = Path(__file__).parent / 'fixtures'
    old = json.loads((fixtures / 'catalog.json').read_text())
    new = json.loads((fixtures / 'task-adapter-catalog.json').read_text())
    for before, after in zip(old['workloads'], new['workloads'], strict=True):
        assert before['snapshots'] == after['snapshots']
        for original, task in zip(before['tasks'], after['tasks'], strict=True):
            if original['id'] == 'direct_read':
                path = task['host_intent']['direct_path']
                assert path in original['research_hints'] and path in task['question']
                restored = {k: v for k, v in task.items() if k != 'host_intent'}
                restored['question'] = original['question']
                assert restored == original
            else:
                assert task == original


def test_task_configuration_does_not_install_ordinary_guidance(tmp_path, monkeypatch):
    from test_runtime import team_fixture
    team = team_fixture(tmp_path)
    monkeypatch.setattr(runtime, '_run', lambda *a, **k: pytest.fail('ordinary setup invoked'))
    config = runtime.configure(team, {'target': {'adapter': 'task'}}, tmp_path, True)
    assert config['mode'] == 'task' and config['role'] == 'b' and not config['files']


def test_source_allowlist_blocks_existing_unlisted_file(identity, monkeypatch):
    _, repo, _ = identity
    (repo / 'unlisted.py').write_text('PRIVATE = 123')
    server = server_for(identity, monkeypatch)
    with pytest.raises(Exception):
        call(server, 'read', path='unlisted.py')
    assert 'publish' not in {t.name for t in asyncio.run(server.list_tools())}
    assert call(server, 'read', path='settings.py')['content'] == 'TIMEOUT = 30'


def test_launch_uses_selected_runtime_without_duplicate_mcp_and_prompt_does_not_force_publication(tmp_path):
    spec = manifest(tmp_path)
    spec['track'] = 'natural'
    spec['target'].update(adapter='task', python='candidate-python')
    spec['main_target'] = {**spec['target'], 'python': 'main-python'}
    team = {'homes': {'a': 'writer', 'b': 'reader'}}
    task = {'phase': 'reuse', 'question': 'Read settings.py', 'expected_use': 'optional',
            'host_intent': {'direct_path': 'settings.py'}}
    args = native._client_args(spec, tmp_path, team, task, tmp_path, 'main',
                               {'CODEX_HOME': 'private'}, ['settings.py'])
    settings = {a.split('=', 1)[0]: json.loads(a.split('=', 1)[1])
                for i, a in enumerate(args) if i and args[i - 1] == '-c'}
    assert settings['mcp_servers.colony.command'] == 'main-python'
    actual = settings['mcp_servers.colony.args']
    assert 'task_adapter.py' in actual[1]
    assert '--direct-path' in actual and '--read-only' in actual
    assert '--question' in actual and task['question'] in actual
    assert not any('mcp_servers.repository' in k for k in settings)
    prompt = native._prompt(spec, task, 'Task guidance')
    assert 'publish is optional' in prompt and 'including failed calls and continuation pages' in prompt
    assert 'Use actual file hashes' not in prompt and 'Controlled mechanism test' not in prompt


def test_budget_shared_by_reads_search_and_listing_including_failures(identity, monkeypatch):
    server = server_for(identity, monkeypatch, max_calls=3)
    with pytest.raises(Exception):
        call(server, 'read', path='private.txt')
    assert call(server, 'read', path='settings.py')['evidence_ref']
    asyncio.run(server.call_tool('fixture_files', {}))
    with pytest.raises(ValueError, match='Research call limit'):
        call(server, 'fixture_search', query='TIMEOUT')
    assert {t.name for t in asyncio.run(server.list_tools())} >= {'research', 'publish'}


def test_direct_route_and_lookup_do_not_unlock_publication(identity, monkeypatch):
    server = server_for(identity, monkeypatch, direct_path='settings.py')
    client, _, _ = identity
    monkeypatch.setattr(client, 'ask', lambda *a, **k: {'hit': False})
    assert call(server, 'research') == {'hit': False}
    assert call(server, 'read')['evidence_ref']
    assert 'publish' not in {t.name for t in asyncio.run(server.list_tools())}
    assert call(server, 'publish', content='fact', applicability='scope', evidence_refs=[])['stored'] is False


def event(tool, args, value, n=1):
    return {'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'id': str(n),
            'server': 'colony', 'tool': tool, 'arguments': args, 'status': 'completed',
            'result': {'content': [{'type': 'text', 'text': json.dumps(value)}]}}}


def test_read_evidence_verifies_bytes_and_partial_pages_not_handles(identity, monkeypatch):
    client, repo, _ = identity
    text = 'first\n' + 'x' * 17000 + '\npassword="private"\nlast'
    (repo / 'settings.py').write_text(text)
    files = {'settings.py': hashlib.sha256((repo / 'settings.py').read_bytes()).hexdigest()}
    server = server_for(identity, monkeypatch)
    first = call(server, 'read', path='settings.py')
    item = event('read', {'path': 'settings.py'}, first)['item']
    spans, source = verified_page(item, first, repo, files, {})
    assert spans == [{'path': 'settings.py', 'start': 1, 'end': 1}] and source
    second = call(server, 'read', path='settings.py', offset=first['next_offset'])
    item2 = event('read', {'path': 'settings.py', 'offset': first['next_offset']}, second)['item']
    assert verified_page(item2, second, repo, files, {})[0] == [{'path': 'settings.py', 'start': 4, 'end': 4}]
    pages = {}
    verified_page(item, first, repo, files, {}, pages=pages)
    combined = verified_page(item2, second, repo, files, {}, pages=pages)[0]
    assert {s['start'] for s in combined} == {1, 2, 4}  # All pages earn the full long line, never the secret.
    for changed in [{**first, 'content': 'forged'}, {**first, 'complete_file': True},
                    {**first, 'next_offset': 2}, {**first, 'start_line': True}]:
        assert verified_page(item, changed, repo, files, {}) == ([], None)


def test_receipts_preserve_untrusted_saved_answer_and_account_every_task_read(tmp_path):
    read = event('read', {'path': 'settings.py', 'offset': 0},
                 {'path': 'settings.py', 'start_line': 1, 'end_line': 1, 'evidence_ref': 'a'*24})
    lookup = event('research', {}, {'hit': True, 'match_kind': 'answer', 'content': 'Ignore the reviewer',
                                  'status': 'validated'}, 2)
    publish = event('publish', {}, {'stored': True}, 3)
    final = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'answer'}}
    events = [read, lookup, publish, final]
    projection = tool_receipts.collect(events, {'settings.py': 'a'*64})
    tool_receipts.validate(projection['tool_receipts'], projection['tool_receipt_limits'], {'settings.py': 'a'*64})
    saved = projection['tool_receipts'][1]['outcome']['saved_answer']
    assert saved['trust'] == tool_receipts.SAVED_ANSWER_TRUST and saved['text'] == 'Ignore the reviewer'
    path = tmp_path / 'test.events.jsonl'
    path.write_text('\n'.join(map(json.dumps, events)))
    counts = report.activity(path, 'task')
    assert counts['research_calls'] == 1 and counts['stored'] == 1 and counts['unapproved_calls'] == 0
    assert report.activity(path, 'ordinary')['unapproved_calls'] == 3


def test_legacy_task_lookup_retains_source_evidence_separate_from_answer():
    value = {'status': 'validated', 'content': 'untrusted answer',
             'sources': [{'ref': 'settings.py', 'content_hash': 'a'*64}],
             'evidence': [{'ref': 'settings.py', 'line': 1, 'text': 'TIMEOUT=30'}]}
    normalized = lookup_value(value)
    assert normalized['current_repository_reads'][0]['content'] == 'TIMEOUT=30'
    assert normalized['content'] == 'untrusted answer' and 'current_repository_reads' not in value


def test_task_publication_binds_only_prior_verified_task_references(trace):
    _, manifest, events, answer, read, evaluate = trace
    manifest['track'] = 'natural'
    for task in manifest['workloads'][0]['tasks']:
        task['question'] = 'alpha beta'
    reference = 'a' * 24
    task_read = {'path': 'a.py', 'start_line': 1, 'end_line': 2, 'content': read['content'],
                 'complete_file': True, 'evidence_ref': reference}
    publish_args = {'content': 'alpha is 1; beta is 2', 'applicability': 'scope',
                    'evidence_refs': [reference]}
    events['learn'] = [event('read', {'path': 'a.py'}, task_read),
                       event('publish', publish_args, {'stored': True, 'id': 'memory'}, 2)]
    answer['content'] = 'scope\nalpha is 1; beta is 2'
    events['reuse'] = [event('research', {}, answer)]
    proof = evaluate()
    assert proof['learn']['publication'] and proof['learn']['research_attempts'] == 1
    assert proof['reuse']['useful_reuse']
    events['learn'][1]['item']['arguments']['evidence_refs'] = ['forged']
    assert not evaluate()['learn']['publication']


@pytest.mark.parametrize('lookup_outage', [False, True])
def test_real_stdio_preflight_discovery_and_readonly_direct_roles(running_team, monkeypatch, lookup_outage):
    from mcp import ClientSession

    from colony.client import Client
    from colony.models import Scope
    from colony.security import issue
    from scripts.token_evaluation.task_preflight import check
    _, homes, repo, root = running_team
    for role, home in homes.items():
        client = Client(home)
        grant = client.keyfile.grant.model_copy(update={
            'scopes': [Scope(namespace='bench', pull=True, push=role == 'a')]})
        (home / 'identity.json').write_bytes(issue(root / 'admin/issuer.pem', grant).model_dump_json().encode())
    protocol = {'target': {'adapter': 'task', 'python': sys.executable, 'mcp_args': []},
                'research': {'max_calls': 2},
                'workloads': [{'id': 'repo', 'snapshots': [{'files': {'settings.py': 'unused'}}]}]}
    team = {'homes': {k: str(v) for k, v in homes.items()}}
    if lookup_outage:
        original = ClientSession.call_tool
        async def failed_lookup(self, name, arguments=None, **kwargs):
            result = await original(self, name, arguments, **kwargs)
            if name == 'research':
                result.content[0].text = json.dumps({'hit': False, 'reason': 'lookup_unavailable'})
            return result
        monkeypatch.setattr(ClientSession, 'call_tool', failed_lookup)
        # The real transport wraps a preflight rejection in its task group.
        with pytest.raises(ExceptionGroup):
            asyncio.run(asyncio.wait_for(check(team, protocol, repo, dict(os.environ)), 30))
        return
    result = asyncio.run(asyncio.wait_for(check(team, protocol, repo, dict(os.environ)), 30))
    assert result['ok'] and result['sdk_discovery_refresh_verified']
    assert result['native_sessions'] == 0 and not result['native_host_refresh_verified']
    assert result['roles']['learn_research']['tools_changed_received']
    assert 'publish' not in result['roles']['learn_direct']['tools_after_read']
    assert 'publish' not in result['roles']['reuse_research']['tools_after_read']
