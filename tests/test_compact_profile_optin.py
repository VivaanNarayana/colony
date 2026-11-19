"""Untrusted server profile markers cannot expand an unnegotiated lookup."""

import copy

import pytest
import test_compact_delivery as delivery
import test_context_budget_transport as transport

from colony import git_client, git_context
from colony.compact import PROFILE
from colony.context_budget import count_response, fit_response
from colony.models import ContextBudget, Trail
from colony.validation import snapshot


@pytest.fixture
def caller(tmp_path):
    client, repo, _ = transport.caller.__wrapped__(tmp_path)
    text = 'PUBLIC = 1\n' + '# neutral padding\n' * 440 + 'FINAL_FACT = 77\n'
    (repo / 'guide.md').write_bytes(text.encode())
    source = snapshot(repo, 'guide.md')
    context = git_context.capture(repo, 'project')
    answer = {
        'hit': True, 'status': 'validated', 'match_kind': 'answer', 'content': 'PUBLIC is 1',
        'sources': [source.model_dump()],
        'git': {'origin': context.model_dump(), 'request': context.model_dump()},
        'evidence': [{'ref': 'guide.md', 'line': 1, 'text': 'PUBLIC = 1', 'complete_file': False}],
    }
    compact = {
        **answer, 'response_profile': PROFILE,
        'current_repository_reads': [{
            'path': 'guide.md', 'sha256': source.content_hash, 'start_line': 1,
            'content': text.rstrip('\n'), 'complete_file': True,
        }],
    }
    return client, repo, context, answer, compact


@pytest.mark.parametrize('mode', ['no_budget', 'budget_no_profile', 'not_advertised'])
@pytest.mark.parametrize('marker', [PROFILE, 'unknown/1', '', False, {}, []])
def test_public_client_rejects_every_unnegotiated_non_null_marker(caller, monkeypatch, mode, marker):
    client, repo, _, _, compact = caller
    assert len(compact['current_repository_reads'][0]['content'].encode()) == 7946
    compact['response_profile'] = marker
    capabilities = transport.capability()
    if mode != 'not_advertised':
        capabilities['context_budget']['profiles'] = [PROFILE]
    calls = []

    def request(route, payload):
        calls.append((route, copy.deepcopy(payload)))
        return copy.deepcopy(capabilities if route == '/capabilities' else compact)

    monkeypatch.setattr(client, 'request', request)
    monkeypatch.setattr(git_client, '_local_reads', lambda *a, **k: pytest.fail('unnegotiated reads'))
    options = {} if mode == 'no_budget' else {'context_budget': ContextBudget(max_tokens=2048)}
    if mode == 'not_advertised':
        options['response_profile'] = PROFILE
    result = client.ask('PUBLIC?', 'team', repo=repo, repository_id='project', **options)
    assert not result['hit'] and result['reason'] == 'context_budget_invalid_profile'
    assert 'current_repository_reads' not in result
    assert 'response_profile' not in calls[-1][1]
    assert 'include_evidence_files' not in calls[-1][1]
    if mode == 'no_budget':
        assert 'context_budget' not in calls[-1][1] and 'context_budget' not in result
    else:
        assert result['context_budget']['used'] == count_response(result, ContextBudget(max_tokens=2048)) <= 2048


@pytest.mark.parametrize('marker', ['absent', None])
@pytest.mark.parametrize('whole_files', [False, True])
def test_public_client_keeps_legacy_and_explicit_whole_file_behavior(caller, monkeypatch, marker, whole_files):
    client, repo, _, answer, _ = caller
    answer['evidence'][0]['complete_file'] = True
    if marker is None:
        answer['response_profile'] = None
    monkeypatch.setattr(client, 'request', lambda route, payload: copy.deepcopy(
        transport.capability() if route == '/capabilities' else answer))
    result = client.ask('PUBLIC?', 'team', repo=repo, repository_id='project',
                        include_evidence_files=whole_files)
    assert result['hit'] and result['match_kind'] == 'answer'
    read = result['evidence'][0]
    assert read['complete_file'] is whole_files
    assert len(read['text'].encode()) == (7946 if whole_files else len('PUBLIC = 1'))
    assert 'context_budget' not in result


@pytest.mark.parametrize('marker', [PROFILE, 'unknown/1', '', False, {}, []])
def test_direct_git_verifier_rejects_profiles_without_budget(caller, monkeypatch, marker):
    _, repo, context, _, compact = caller
    compact['response_profile'] = marker
    monkeypatch.setattr(git_client, '_local_reads', lambda *a, **k: pytest.fail('unbudgeted profile reads'))
    result = git_client.verify_lookup(compact, repo, context)
    assert result == {'hit': False, 'reason': 'git_local_evidence_not_verified'}


def test_direct_git_verifier_rejects_unknown_profile_even_with_budget(caller):
    _, repo, context, answer, _ = caller
    answer['response_profile'] = 'unknown/1'
    result = git_client.verify_lookup(answer, repo, context, context_budget=ContextBudget(max_tokens=2048))
    assert result == {'hit': False, 'reason': 'git_local_evidence_not_verified'}


@pytest.mark.parametrize('marker', ['absent', None, 'unknown/1', False])
def test_public_client_rejects_missing_or_mismatched_negotiated_profile(caller, monkeypatch, marker):
    client, _, _, answer, _ = caller
    capabilities = transport.capability()
    capabilities['context_budget']['profiles'] = [PROFILE]
    if marker != 'absent':
        answer['response_profile'] = marker
    monkeypatch.setattr(client, 'request', lambda route, payload: copy.deepcopy(
        capabilities if route == '/capabilities' else answer))
    result = client.ask('PUBLIC?', 'team', context_budget=ContextBudget(max_tokens=2048), response_profile=PROFILE)
    assert not result['hit'] and result['reason'] == 'context_budget_invalid_profile'
    assert result['response_profile'] == PROFILE
    assert result['context_budget']['used'] == count_response(result, ContextBudget(max_tokens=2048)) <= 2048


@pytest.fixture
def real_transport(tmp_path, monkeypatch):
    return delivery.transport.__wrapped__(tmp_path, monkeypatch)


def test_negotiated_compact_keeps_actual_transport_and_git_budget_checks(real_transport, monkeypatch):
    client, repo, _, responses = real_transport
    for index in range(3):
        (repo / f'part{index}.py').write_text(f'VALUE_{index} = {index}\n', encoding='utf-8')
    trail = Trail(id='a' * 32, namespace='team', question='Explain the configured values',
                  content='A concise saved finding.',
                  sources=[snapshot(repo, f'part{index}.py') for index in range(3)])
    assert client.push(trail, repo=repo, repository_id='project')['stored']
    budget = ContextBudget(max_tokens=4096)
    value = client.ask('Explain the configured values', 'team', repo=repo,
                       repository_id='project', context_budget=budget, response_profile=PROFILE)
    assert value['hit'] and value['response_profile'] == PROFILE
    assert len(value['current_repository_reads']) == 3
    assert all(read['complete_file'] for read in value['current_repository_reads'])
    assert value['context_budget']['used'] == count_response(value, budget) <= 4096
    assert value['sources'] == responses[-1]['sources']
    assert value['git']['compatibility'] == 'not_applicable'
    assert fit_response(value, budget) == value

    real = client.request

    def changed(route, payload):
        result = real(route, payload)
        if route == '/ask':
            (repo / 'part0.py').write_text('VALUE_0 = 999\n', encoding='utf-8')
        return result

    monkeypatch.setattr(client, 'request', changed)
    invalid = client.ask('Explain the configured values', 'team', repo=repo,
                         repository_id='project', context_budget=budget, response_profile=PROFILE)
    assert not invalid['hit'] and invalid['reason'] == 'git_local_evidence_not_verified'
    assert invalid['context_budget']['used'] == count_response(invalid, budget) <= 4096
