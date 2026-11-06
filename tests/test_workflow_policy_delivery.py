"""Installed configuration must deliver the same policy over actual MCP stdio.

This checks delivery and permissions, not a model's interpretation or compliance.
"""
import asyncio
import tomllib

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_workflow import identity as identity

from colony.models import ContextBudget
from colony.setup import setup_codex
from colony.workflow import (
    ADAPTIVE_READ_ONLY_WORKFLOW_GUIDANCE,
    ADAPTIVE_WORKFLOW_GUIDANCE,
    WORKFLOW_GUIDANCE,
)


@pytest.mark.parametrize('adaptive', [False, True])
@pytest.mark.parametrize('read_only', [False, True])
def test_installed_setup_and_stdio_initialization_share_policy(identity, adaptive, read_only):
    client, repo, _ = identity
    setup_codex(client.home, repo, 'install', namespace='team', read_only=read_only,
                context_budget=ContextBudget(max_tokens=1024) if adaptive else None)
    config = tomllib.loads((repo / '.codex/config.toml').read_text())['mcp_servers']
    assert set(config) == {'colony'}
    settings = config['colony']
    policy = (ADAPTIVE_READ_ONLY_WORKFLOW_GUIDANCE if read_only else ADAPTIVE_WORKFLOW_GUIDANCE
              ) if adaptive else WORKFLOW_GUIDANCE
    installed = (repo / 'AGENTS.md').read_text()
    assert installed.count(policy) == 1
    assert ('--context-budget' in settings['args']) is adaptive
    assert ('--workflow-read-only' in settings['args']) is read_only

    async def inspect():
        parameters = StdioServerParameters(command=settings['command'], args=settings['args'],
                                           cwd=settings['cwd'])
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            initialized = await session.initialize()
            assert initialized.instructions.count(policy) == 1
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert 'ask' in names
            assert ('push' in names) is not read_only
            if adaptive:
                assert names == ({'ask'} if read_only else {'ask', 'push'})
                assert WORKFLOW_GUIDANCE not in initialized.instructions
                assert 'Research gaps, contradictions, misses and stale/unverified' in policy
                assert 'Hashes validate files, not answer truth' in policy
                assert 'current_repository_reads and provenance' in policy
                assert 'Cite actual source coordinates' in policy
                if read_only:
                    assert 'do not call push or publish findings' in initialized.instructions
                    refused = await session.call_tool('push', {})
                    assert refused.isError
            else:
                assert ADAPTIVE_WORKFLOW_GUIDANCE not in initialized.instructions

    asyncio.run(asyncio.wait_for(inspect(), timeout=30))
