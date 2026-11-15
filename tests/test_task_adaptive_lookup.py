import asyncio
import copy
import json
import os
import sys

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from test_workflow import identity as identity
from test_integration import running_team as running_team
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from colony.mcp import create_mcp
from colony.models import ContextBudget
from colony.task_mcp import ANSWER_POLICY, TaskMCP, main
from colony.workflow import Workflow


def call(server, name, **args):
    return asyncio.run(server.call_tool(name, args))


@pytest.mark.parametrize('limit', [256, 3072, 8192])
@pytest.mark.parametrize('hit', [False, True])
def test_task_lookup_matches_ordinary_adaptive_wire_response(identity, monkeypatch, limit, hit):
    client, repo, sources = identity
    seen = []
    result = {'hit': False, 'reason': 'no_relevant_trail'} if not hit else {
        'hit': True, 'match_kind': 'answer', 'status': 'validated',
        'content': 'TIMEOUT defaults to 30. ' * 100,
        'sources': [sources[0].model_dump()],
        'evidence': [{'ref': 'settings.py', 'line': 1, 'text': 'TIMEOUT = 30', 'complete_file': True}],
    }
    def ask(*args, **kwargs):
        seen.append((args, kwargs))
        return copy.deepcopy(result)
    monkeypatch.setattr(client, 'ask', ask)
    monkeypatch.setattr('colony.mcp.Client', lambda home: client)
    budget = ContextBudget(max_tokens=3072, encoding='utf8_bytes')
    ordinary = create_mcp(client.home, workflow_namespace='team', workflow_repo=repo, context_budget=budget)
    task = TaskMCP(Workflow(client, 'team', repo), 'Explain timeout', context_budget=budget)
    actual = call(task, 'research', max_context_tokens=limit)
    expected = call(ordinary, 'ask', question='Explain timeout', max_context_tokens=limit)
    assert actual == expected
    assert len(actual[0].text.encode('utf-8')) <= limit
    assert len(seen) == 2  # Exactly one request for each explicit call; no automatic retry.
    assert seen[0] == seen[1]
    assert seen[0][1]['context_budget'].max_tokens == limit
    assert seen[0][1]['response_profile']
    assert {t.name for t in asyncio.run(task.list_tools())} == {'read', 'research'}
    assert ANSWER_POLICY in task.instructions
    assert json.loads(call(task, 'read', path='settings.py')[0].text)['evidence_ref']
    assert 'publish' in {t.name for t in asyncio.run(task.list_tools())}


def test_direct_adaptive_read_is_lookup_free_and_failure_does_not_block_reads(identity, monkeypatch):
    client, repo, _ = identity
    seen = []
    def unavailable(*a, **k):
        seen.append(k)
        raise RuntimeError('private failure')
    monkeypatch.setattr(client, 'ask', unavailable)
    monkeypatch.setattr('colony.mcp.Client', lambda home: client)
    server = TaskMCP(Workflow(client, 'team', repo), 'Read settings.py', direct_path='settings.py',
                     context_budget=ContextBudget(max_tokens=3072))
    assert json.loads(call(server, 'read')[0].text)['content'] == 'TIMEOUT = 30'
    assert not seen
    failed = json.loads(call(server, 'research')[0].text)
    assert failed == {'hit': False, 'reason': 'lookup_unavailable', 'continue_research': True}
    assert len(seen) == 1
    assert json.loads(call(server, 'read', path='README.md')[0].text)['content']
    assert {t.name for t in asyncio.run(server.list_tools())} == {'read', 'research'}


@pytest.mark.parametrize('value', [True, '512', 1, 8193, 1.5])
def test_invalid_override_fails_before_lookup_and_leaves_tools_available(identity, monkeypatch, value):
    client, repo, _ = identity
    monkeypatch.setattr(client, 'ask', lambda *a, **k: pytest.fail('lookup invoked'))
    monkeypatch.setattr('colony.mcp.Client', lambda home: client)
    server = TaskMCP(Workflow(client, 'team', repo), 'q', context_budget=ContextBudget(max_tokens=3072))
    with pytest.raises(ToolError):
        call(server, 'research', max_context_tokens=value)
    assert json.loads(call(server, 'read', path='settings.py')[0].text)['content']


def test_cli_forwards_budget_and_legacy_has_no_new_parameter(identity, monkeypatch):
    client, repo, _ = identity
    seen = []
    monkeypatch.setattr(TaskMCP, 'run', lambda self, **k: seen.append(self))
    main(['--home', str(client.home), '--repo', str(repo), '--ns', 'team', '--question', 'q',
          '--context-budget', '512', '--context-encoding', 'utf8_bytes'])
    tool = next(t for t in asyncio.run(seen[0].list_tools()) if t.name == 'research')
    assert 'default 512 UTF-8 bytes' in tool.description
    legacy = TaskMCP(Workflow(client, 'team', repo), 'q')
    tool = next(t for t in asyncio.run(legacy.list_tools()) if t.name == 'research')
    assert not tool.inputSchema['properties']
    with pytest.raises(ValueError, match='requires a context budget'):
        main(['--home', str(client.home), '--repo', str(repo), '--ns', 'team', '--question', 'q',
              '--context-encoding', 'utf8_bytes'])


def test_real_adaptive_task_transport_publication_and_evidence_reuse(running_team):
    _, homes, repo, _ = running_team

    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=[
            '-m', 'colony.task_mcp', '--home', str(homes['a']), '--repo', str(repo),
            '--ns', 'team', '--question', 'How does the timeout default work?',
            '--context-budget', '3072', '--context-encoding', 'utf8_bytes'], env=dict(os.environ))
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            observed = await session.call_tool('read', {'path': 'settings.py'})
            reference = json.loads(observed.content[0].text)['evidence_ref']
            published = await session.call_tool('publish', {'content': 'The timeout defaults to 30.',
                'applicability': 'settings.py', 'evidence_refs': [reference]})
            assert json.loads(published.content[0].text)['stored']
            response = await session.call_tool('research', {})
            assert not response.isError and len(response.content[0].text.encode('utf-8')) <= 3072
            value = json.loads(response.content[0].text)
            assert value['hit'] and value['status'] == 'validated'
            assert value['current_repository_reads'][0]['content'] == 'TIMEOUT = 30'
            assert 'publish' not in {t.name for t in (await session.list_tools()).tools}
            # Publication completion never removes research or limits answer content.
            assert not (await session.call_tool('read', {'path': 'settings.py'})).isError

    asyncio.run(asyncio.wait_for(exercise(), 30))
