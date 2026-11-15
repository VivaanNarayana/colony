"""Actual producer parity and synthetic reviewer controls; no model sessions."""
import copy
import hashlib
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from test_agent_quality import build_reviewed_run
from test_evidence import trace as trace
from test_report import put, rewrite_events
from test_report import run as run
from test_task_adapter import call, event, server_for
from test_workflow import identity as identity

from scripts.token_evaluation.protocol import digest
from scripts.token_evaluation.task_adapter import create_server
from scripts.token_evaluation.task_evidence import verified_page
from scripts.token_evaluation.usage import native_usage

ACCEPTED = [
    {}, {'path': None}, {'path': 'null'}, {'path': 'settings.py', 'start': '1'},
    {'path': 'settings.py', 'end': '1'}, {'start': '1.0', 'end': '1.0'},
    {'start': 1.0, 'end': 1.0}, {'start': True, 'end': True},
    {'start': ' 1 ', 'end': '+1'}, {'end': 'null'},
]


@pytest.mark.parametrize('args', ACCEPTED)
@pytest.mark.parametrize('serialized', [False, True])
def test_successful_producer_arguments_preserve_evidence(identity, monkeypatch, args, serialized):
    _, repo, _ = identity
    server = server_for(identity, monkeypatch, direct_path='settings.py')
    result = call(server, 'read', **args)
    assert result['content'] == 'TIMEOUT = 30' and result['evidence_ref']
    files = {'settings.py': hashlib.sha256((repo / 'settings.py').read_bytes()).hexdigest()}
    item = event('read', json.dumps(args) if serialized else args, result)['item']
    before = copy.deepcopy(item)
    spans, source = verified_page(item, result, repo, files, {'host_intent': {'direct_path': 'settings.py'}})
    assert spans == [{'path': 'settings.py', 'start': 1, 'end': 1}]
    assert source == {'ref': 'settings.py', 'content_hash': files['settings.py']}
    assert item == before  # Raw historical arguments are never rewritten.


@pytest.mark.parametrize('args', [
    {'path': 1}, {'path': []}, {'path': '[]'}, {'start': None}, {'start': 'null'},
    {'start': 1.2}, {'start': '1.2'}, {'start': 'true'}, {'start': []},
    {'start': 0}, {'end': 0}, {'end': -1}, {'end': 2},
    {'offset': '0'}, {'offset': 0.0}, {'offset': True}, {'offset': None}, {'offset': -1},
])
def test_rejected_producer_arguments_cannot_gain_evidence(identity, monkeypatch, args):
    _, repo, _ = identity
    server = server_for(identity, monkeypatch, direct_path='settings.py')
    valid = call(server, 'read')
    with pytest.raises(ToolError):
        call(server, 'read', **args)
    files = {'settings.py': hashlib.sha256((repo / 'settings.py').read_bytes()).hexdigest()}
    item = event('read', args, valid)['item']  # Forged success for an invalid invocation.
    pages = {}
    assert verified_page(item, valid, repo, files, {'host_intent': {'direct_path': 'settings.py'}},
                         pages=pages) == ([], None)
    assert pages == {}


@pytest.mark.parametrize('malformed', ['{', '[]', None, [], 'null'])
def test_malformed_argument_envelopes_do_not_become_default_reads(identity, monkeypatch, malformed):
    _, repo, _ = identity
    result = call(server_for(identity, monkeypatch, direct_path='settings.py'), 'read')
    files = {'settings.py': hashlib.sha256((repo / 'settings.py').read_bytes()).hexdigest()}
    assert verified_page(event('read', malformed, result)['item'], result, repo, files,
                         {'host_intent': {'direct_path': 'settings.py'}}) == ([], None)


@pytest.mark.parametrize('mutation', ['content', 'path', 'range', 'hash', 'host_path', 'no_host_path',
                                     'unlisted', 'changed_source', 'offset'])
def test_normalization_never_waives_source_bindings(identity, monkeypatch, mutation):
    _, repo, _ = identity
    args = {'path': None, 'start': '1', 'end': '1'}
    result = call(server_for(identity, monkeypatch, direct_path='settings.py'), 'read', **args)
    files = {'settings.py': hashlib.sha256((repo / 'settings.py').read_bytes()).hexdigest()}
    task = {'host_intent': {'direct_path': 'settings.py'}}
    if mutation == 'content':
        result['content'] = 'TIMEOUT = 9000'
    elif mutation == 'path':
        result['path'] = 'README.md'
    elif mutation == 'range':
        result['start_line'] = True
    elif mutation == 'hash':
        files['settings.py'] = 'a' * 64
    elif mutation == 'host_path':
        task['host_intent']['direct_path'] = 'README.md'
    elif mutation == 'no_host_path':
        task = {}
    elif mutation == 'unlisted':
        files = {}
    elif mutation == 'changed_source':
        (repo / 'settings.py').write_text('TIMEOUT = 9000')
    else:
        result['start_offset'] = 0
    pages = {}
    assert verified_page(event('read', args, result)['item'], result, repo, files, task,
                         pages=pages) == ([], None)
    assert pages == {}


@pytest.mark.parametrize('args', [{'path': None}, {'start': '1'}, {'end': '1'}])
def test_reviewer_packet_keeps_evidence_and_gross_usage(run, identity, monkeypatch, args):
    client, _, _ = identity
    usage = {}

    def replace_read(root, _quality):
        frozen = json.loads((root / 'run.json').read_text())
        task = frozen['protocol']['workloads'][0]['tasks'][0]
        task.update(question='Read settings.py', host_intent={'direct_path': 'settings.py'})
        frozen['protocol']['target']['adapter'] = 'task'
        frozen['protocol_sha256'] = digest(frozen['protocol'])
        put(root / 'run.json', frozen)
        source = root / 'workspaces/colony/gateway/v1'
        server = server_for((client, source, []), monkeypatch, direct_path='settings.py')
        result = call(server, 'read', **args)
        path = root / 'logs/b.events.jsonl'
        usage['before'] = native_usage(path)['known_usage']
        def replace(events):
            item = next(e['item'] for e in events if e.get('item', {}).get('id') == 'observed-source')
            item.update(event('read', args, result)['item'])
        rewrite_events(root, 'b', replace)
        usage['after'] = native_usage(path)['known_usage']

    _, public, _ = build_reviewed_run(run, replace_read)
    assert public[0]['observed_current_evidence'] == public[1]['observed_current_evidence']
    assert public[0]['observed_current_evidence'][0]['content'] == 'TIMEOUT = 1500'
    assert usage['before'] == usage['after']


@pytest.mark.parametrize('args', [{'path': None}, {'start': '1'}, {'end': '2'}])
def test_publication_and_reuse_keep_normalized_read_references(trace, identity, monkeypatch, args):
    root, manifest, events, answer, _, evaluate = trace
    client, _, _ = identity
    source = root / 'workspaces/colony/w/v1'
    server_for((client, source, []), monkeypatch)  # Bind the signed fixture's team namespace.
    server = create_server(client.home, source, 'Remember a.py alpha beta', ['a.py'], 6,
                           direct_path='a.py', remember=True)
    monkeypatch.setattr(client, 'push', lambda *a, **k: {'stored': True, 'id': 'memory'})
    read = call(server, 'read', **args)
    pub_args = {'content': 'alpha is 1; beta is 2', 'applicability': 'scope',
                'evidence_refs': [read['evidence_ref']]}
    published = call(server, 'publish', **pub_args)
    manifest['track'] = 'natural'
    for task in manifest['workloads'][0]['tasks']:
        task.update(question='Remember a.py alpha beta', host_intent={'direct_path': 'a.py', 'remember': True})
    events['learn'] = [event('read', args, read), event('publish', pub_args, published, 2)]
    answer['content'] = 'scope\nalpha is 1; beta is 2'
    events['reuse'] = [event('research', {}, answer)]
    proof = evaluate()
    assert proof['learn']['publication'] and proof['learn']['research_attempts'] == 1
    assert proof['reuse']['useful_reuse']
    events['learn'][1]['item']['arguments'] = {**pub_args, 'evidence_refs': ['forged']}
    assert not evaluate()['learn']['publication']
