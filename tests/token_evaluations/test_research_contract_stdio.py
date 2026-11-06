"""Actual SDK/stdio clarity and allowance controls; no native or provider calls."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from scripts.token_evaluation.runtime import environment


def parameters(root, tmp_path):
    script = Path(__file__).resolve().parents[2] / 'scripts/token_evaluation/research.py'
    return StdioServerParameters(command=sys.executable,
        args=['-I', str(script), '--root', str(root), '--max-calls', '6',
              '--files', '["alpha.py","notes.txt"]'], env=environment(tmp_path))


def text_only(result):
    assert result.structuredContent is None
    assert 'structuredContent' not in result.model_dump(exclude_none=True)
    assert all(block.type == 'text' for block in result.content)
    return result


def test_actual_metadata_and_literal_content_contract(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    raw = 'VALUE = 77\r\nvalue = "café"\r\n'.encode()
    (root / 'alpha.py').write_bytes(raw)
    (root / 'notes.txt').write_bytes(b'VALUE = 99\n')

    async def control():
        async with stdio_client(parameters(root, tmp_path)) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert set(tools) == {'fixture_files', 'fixture_read', 'fixture_search'}
            assert all(tool.description and tool.outputSchema is None for tool in tools.values())
            assert 'known file directly' in tools['fixture_files'].description
            assert 'End-of-file is clamped' in tools['fixture_read'].description
            assert 'not regex, semantic or filename search' in tools['fixture_search'].description
            properties = tools['fixture_read'].inputSchema['properties']
            assert all(properties[name]['description'] for name in ['path', 'start', 'lines'])
            assert properties['start']['minimum'] == properties['start']['default'] == 1
            assert properties['lines']['minimum'] == 1
            assert properties['lines']['maximum'] == properties['lines']['default'] == 120
            query = tools['fixture_search'].inputSchema['properties']['query']
            assert query['minLength'] == 1 and 'literal' in query['description']

            source = text_only(await session.call_tool('fixture_read', {'path': 'alpha.py'}))
            assert not source.isError
            assert json.loads(source.content[0].text) == {
                'path': 'alpha.py', 'sha256': hashlib.sha256(raw).hexdigest(),
                'start': 1, 'end': 2, 'content': 'VALUE = 77\nvalue = "café"'}
            eof = text_only(await session.call_tool('fixture_read', {'path': 'alpha.py', 'start': 99}))
            assert not eof.isError
            assert json.loads(eof.content[0].text) == {
                'path': 'alpha.py', 'sha256': hashlib.sha256(raw).hexdigest(),
                'start': 99, 'end': 2, 'content': ''}
            upper = text_only(await session.call_tool('fixture_search', {'query': 'VALUE'}))
            assert {json.loads(b.text)['path']: json.loads(b.text) for b in upper.content} == {
                'alpha.py': {'path': 'alpha.py', 'sha256': hashlib.sha256(raw).hexdigest(),
                             'matches': ['1: VALUE = 77']},
                'notes.txt': {'path': 'notes.txt',
                              'sha256': hashlib.sha256(b'VALUE = 99\n').hexdigest(),
                              'matches': ['1: VALUE = 99']},
            }
            lower = text_only(await session.call_tool('fixture_search', {'query': 'value'}))
            assert len(lower.content) == 1
            assert json.loads(lower.content[0].text) == {
                'path': 'alpha.py', 'sha256': hashlib.sha256(raw).hexdigest(),
                'matches': ['2: value = "café"']}
            for query in ['VALUE|value', 'alpha.py']:
                empty = text_only(await session.call_tool('fixture_search', {'query': query}))
                assert not empty.isError and empty.content == []
    asyncio.run(asyncio.wait_for(control(), timeout=30))


@pytest.mark.parametrize('tool,arguments', [
    ('unknown_tool', {}),
    ('fixture_read', {'path': 'missing.py'}),
    ('fixture_read', {'path': 'AGENTS.md'}),
    ('fixture_read', {'path': 'alpha.py', 'lines': 200}),
    ('fixture_search', {'query': ''}),
])
def test_each_failed_attempt_charged_once_and_sixth_valid_call_still_available(tmp_path, tool, arguments):
    root = tmp_path / 'source'
    root.mkdir()
    raw = b'CURRENT = 42\n'
    (root / 'alpha.py').write_bytes(raw)
    (root / 'AGENTS.md').write_text('Private control data.', encoding='utf-8')

    async def control():
        async with stdio_client(parameters(root, tmp_path)) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            for _ in range(5):
                failed = text_only(await session.call_tool(tool, arguments))
                assert failed.isError
                assert 'Research call limit reached' not in failed.content[0].text
            sixth = text_only(await session.call_tool('fixture_read', {'path': 'alpha.py'}))
            assert not sixth.isError
            assert json.loads(sixth.content[0].text) == {
                'path': 'alpha.py', 'sha256': hashlib.sha256(raw).hexdigest(),
                'start': 1, 'end': 1, 'content': 'CURRENT = 42'}
            seventh = text_only(await session.call_tool('fixture_files', {}))
            assert seventh.isError and 'Research call limit reached' in seventh.content[0].text
    asyncio.run(asyncio.wait_for(control(), timeout=30))
