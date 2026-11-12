"""Rules delivery and safe managed-file upgrade; these do not test model adherence."""
import hashlib
import re

from test_setup import project as project
from test_workflow_policy_envelopes import EVALUATED_C0481B2_POLICY, _agents_with_policy

from colony.models import ContextBudget
from colony.setup import setup_codex
from colony.workflow import ADAPTIVE_PUBLICATION_RULES, ADAPTIVE_WORKFLOW_GUIDANCE


def old_owned_block():
    """Reconstruct the prior signed managed block without importing current policy."""
    raw = _agents_with_policy(EVALUATED_C0481B2_POLICY, False)
    match = re.fullmatch(rb'<!-- colony:setup:v1 begin sha256=([a-f0-9]{64}) pad=0 created=1 -->\n(.*)<!-- colony:setup:end -->\n', raw, re.DOTALL)
    body = match[2].replace(b'publish new evidence-backed findings.', b'selective publication.')
    signature = hashlib.sha256(b'v1:1' + body).hexdigest().encode()
    return b'<!-- colony:setup:v1 begin sha256=' + signature + b' pad=0 created=1 -->\n' + body + b'<!-- colony:setup:end -->\n'


def test_previous_adaptive_block_upgrades_rules_and_preserves_user_text(project):
    repo, home = project
    prefix = b'# Project rules\r\nKeep credentials private.\r\n\r\n'
    suffix = b'\r\n# Team notes\r\nKeep reviewed changes scoped.\r\n'
    agents = repo / 'AGENTS.md'
    agents.write_bytes(prefix + old_owned_block() + suffix)
    changed = setup_codex(home, repo, 'install', namespace='team', context_budget=ContextBudget())
    assert changed['changed']
    raw = agents.read_bytes()
    assert raw.startswith(prefix) and raw.endswith(suffix)
    normalized = raw.replace(b'\r\n', b'\n')
    assert ADAPTIVE_WORKFLOW_GUIDANCE.encode() in normalized
    assert normalized.count(ADAPTIVE_PUBLICATION_RULES.encode()) == 1
    assert b'selective publication' not in raw
    assert b'Publication rules before final: Only push' in normalized
    assert not setup_codex(home, repo, 'install', namespace='team', context_budget=ContextBudget())['changed']
    assert agents.read_bytes() == raw
    setup_codex(home, repo, 'uninstall')
    assert agents.read_bytes() == prefix + suffix


def test_switching_to_read_only_removes_publication_rules(project):
    repo, home = project
    setup_codex(home, repo, 'install', namespace='team', context_budget=ContextBudget())
    assert ADAPTIVE_PUBLICATION_RULES in (repo / 'AGENTS.md').read_text(encoding='utf-8')
    setup_codex(home, repo, 'install', namespace='team', read_only=True, context_budget=ContextBudget())
    text = (repo / 'AGENTS.md').read_text(encoding='utf-8')
    assert ADAPTIVE_PUBLICATION_RULES not in text
    assert 'do not call push or publish findings' in text
