"""Shared discovery contract on real MCP transports; no model sessions."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_workflow import identity as identity

from colony.models import Scope
from colony.security import issue
from scripts.token_evaluation.runtime import environment


def test_task_and_no_colony_discovery_have_identical_contract_and_accounting(identity, tmp_path):
    client, repo, _ = identity
    grant = client.keyfile.grant.model_copy(update={
        'scopes': [Scope(namespace='bench', pull=True, push=True)]})
    (client.home / 'identity.json').write_bytes(issue(
        tmp_path / 'admin/issuer.pem', grant).model_dump_json().encode())
    (repo / 'alpha.py').write_text('VALUE = 7\nvalue = 8\n', encoding='utf-8')
    (repo / 'notes.txt').write_text('VALUE = 9', encoding='utf-8')
    (repo / 'AGENTS.md').write_text('VALUE = private', encoding='utf-8')
    scripts = Path(__file__).resolve().parents[2] / 'scripts/token_evaluation'

    async def collect(task):
        args = (['-I', str(scripts / 'task_adapter.py'), '--home', str(client.home),
                 '--repo', str(repo), '--question', 'Explain the source'] if task else
                ['-I', str(scripts / 'research.py'), '--root', str(repo)])
        args += ['--max-calls', '6', '--files', '["alpha.py", "notes.txt"]']
        params = StdioServerParameters(command=sys.executable, args=args, env=environment(tmp_path))
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = {t.name: t.model_dump() for t in (await session.list_tools()).tools
                     if t.name in {'fixture_search', 'fixture_files'}}
            assert 'not regex, semantic or filename search' in tools['fixture_search']['description']
            assert 'known file directly' in tools['fixture_files']['description']
            query = tools['fixture_search']['inputSchema']['properties']['query']
            assert query['minLength'] == 1 and 'case-sensitive literal' in query['description']
            results = []
            # Empty queries fail but still consume one unit; arbitrary text is never
            # interpreted as regex, semantic search, or a private path to read.
            for index, query in enumerate(['', 'VALUE', 'value', 'VALUE|value', 'alpha.py']):
                result = await session.call_tool('fixture_search', {'query': query})
                assert result.isError == (index == 0)
                assert result.structuredContent is None
                blocks = [json.loads(b.text) for b in result.content]
                receipt = blocks[-1]['research_budget']
                assert receipt == {'admitted': 1, 'denied': 0,
                                   'succeeded': int(index != 0), 'remaining': 5-index}
                results.append(blocks)
            files = await session.call_tool('fixture_files', {})
            assert files.content[0].text == 'alpha.py\nnotes.txt'
            assert json.loads(files.content[-1].text)['research_budget']['remaining'] == 0
            denied = await session.call_tool('fixture_search', {'query': 'VALUE'})
            assert denied.isError
            denied_data = json.loads(denied.content[0].text)
            assert denied_data['research_budget'] == {
                'admitted': 0, 'denied': 1, 'succeeded': 0, 'remaining': 0}
            return tools, results

    async def check():
        plain, task = await collect(False), await collect(True)
        assert plain == task
        results = task[1]
        assert [b['path'] for b in results[1][:-1]] == ['alpha.py', 'notes.txt']
        assert results[2][0]['matches'] == ['2: value = 8']
        assert len(results[3]) == len(results[4]) == 1

    asyncio.run(asyncio.wait_for(check(), timeout=30))
