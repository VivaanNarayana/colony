"""Actual research results and synthetic reports; no model sessions or token measurements."""
import copy
import hashlib
import json
import os
import subprocess

import pytest

from scripts.token_evaluation.evidence import _research, verify_current_reads
from scripts.token_evaluation.research import Research
from tests.token_evaluations.test_agent_quality import build_reviewed_run, score
from tests.token_evaluations.test_report import rewrite_events
from tests.token_evaluations.test_report import run as run

ALIASES = ['pkg/settings.py', './pkg/settings.py', '././pkg//settings.py', 'pkg/../pkg/settings.py']
if os.name == 'nt':
    ALIASES += [r'pkg\settings.py', r'.\pkg\settings.py']


def item(path, result, *, start=1, lines=1):
    return {'type': 'mcp_tool_call', 'id': 'read', 'server': 'repository', 'tool': 'fixture_read',
            'status': 'completed', 'arguments': {'path': path, 'start': start, 'lines': lines},
            'result': {'content': [{'type': 'text', 'text': json.dumps(result)}]}}


@pytest.fixture
def sources(tmp_path):
    root = tmp_path / 'snapshot'
    (root / 'pkg').mkdir(parents=True)
    raw = b'TIMEOUT = 1500\nOTHER = 2\n'
    (root / 'pkg/settings.py').write_bytes(raw)
    return root, {'pkg/settings.py': hashlib.sha256(raw).hexdigest()}


@pytest.mark.parametrize('alias', ALIASES)
def test_actual_read_aliases_have_identical_verified_spans(sources, alias):
    root, files = sources
    research = Research(root, files=files)
    result = json.loads(research.read(alias, lines=1)[0].text)
    assert result['path'] == alias  # retain the tool's actual response contract
    call = item(alias, result)
    before = copy.deepcopy(call)
    expected = [{'path': 'pkg/settings.py', 'start': 1, 'end': 1}]
    assert _research(call, root, files) == expected
    assert _research({**call, 'arguments': json.dumps(call['arguments'])}, root, files) == expected
    assert call == before and research.calls == 1
    # The cache verifier still accepts the same canonical source, without alias exemptions.
    assert verify_current_reads([{**result, 'path': 'pkg/settings.py'}], root, files) == expected


@pytest.mark.parametrize('mutation', ['hash', 'content', 'start', 'end', 'request_path', 'absolute',
                                     'outside', 'unlisted', 'missing', 'failed', 'tool_error'])
def test_alias_normalization_does_not_waive_strict_checks(sources, mutation):
    root, files = sources
    research = Research(root, files=files)
    result = json.loads(research.read('./pkg/settings.py', lines=1)[0].text)
    call = item('./pkg/settings.py', result)
    if mutation == 'hash':
        result['sha256'] = '0' * 64
    elif mutation == 'content':
        result['content'] = 'invented'
    elif mutation == 'start':
        result['start'] = 2
    elif mutation == 'end':
        result['end'] = 2
    elif mutation == 'request_path':
        call['arguments']['path'] = 'pkg/settings.py'  # equivalent identity cannot waive request binding
    elif mutation in {'absolute', 'outside', 'unlisted', 'missing'}:
        raw = (root / 'pkg/settings.py').read_bytes()
        (root.parent / 'outside.py').write_bytes(raw)
        (root / 'unlisted.py').write_bytes(raw)
        path = {'absolute': str(root / 'pkg/settings.py'), 'outside': '../outside.py',
                'unlisted': './unlisted.py', 'missing': './missing.py'}[mutation]
        result['path'] = call['arguments']['path'] = path
    elif mutation == 'failed':
        call['status'] = 'failed'
    else:
        call['result']['isError'] = True
    call['result']['content'][0]['text'] = json.dumps(result)
    assert _research(call, root, files) == []


def test_alias_verification_rejects_changed_file_and_retains_cache_path_strictness(sources):
    root, files = sources
    result = json.loads(Research(root, files=files).read('./pkg/settings.py', lines=1)[0].text)
    assert verify_current_reads([result], root, files) == []  # cache protocol still requires canonical keys
    (root / 'pkg/settings.py').write_text('TIMEOUT = 9000\n', encoding='utf-8')
    assert _research(item('./pkg/settings.py', result), root, files) == []


def test_one_invalid_block_rejects_all_alias_evidence(sources):
    root, files = sources
    result = json.loads(Research(root, files=files).read('./pkg/settings.py', lines=1)[0].text)
    call = item('./pkg/settings.py', result)
    call['result']['content'].append({'type': 'text', 'text': json.dumps({**result, 'sha256': 'wrong'})})
    assert _research(call, root, files) == []


def make_directory_link(link, target):
    if os.name == 'nt':
        # Both paths are known test directories. No destructive shell operation.
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], check=True,
                       capture_output=True, timeout=10)
        assert link.is_junction()
    else:
        link.symlink_to(target, target_is_directory=True)
        assert link.is_symlink()


@pytest.mark.parametrize('route', ['directory', 'cancelled_parent', 'root'])
def test_links_are_rejected_before_alias_canonicalization(sources, route):
    root, files = sources
    result = json.loads(Research(root, files=files).read('pkg/settings.py', lines=1)[0].text)
    if route == 'root':
        linked = root.parent / 'root-link'
        make_directory_link(linked, root)
        result['path'] = './pkg/settings.py'
        assert _research(item(result['path'], result), linked, files) == []
    else:
        make_directory_link(root / 'alias', root / 'pkg')
        result['path'] = 'alias/settings.py' if route == 'directory' else 'alias/../pkg/settings.py'
        assert _research(item(result['path'], result), root, files) == []


@pytest.mark.parametrize('identity', ['a', 'b'])
@pytest.mark.parametrize('alias', ['./settings.py', '././settings.py', 'nested/../settings.py']
                         + ([r'.\settings.py'] if os.name == 'nt' else []))
def test_review_and_report_treat_alias_reads_equally_in_either_arm(run, identity, alias):
    def use_alias(root, _quality):
        arm = 'no_colony' if identity == 'a' else 'colony'
        source_root = root / 'workspaces' / arm / 'gateway/v1'
        (source_root / 'nested').mkdir()
        # review_subject validates both arms against the frozen canonical source root.
        canonical_root = root / 'workspaces/no_colony/gateway/v1'
        (canonical_root / 'nested').mkdir(exist_ok=True)
        result = json.loads(Research(source_root, files=['settings.py']).read(alias, lines=1)[0].text)
        def replace(events):
            target = next(e['item'] for e in events if e.get('item', {}).get('id') == 'observed-source')
            target.update(item(alias, result))
        rewrite_events(root, identity, replace)
    root, public, submission = build_reviewed_run(run, use_alias)
    assert public[0]['observed_current_evidence'] == public[1]['observed_current_evidence']
    assert public[0]['observed_current_evidence']
    value = score(root, submission)
    assert all(row['quality_state'] == 'passed' and row['answer_quality_successful'] for row in value['attempts'])
    assert value['tool_policy_compliant'] and value['quality_complete']
    assert value['arms']['colony']['total_usage']['gross_tokens'] == 100
    assert value['arms']['no_colony']['total_usage']['gross_tokens'] == 120
    # When both arms use repository reads, absence of Colony adoption must still block a reduction claim.
    if identity == 'b':
        assert not value['adoption'] and value['observed_token_reduction'] is None


def test_missing_alias_component_is_not_normalized_lexically(sources):
    root, files = sources
    result = json.loads(Research(root, files=files).read('pkg/settings.py', lines=1)[0].text)
    result['path'] = 'absent/../pkg/settings.py'
    # Only spellings that the actual tool can resolve should earn evidence.
    try:
        Research(root, files=files).read(result['path'], lines=1)
    except (OSError, ValueError):
        assert _research(item(result['path'], result), root, files) == []
    else:
        # Some native path resolvers collapse this spelling; mirror the tool exactly.
        assert _research(item(result['path'], result), root, files) == [{'path': 'pkg/settings.py', 'start': 1, 'end': 1}]
