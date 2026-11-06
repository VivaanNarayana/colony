"""Synthetic trace counterproofs; native product traces are tested separately."""
import copy
import hashlib
import json

import pytest

from scripts.token_evaluation.evidence import evaluate_mechanisms, verify_current_reads


def call(server, tool, args, value, failed=False):
    return {'type': 'item.completed', 'item': {'id': str(id(value)), 'type': 'mcp_tool_call',
        'server': server, 'tool': tool, 'arguments': args,
        'status': 'failed' if failed else 'completed',
        'result': {'content': [{'type': 'text', 'text': json.dumps(value)}]}}}


@pytest.fixture
def trace(tmp_path):
    text = 'alpha = 1\nbeta = 2\n'
    sha = hashlib.sha256(text.encode()).hexdigest()
    files = {'a.py': sha}
    base = tmp_path / 'workspaces/colony/w/v1'
    base.mkdir(parents=True)
    (base / 'a.py').write_bytes(text.encode())
    citations = [{'path': 'a.py', 'start': 1, 'end': 2}]
    tasks = [{'id': name, 'snapshot': 'v1', 'oracle': {'citations': citations}} for name in ('learn', 'reuse', 'gap', 'stale')]
    manifest = {'workloads': [{'id': 'w', 'snapshots': [{'id': 'v1', 'files': files}], 'tasks': tasks}]}
    sources = [{'ref': 'a.py', 'content_hash': sha}]
    read = {'path': 'a.py', 'sha256': sha, 'start': 1, 'end': 2, 'content': text.rstrip('\n')}
    args = {'question': 'alpha beta', 'content': 'alpha is 1; beta is 2', 'sources': sources}
    learn = [call('repository', 'fixture_read', {'path': 'a.py'}, read),
             call('colony', 'push', args, {'stored': True, 'id': 'memory'})]
    answer = {'hit': True, 'status': 'validated', 'id': 'memory', 'sources': sources,
              'content': args['content'], 'current_repository_reads': [{**read, 'start_line': 1}]}
    events = {'learn': learn, 'reuse': [call('colony', 'ask', {'question': 'alpha beta'}, answer)]}
    def evaluate():
        rows = []
        for name, values in events.items():
            path = tmp_path / f'{name}.jsonl'
            path.write_text('\n'.join(map(json.dumps, values)), encoding='utf-8')
            rows.append({'id': name, 'task': name, 'arm': 'colony', 'workload': 'w', 'events_path': str(path)})
        return evaluate_mechanisms(tmp_path, manifest, rows)
    return tmp_path, manifest, events, answer, read, evaluate


def test_chronological_publication_and_hit(trace):
    *_, evaluate = trace
    proof = evaluate()
    assert proof['learn']['publication']
    assert proof['reuse']['hit'] and proof['reuse']['useful_reuse']


@pytest.mark.parametrize('mutation', ['wrong_id', 'bogus_read', 'wrong_content', 'wrong_source', 'failed_research', 'research_after_push'])
def test_unbound_or_forged_evidence_fails(trace, mutation):
    _, _, events, answer, _, evaluate = trace
    if mutation == 'wrong_id':
        answer['id'] = 'other'
    elif mutation == 'bogus_read':
        answer['current_repository_reads'][0]['content'] = 'made up'
    elif mutation == 'wrong_content':
        answer['content'] = 'different saved answer'
    elif mutation == 'wrong_source':
        answer['sources'] = [{'ref': 'a.py', 'content_hash': '0' * 64}]
    elif mutation == 'failed_research':
        events['learn'][0]['item']['status'] = 'failed'
    else:
        events['learn'].reverse()
    if mutation in {'wrong_id', 'bogus_read', 'wrong_content', 'wrong_source'}:
        events['reuse'][0] = call('colony', 'ask', {}, answer)
    assert not evaluate()['reuse']['hit']


def test_partial_then_successful_gap_research(trace):
    _, _, events, answer, read, evaluate = trace
    partial = copy.deepcopy(answer)
    partial['current_repository_reads'][0].update(content='alpha = 1', end=1)
    events['gap'] = [call('colony', 'ask', {}, partial),
        call('repository', 'fixture_read', {'path': 'a.py', 'start': 2}, {**read, 'start': 2, 'content': 'beta = 2'})]
    assert evaluate()['gap']['insufficient']
    events['gap'][1]['item']['status'] = 'failed'
    assert not evaluate()['gap']['insufficient']


def test_generic_miss_cannot_prove_partial_or_stale(trace):
    _, _, events, _, read, evaluate = trace
    events['gap'] = [call('colony', 'ask', {}, {'hit': False, 'reason': 'no_relevant_trail'}),
        call('repository', 'fixture_read', {'path': 'a.py'}, read)]
    proof = evaluate()['gap']
    assert proof['miss'] and not proof['insufficient'] and not proof['stale']


def test_stale_requires_same_previously_retrieved_identity_and_fallback(trace):
    root, manifest, events, answer, read, evaluate = trace
    new_text = 'alpha = 3\nbeta = 2\n'
    sha = hashlib.sha256(new_text.encode()).hexdigest()
    base = root / 'workspaces/colony/w/v2'
    base.mkdir()
    (base / 'a.py').write_bytes(new_text.encode())
    manifest['workloads'][0]['snapshots'].append({'id': 'v2', 'files': {'a.py': sha}})
    manifest['workloads'][0]['tasks'][-1]['snapshot'] = 'v2'
    response = {'hit': True, 'id': 'memory', 'status': 'stale', 'sources': answer['sources']}
    events['stale'] = [call('colony', 'ask', {}, response), call('repository', 'fixture_read', {'path': 'a.py'},
        {**read, 'sha256': sha, 'content': new_text.rstrip('\n')})]
    assert evaluate()['stale']['stale']
    events['stale'][1]['item']['status'] = 'failed'
    assert not evaluate()['stale']['stale']
    events['stale'][1]['item']['status'] = 'completed'
    response['id'] = 'wrong'
    events['stale'][0] = call('colony', 'ask', {}, response)
    assert not evaluate()['stale']['stale']


@pytest.mark.parametrize('reads', [[True], [{'path': '../a.py'}], [{'path': 'a.py', 'sha256': 'wrong', 'content': 'alpha = 1', 'start_line': 1}]])
def test_bogus_current_reads_are_not_evidence(trace, reads):
    root, manifest, *_ = trace
    files = manifest['workloads'][0]['snapshots'][0]['files']
    assert verify_current_reads(reads, root / 'workspaces/colony/w/v1', files) == []


def test_successful_search_spans_can_ground_publication(trace):
    _, _, events, _, read, evaluate = trace
    events['learn'][0] = call('repository', 'fixture_search', {'query': '='},
        {'path': 'a.py', 'sha256': read['sha256'], 'matches': ['1: alpha = 1', '2: beta = 2']})
    assert evaluate()['learn']['publication']
    events['learn'][0]['item']['arguments']['query'] = 'absent'
    assert not evaluate()['learn']['publication']


@pytest.mark.parametrize('field,value', [('memory_from', 'other'), ('memory_query', 'other')])
def test_declared_memory_binding_is_required(trace, field, value):
    _, manifest, _, _, _, evaluate = trace
    manifest['workloads'][0]['tasks'][1][field] = value
    assert not evaluate()['reuse']['hit']


def test_partial_requires_current_relevant_linked_reads(trace):
    _, _, events, answer, read, evaluate = trace
    partial = copy.deepcopy(answer)
    partial['id'] = 'unpublished'
    partial['current_repository_reads'][0].update(content='alpha = 1', end=1)
    events['gap'] = [call('colony', 'ask', {}, partial),
        call('repository', 'fixture_read', {'path': 'a.py', 'start': 2}, {**read, 'start': 2, 'content': 'beta = 2'})]
    assert not evaluate()['gap']['insufficient']


def test_current_evidence_after_transition_can_prove_stale_without_reresearch(trace):
    root, manifest, events, answer, read, evaluate = trace
    text = 'alpha = 3\nbeta = 2\n'
    sha = hashlib.sha256(text.encode()).hexdigest()
    base = root / 'workspaces/colony/w/v2'
    base.mkdir()
    (base / 'a.py').write_bytes(text.encode())
    manifest['workloads'][0]['snapshots'].append({'id': 'v2', 'files': {'a.py': sha}})
    manifest['workloads'][0]['tasks'][-1]['snapshot'] = 'v2'
    value = {**answer, 'match_kind': 'evidence', 'sources': [{'ref': 'a.py', 'content_hash': sha}],
        'current_repository_reads': [{**read, 'start_line': 1, 'sha256': sha, 'content': text.rstrip('\n')}]}
    events['stale'] = [call('colony', 'ask', {}, value)]
    assert evaluate()['stale']['stale']
    del events['reuse']
    assert not evaluate()['stale']['stale']


@pytest.mark.parametrize('question', ['unrelated', None])
def test_miss_requires_declared_question(trace, question):
    _, manifest, events, _, _, evaluate = trace
    manifest['workloads'][0]['tasks'][1]['question'] = 'frozen question'
    events['reuse'] = [call('colony', 'ask', {'question': question},
        {'hit': False, 'reason': 'no_relevant_trail'})]
    assert not evaluate()['reuse']['miss']
    events['reuse'][0]['item']['arguments']['question'] = 'frozen question'
    assert evaluate()['reuse']['miss']
    manifest['workloads'][0]['tasks'][1]['memory_query'] = 'specific memory query'
    assert not evaluate()['reuse']['miss']


def test_stale_false_hit_marker_is_not_stale_response(trace):
    root, manifest, events, answer, read, evaluate = trace
    text = 'alpha = 3\nbeta = 2\n'
    sha = hashlib.sha256(text.encode()).hexdigest()
    base = root / 'workspaces/colony/w/v2'
    base.mkdir()
    (base / 'a.py').write_bytes(text.encode())
    manifest['workloads'][0]['snapshots'].append({'id': 'v2', 'files': {'a.py': sha}})
    manifest['workloads'][0]['tasks'][-1]['snapshot'] = 'v2'
    events['stale'] = [call('colony', 'ask', {}, {'hit': False, 'id': 'memory',
        'status': 'stale', 'sources': answer['sources']}),
        call('repository', 'fixture_read', {'path': 'a.py'},
             {**read, 'sha256': sha, 'content': text.rstrip('\n')})]
    assert not evaluate()['stale']['stale']


def test_natural_paraphrase_can_prove_reuse_but_controlled_requires_frozen_query(trace):
    _, manifest, events, answer, _, evaluate = trace
    task = manifest['workloads'][0]['tasks'][1]
    task.update(question='What are alpha and beta?', memory_query='alpha beta', memory_from='learn')
    paraphrase = {'question': 'Explain the configured alpha and beta values'}
    events['reuse'] = [call('colony', 'ask', paraphrase, answer)]
    manifest['track'] = 'controlled'
    assert not evaluate()['reuse']['hit']
    manifest['track'] = 'natural'
    assert evaluate()['reuse']['hit'] and evaluate()['reuse']['useful_reuse']
    wrong = copy.deepcopy(answer)
    wrong['id'] = 'unpublished'
    events['reuse'] = [call('colony', 'ask', paraphrase, wrong)]
    assert not evaluate()['reuse']['hit']
    wrong = copy.deepcopy(answer)
    wrong['current_repository_reads'][0]['content'] = 'invented bytes'
    events['reuse'] = [call('colony', 'ask', paraphrase, wrong)]
    assert not evaluate()['reuse']['hit']
    events['reuse'] = [call('colony', 'ask', {'question': '   '}, answer)]
    assert not evaluate()['reuse']['hit']


@pytest.mark.parametrize('choice', ['concise_question', 'additional_source', 'narrow_finding', 'different_parent'])
def test_natural_publications_are_bound_to_evidence_instead_of_controlled_choices(trace, choice):
    root, manifest, events, answer, read, evaluate = trace
    workload = manifest['workloads'][0]
    learn, reuse = workload['tasks'][:2]
    learn.update(phase='learn', question='alpha beta', publication_sources=['a.py'])
    reuse.update(phase='reuse', question='alpha beta', memory_query='alpha beta', memory_from='learn')
    if choice == 'concise_question':
        learn['question'] = 'Explain the configured alpha and beta values using current sources.'
        reuse['memory_query'] = learn['question']
        events['reuse'][0]['item']['arguments']['question'] = learn['question']
    elif choice == 'additional_source':
        text = 'gamma = 3'
        sha = hashlib.sha256(text.encode()).hexdigest()
        (root / 'workspaces/colony/w/v1/b.py').write_bytes(text.encode())
        workload['snapshots'][0]['files']['b.py'] = sha
        extra_read = {'path': 'b.py', 'sha256': sha, 'start': 1, 'end': 1, 'content': text}
        events['learn'].insert(1, call('repository', 'fixture_read', {'path': 'b.py'}, extra_read))
        # Returned memory and publication share the same independently verified sources.
        answer['sources'].append({'ref': 'b.py', 'content_hash': sha})
        answer['current_repository_reads'].append({**extra_read, 'start_line': 1})
        events['reuse'][0] = call('colony', 'ask', {'question': 'alpha beta'}, answer)
    elif choice == 'narrow_finding':
        # Natural learning may publish one fact, later fully answering a narrower task.
        events['learn'][0]['item']['result']['content'][0]['text'] = json.dumps({**read, 'end': 1, 'content': 'alpha = 1'})
        events['learn'][-1]['item']['arguments']['content'] = 'alpha is 1'
        answer['content'] = 'alpha is 1'
        reuse['oracle'] = {'citations': [{'path': 'a.py', 'start': 1, 'end': 1}]}
        answer['current_repository_reads'][0].update(end=1, content='alpha = 1')
        events['reuse'][0] = call('colony', 'ask', {'question': 'alpha beta'}, answer)
    else:
        other = copy.deepcopy(learn)
        other.update(id='other', question='another learning question')
        workload['tasks'].insert(1, other)
        reuse.update(memory_from='other', memory_query=other['question'])
        reuse_events = events.pop('reuse')
        events['other'] = [call('repository', 'fixture_read', {'path': 'a.py'}, read)]
        events['reuse'] = reuse_events
        events['reuse'][0]['item']['arguments']['question'] = other['question']
    manifest['track'] = 'controlled'
    assert not evaluate()['reuse']['useful_reuse']
    manifest['track'] = 'natural'
    proof = evaluate()
    assert proof['learn']['publication'] and proof['reuse']['useful_reuse']
    forged = copy.deepcopy(events['reuse'][0])
    payload = json.loads(forged['item']['result']['content'][0]['text'])
    payload['current_repository_reads'][0]['sha256'] = '0' * 64
    forged['item']['result']['content'][0]['text'] = json.dumps(payload)
    events['reuse'] = [forged]
    assert not evaluate()['reuse']['useful_reuse']


@pytest.mark.parametrize('lifecycle,unresolved', [
    (['item.started'], 1),
    (['item.updated'], 1),
    (['item.completed'], 0),
    (['item.started', 'item.updated', 'item.completed'], 0),
    (['item.completed', 'item.started'], 1),
])
def test_every_research_attempt_disqualifies_no_extra_research_credit(trace, lifecycle, unresolved):
    root, _, events, _, _, evaluate = trace
    item = {'id': 'repository-attempt', 'type': 'mcp_tool_call', 'server': 'repository',
            'tool': 'fixture_read', 'arguments': {'path': 'a.py'}, 'status': 'failed'}
    events['reuse'] += [{'type': state, 'item': copy.deepcopy(item)} for state in lifecycle]
    proof = evaluate()['reuse']
    from scripts.token_evaluation.report import activity
    assert proof['research_attempts'] == 1
    if lifecycle == ['item.completed', 'item.started']:
        # The mechanism diagnostic still withholds reuse credit; the report now
        # rejects this invalid lifecycle for either arm before comparing tokens.
        with pytest.raises(ValueError, match='after completion'):
            activity(root / 'reuse.jsonl')
    else:
        assert activity(root / 'reuse.jsonl')['research_calls'] == 1
    assert proof['hit']  # Current source evidence still exists; extra work is counted separately.
    assert proof['unresolved_mcp_calls'] == unresolved
    assert not proof['useful_reuse']


def test_resolved_colony_lifecycle_preserves_reuse_but_unresolved_call_withholds_credit(trace):
    _, _, events, _, _, evaluate = trace
    completed = events['reuse'][0]
    item = {k: v for k, v in completed['item'].items() if k not in {'result', 'status'}}
    events['reuse'] = [{'type': 'item.started', 'item': item},
                       {'type': 'item.updated', 'item': item}, completed]
    assert evaluate()['reuse']['useful_reuse']
    events['reuse'].append({'type': 'item.started', 'item': {**item, 'id': 'unfinished-colony'}})
    proof = evaluate()['reuse']
    assert proof['research_attempts'] == 0 and proof['unresolved_mcp_calls'] == 1
    assert not proof['useful_reuse']


def test_uninspected_arm_does_not_report_zero_research_attempts(tmp_path):
    proof = evaluate_mechanisms(tmp_path, {}, [{'id': 'baseline', 'arm': 'no_colony'}])['baseline']
    assert proof['research_attempts'] is None and proof['unresolved_mcp_calls'] is None
