"""Actual local stdio MCP shapes/budget; no models or external network."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from scripts.token_evaluation.runtime import environment


def test_actual_research_mcp_blocks_validation_eof_and_hard_budget(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'a.py').write_bytes(b'one\ncommon\n')
    (root / 'b.py').write_bytes(b'common\n')
    (root / 'AGENTS.md').write_bytes(b'private instructions')
    script = Path(__file__).resolve().parents[2] / 'scripts/token_evaluation/research.py'
    parameters = StdioServerParameters(command=sys.executable,
        args=['-I', str(script), '--root', str(root), '--max-calls', '7', '--files', '["a.py","b.py"]'],
        env=environment(tmp_path))

    async def control():
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            listing = await session.list_tools()
            assert {t.name for t in listing.tools} == {'fixture_files', 'fixture_read', 'fixture_search'}
            files = await session.call_tool('fixture_files', {})
            assert files.content[0].text == 'a.py\nb.py'
            missing = await session.call_tool('fixture_search', {'query': 'absent'})
            assert missing.content == []
            multi = await session.call_tool('fixture_search', {'query': 'common'})
            assert {json.loads(block.text)['path'] for block in multi.content} == {'a.py', 'b.py'}
            one = await session.call_tool('fixture_search', {'query': 'one'})
            assert len(one.content) == 1
            eof = await session.call_tool('fixture_read', {'path': 'a.py', 'start': 99})
            assert json.loads(eof.content[0].text)['content'] == ''
            invalid = await session.call_tool('fixture_read', {'path': 'a.py', 'lines': True})
            assert invalid.isError
            private = await session.call_tool('fixture_read', {'path': 'AGENTS.md'})
            assert private.isError
            limited = await session.call_tool('fixture_files', {})
            assert limited.isError and 'limit' in limited.content[0].text.lower()
    asyncio.run(asyncio.wait_for(control(), timeout=30))
