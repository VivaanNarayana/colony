"""Run against a batch-capable installed target as well as the legacy target; no models."""
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError

from colony.client import Client
from colony.models import Grant, Scope
from colony.security import generate_keys, issue, private_write
from scripts.token_evaluation.task_adapter import create_server
from scripts.token_evaluation.task_evidence import verified_reads


def call(server, name, **args):
    return json.loads(asyncio.run(server.call_tool(name, args))[0].text)


def event(tool, args, value):
    return {'item': {'tool': tool, 'arguments': args}}


@pytest.fixture
def identity(tmp_path):
    # Independent fixture: it must also import against non-adaptive main.
    admin, home, repo = (tmp_path / name for name in ('admin', 'home', 'repo'))
    generate_keys(admin)
    grant = Grant(member_id='batch-test', name='Batch test', server='http://127.0.0.1:7331',
                  expires_at=int(time.time()) + 3600,
                  scopes=[Scope(namespace='bench', pull=True, push=True)], capture='none')
    private_write(home / 'identity.json', issue(admin / 'issuer.pem', grant).model_dump_json().encode())
    private_write(home / 'issuer.pub', (admin / 'issuer.pub').read_bytes())
    private_write(home / 'queue.key', Fernet.generate_key())
    repo.mkdir()
    for path in ('settings.py', 'README.md'):
        (repo / path).write_bytes(b'TIMEOUT = 30\n')
    return Client(home), repo, []


@pytest.fixture
def batch_server(identity, monkeypatch):
    servers = []
    client, repo, _ = identity
    monkeypatch.setattr('scripts.token_evaluation.task_adapter.Client', lambda home: client)
    def create(**kwargs):
        server = create_server(client.home, repo, 'Explain settings behavior',
                               ['settings.py', 'README.md'], kwargs.pop('max_calls', 6), **kwargs)
        servers.append(server)
        read = next(t for t in asyncio.run(server.list_tools()) if t.name == 'read')
        if 'batch' not in read.inputSchema['properties']:
            pytest.skip('Run this integration suite again with the merged batch-capable target on PYTHONPATH')
        return server
    yield create
    for server in servers:
        server.evidence.close()


@pytest.mark.parametrize("question", ["Explain settings", "Does this repo contain the measurement?"])
def test_lookup_boundary_independent_of_containment_capability(identity, monkeypatch, question):
    import inspect

    from scripts.token_evaluation import task_adapter

    if "read_path_allowed" not in inspect.signature(task_adapter.TaskMCP).parameters:
        pytest.skip("Historical target has no lookup path policy")
    monkeypatch.delattr(task_adapter.task_mcp, "containment_question", raising=False)
    client, repo, _ = identity
    server = create_server(client.home, repo, question, ["settings.py"], 6)
    try:
        assert server.evidence.path_allowed("settings.py")
        assert not server.evidence.path_allowed("README.md")
    finally:
        server.evidence.close()


def test_containment_uses_disclosed_inventory_and_real_shared_budget(identity):
    client, repo, _ = identity
    paths = ["README.md", "adapter.py", "client.py", "handler.py", "settings.py"]
    for path in paths:
        (repo / path).write_text("local fixture")
    server = create_server(client.home, repo, "Does this repo contain the measurement?", paths, 6,
                           read_only=True)
    if not hasattr(server, "_scope"):
        pytest.skip("Historical target has no source scope planner")
    try:
        listed = asyncio.run(server.call_tool("fixture_files", {})).content
        assert listed[0].text.splitlines() == paths
        assert json.loads(listed[-1].text)["research_budget"]["remaining"] == 5
        action = json.loads(listed[1].text)["scope_read_plan"]
        response = asyncio.run(server.call_tool(action["next_tool"], action["arguments"]))
        assert json.loads(response[0].text)["listed_scope"] == {"status": "complete", "listed_files": 5}
        assert json.loads(response[-1].text)["research_budget"] == {
            "admitted": 5, "denied": 0, "succeeded": 5, "remaining": 0,
        }
        with pytest.raises(ValueError, match="Research call limit"):
            asyncio.run(server.call_tool("fixture_search", {"query": "measurement"}))
    finally:
        server.evidence.close()


@pytest.mark.parametrize('serialized', [False, True])
def test_real_batch_shares_quota_and_preserves_per_entry_errors(batch_server, identity, serialized):
    _, repo, _ = identity
    (repo / 'unlisted.py').write_text('SECRET')
    server = batch_server(max_calls=4)
    batch = [{'path': 'settings.py'}, {'path': 'unlisted.py'}, {'path': 'README.md'}]
    args = {'batch': json.dumps(batch) if serialized else batch}
    result = call(server, 'read', **args)
    assert result['results'][0]['content'] == 'TIMEOUT = 30'
    assert result['results'][0]['evidence_ref'] and result['results'][2]['evidence_ref']
    assert result['results'][1]['evidence_ref'] is None
    assert 'SECRET' not in str(result)
    asyncio.run(server.call_tool('fixture_files', {}))
    with pytest.raises(ValueError, match='Research call limit'):
        call(server, 'fixture_search', query='TIMEOUT')
    assert 'publish' in {t.name for t in asyncio.run(server.list_tools())}


def test_over_limit_batch_returns_admitted_prefix_only(batch_server, monkeypatch):
    server = batch_server(max_calls=2)
    original, seen = server.evidence.read, []
    def observed(*args, **kwargs):
        seen.append(args[0] if args else kwargs['path'])
        return original(*args, **kwargs)
    monkeypatch.setattr(server.evidence, 'read', observed)
    result = call(server, 'read', batch=[{'path': 'settings.py'}] * 3)['results']
    assert seen == ['settings.py'] * 2
    assert all(r['evidence_ref'] for r in result[:2])
    assert result[2] == {'error': 'Research call limit reached', 'evidence_ref': None}
    assert 'publish' in {t.name for t in asyncio.run(server.list_tools())}
    with pytest.raises(ValueError, match='Research call limit'):
        asyncio.run(server.call_tool('fixture_files', {}))


@pytest.mark.parametrize('batch', [[{'path': 'settings.py', 'offset': '0'}, {'path': 'settings.py'}],
                                  [None, {}], [{'path': 'settings.py'}] * 9])
def test_invalid_batch_charged_before_validation(batch_server, batch):
    server = batch_server(max_calls=len(batch))
    with pytest.raises(ToolError):
        call(server, 'read', batch=batch)
    assert 'publish' not in {t.name for t in asyncio.run(server.list_tools())}
    with pytest.raises(ValueError, match='Research call limit'):
        call(server, 'read', path='settings.py')


def test_real_batch_continuation_evidence_and_publication(batch_server, identity, monkeypatch):
    client, repo, _ = identity
    text = 'first\n' + 'é' * 9000 + '\npassword="private"\nlast'
    (repo / 'settings.py').write_bytes(text.encode())
    files = {p: hashlib.sha256((repo / p).read_bytes()).hexdigest() for p in ['settings.py', 'README.md']}
    server = batch_server()
    args = {'batch': [{'path': 'settings.py'}, {'path': 'README.md'}]}
    first = call(server, 'read', **args)
    pages = {}
    verified = verified_reads(event('read', args, first)['item'], first, repo, files, {}, pages=pages)
    assert all(source for _, source, _ in verified)
    assert verified[0][0] == [{'path': 'settings.py', 'start': 1, 'end': 1}]
    next_args = {'path': 'settings.py', 'offset': first['results'][0]['next_offset']}
    second = call(server, 'read', **next_args)
    verified = verified_reads(event('read', next_args, second)['item'], second, repo, files, {}, pages=pages)
    assert {s['start'] for s in verified[0][0]} == {1, 2, 4}
    assert 'private' not in str(first) + str(second)
    pushed = []
    monkeypatch.setattr(client, 'push', lambda *a, **k: pushed.append((a, k)) or {'stored': True})
    result = call(server, 'publish', content='Supported behavior', applicability='settings',
                  evidence_refs=[first['results'][0]['evidence_ref'], second['evidence_ref']])
    assert result['stored'] and len(pushed) == 1
    assert not call(server, 'publish', content='again', applicability='settings',
                    evidence_refs=[second['evidence_ref']])['stored']


@pytest.mark.parametrize('options', [{'read_only': True}, {'direct_path': 'settings.py'}])
def test_batch_does_not_promote_publication_or_restrict_source_access(batch_server, options):
    server = batch_server(**options)
    result = call(server, 'read', batch=[{'path': 'settings.py'}, {'path': 'README.md'}])
    assert all(r['evidence_ref'] for r in result['results'])
    assert 'publish' not in {t.name for t in asyncio.run(server.list_tools())}


def test_real_stdio_batch_and_shared_limit(batch_server, identity):
    batch_server()  # Check this installed target's capability before launching the local peer.
    client, repo, _ = identity
    script = Path(__file__).resolve().parents[2] / 'scripts/token_evaluation/task_adapter.py'
    async def check():
        params = StdioServerParameters(command=sys.executable, args=[str(script),
            '--home', str(client.home), '--repo', str(repo), '--question', 'Explain settings',
            '--files', json.dumps(['settings.py', 'README.md']), '--max-calls', '2'], env=dict(os.environ))
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.call_tool('read', {'batch': [{'path': 'settings.py'}, {'path': 'README.md'},
                                                               {'path': 'settings.py'}]})
            assert not result.isError
            values = json.loads(result.content[0].text)['results']
            assert all(r['evidence_ref'] for r in values[:2])
            assert values[2] == {'error': 'Research call limit reached', 'evidence_ref': None}
            assert json.loads(result.content[-1].text)['research_budget'] == {
                'admitted': 2, 'denied': 1, 'succeeded': 2, 'remaining': 0}
            assert 'publish' in {t.name for t in (await session.list_tools()).tools}
            denied = await session.call_tool('fixture_files', {})
            assert denied.isError and 'Research call limit' in denied.content[0].text
            assert json.loads(denied.content[0].text)['research_budget']['denied'] == 1
    asyncio.run(asyncio.wait_for(check(), 30))
