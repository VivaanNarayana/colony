"""Requested status stays answerable; optional storage never gates task completion."""
import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from test_workflow import identity as identity

from colony import mcp
from colony.models import ContextBudget
from colony.setup import setup_codex


@pytest.mark.parametrize('failure', ['invalid_evidence', 'unknown_outcome'])
@pytest.mark.parametrize('bound', [False, True])
def test_requested_status_remains_allowed_after_publication_failure(identity, monkeypatch, failure, bound):
    client, repo, sources = identity
    attempts, lookups = [], []
    question = 'Remember the timeout; explain whether it was saved and why if not.'

    def push(self, trail, **kwargs):
        attempts.append(trail)
        raise RuntimeError('Unknown outcome')

    def ask(self, question, namespace, **kwargs):
        lookups.append(question)
        return {'hit': False, 'reason': 'no_relevant_trail'}

    monkeypatch.setattr(mcp.Client, 'push', push)
    monkeypatch.setattr(mcp.Client, 'ask', ask)
    budget = ContextBudget()
    options = {'workflow_namespace': 'team', 'workflow_repo': repo} if bound else {}
    server = mcp.create_mcp(client.home, context_budget=budget, **options)
    setup_codex(client.home, repo, 'install', namespace='team', context_budget=budget)
    for guidance in (server.instructions, (repo / 'AGENTS.md').read_text()):
        assert 'Explain publication status when asked' in guidance
        assert 'omit unsolicited abstention reports' in guidance
        assert 'No abstention report,' not in guidance
        assert 'No decision call or automatic retry.' in guidance
        assert 'Publication limits never restrict answer detail or necessary research.' in guidance
        assert 'publication/telemetry failure must not block the answer.' in guidance
        assert 'Mutation timeout: unknown outcome' in guidance
    assert not attempts and not lookups
    evidence = [{'ref': s.ref, 'content_hash': s.content_hash} for s in sources]
    if failure == 'invalid_evidence':
        evidence[-1]['content_hash'] = 'private-invalid-hash'
    with pytest.raises(ToolError) as rejected:
        asyncio.run(server.call_tool('push', {'namespace': 'team', 'question': question,
            'content': 'In this repository, settings define the timeout.', 'sources': evidence}))
    if failure == 'invalid_evidence':
        assert 'no publication attempted' in str(rejected.value)
        assert 'private-invalid-hash' not in str(rejected.value)
    assert len(attempts) == int(failure == 'unknown_outcome')
    # A subsequent explicit lookup remains available, without an implicit push retry.
    assert asyncio.run(server.call_tool('ask', {'question': 'Research the remaining question',
                                               'namespace': 'team'}))
    assert lookups == ['Research the remaining question']
    assert len(attempts) == int(failure == 'unknown_outcome')
