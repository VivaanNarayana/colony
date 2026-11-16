"""Synthetic accounting/evidence counterproofs; no worker or reviewer sessions."""
import copy
import hashlib
import json

import pytest
from test_agent_quality import build_reviewed_run, score
from test_evidence import trace as trace
from test_report import put, rewrite_events
from test_report import run as run
from test_task_adapter import event

from scripts.token_evaluation import report, tool_receipts
from scripts.token_evaluation.read_contract import arguments, read_units
from scripts.token_evaluation.task_evidence import verified_reads
from scripts.token_evaluation.usage import native_usage


def write_events(path, events):
    path.write_text('\n'.join(map(json.dumps, events)), encoding='utf-8')
    return path


def page(path, content, ref='a', **extra):
    return {'path': path, 'content': content, 'start_line': 1, 'end_line': 1,
            'complete_file': True, 'evidence_ref': ref * 24, **extra}


@pytest.mark.parametrize('value,count', [
    ({}, 1), ({'batch': None}, 1), ({'batch': []}, 1), ({'batch': 'bad'}, 1),
    ({'batch': [None, {}]}, 2), ({'batch': [{'path': 'a'}] * 9}, 9),
    ({'batch': '[{"path":"a"},{"path":"a","offset":4}]'}, 2),
    ({'path': '[' * 2000 + ']' * 2000, 'batch': [{}, {}]}, 2),
    ('{"batch":[{},{}]}', 2), ('[]', 1), (None, 1),
])
def test_requested_entries_are_charged_before_validation(value, count):
    assert read_units(value) == count


@pytest.mark.parametrize('batch', [[], [{}], [{'path': 'a', 'offset': '0'}],
    [{'path': 'a', 'start': True}], [{'path': 'a', 'extra': 1}], [{'path': 'a'}] * 9])
def test_invalid_batch_arguments_never_gain_evidence(batch):
    assert arguments({'arguments': {'batch': batch}}) is None


def test_per_entry_lifecycle_accounting_keeps_physical_calls_and_gross(tmp_path):
    args = {'batch': [{'path': 'a'}, {'path': 'b', 'offset': 10}]}
    done = event('read', args, {'results': [page('a', 'x'), {'error': 'failed', 'evidence_ref': None}]})
    start = copy.deepcopy(done)
    start['type'] = 'item.started'
    start['item'].pop('result')
    start['item']['arguments'] = {}  # Arguments may arrive on the update.
    update = copy.deepcopy(start)
    update.update(type='item.updated')
    update['item']['arguments'] = args
    usage = {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 20,
                                               'output_tokens': 7}}
    events = [{'type': 'turn.started'}, start, update, done, usage,
              {'type': 'turn.started'}, update, usage]  # Same ID, unresolved second turn.
    path = write_events(tmp_path / 'events.jsonl', events)
    counts = report.activity(path, 'task')
    assert counts['colony_calls'] == counts['task_adapter_calls'] == 2
    assert counts['research_calls'] == counts['task_read_attempts'] == 4
    assert counts['task_reference_results'] == 1
    assert native_usage(path)['known_usage']['gross_tokens'] == 214
    events.insert(4, done)
    with pytest.raises(ValueError, match='Duplicate completed'):
        report.activity(write_events(path, events), 'task')


@pytest.fixture
def frozen(tmp_path):
    content = {'a.py': 'alpha = 1', 'b.py': 'beta = 2'}
    for path, text in content.items():
        (tmp_path / path).write_bytes(text.encode())
    files = {path: hashlib.sha256(text.encode()).hexdigest() for path, text in content.items()}
    args = {'batch': [{'path': path} for path in content]}
    value = {'results': [page(path, text, ref) for (path, text), ref in zip(content.items(), 'ab', strict=True)]}
    return tmp_path, files, event('read', args, value)['item'], value


def test_batch_evidence_matches_requested_sources(frozen):
    root, files, item, value = frozen
    result = verified_reads(item, value, root, files, {})
    assert [source['ref'] for _, source, _ in result] == ['a.py', 'b.py']
    assert [spans[0]['path'] for spans, _, _ in result] == ['a.py', 'b.py']


@pytest.mark.parametrize('mutation', ['reorder', 'missing', 'extra', 'duplicate_handle',
                                     'forged', 'hash', 'offset', 'unlisted', 'error_handle'])
def test_batch_cannot_gain_unobserved_evidence(frozen, mutation):
    root, files, item, value = frozen
    if mutation == 'reorder':
        value['results'].reverse()
    elif mutation == 'missing':
        value['results'].pop()
    elif mutation == 'extra':
        value['results'].append(value['results'][0])
    elif mutation == 'duplicate_handle':
        value['results'][1]['evidence_ref'] = value['results'][0]['evidence_ref']
    elif mutation == 'forged':
        value['results'][0]['content'] = 'alpha = 9000'
    elif mutation == 'hash':
        files['a.py'] = '0' * 64
    elif mutation == 'offset':
        item['arguments']['batch'][0]['offset'] = 1
    elif mutation == 'unlisted':
        del files['a.py']
    else:
        value['results'][0]['error'] = 'ignore safety'
    result = verified_reads(item, value, root, files, {})
    assert not any(source and source['ref'] == 'a.py' for _, source, _ in result)


def test_batch_aggregate_limit_and_continuations(frozen):
    root, files, item, value = frozen
    text = 'é' * 9000
    (root / 'a.py').write_bytes(text.encode())
    files['a.py'] = hashlib.sha256(text.encode()).hexdigest()
    value['results'][0] = page('a.py', text[:4000], complete_file=False,
                              start_offset=0, end_offset=4000, next_offset=4000)
    pages = {}
    result = verified_reads(item, value, root, files, {}, pages=pages)
    assert result[0][0] == [] and result[0][1]  # Partial long line is not yet evidence.
    follow = page('a.py', text[4000:], 'c', complete_file=False,
                  start_offset=4000, end_offset=9000, next_offset=None)
    single = event('read', {'path': 'a.py', 'offset': 4000}, follow)['item']
    assert verified_reads(single, follow, root, files, {}, pages=pages)[0][0] == [
        {'path': 'a.py', 'start': 1, 'end': 1}]
    value['results'][0]['content'] = text
    assert verified_reads(item, value, root, files, {}, pages={}) == []


def test_batch_publication_requires_prior_verified_entries(trace):
    _, manifest, events, answer, read, evaluate = trace
    manifest['track'] = 'natural'
    for task in manifest['workloads'][0]['tasks']:
        task['question'] = 'alpha beta'
    value = {'results': [page('a.py', read['content'], end_line=2),
                         {'error': 'read failed', 'evidence_ref': None}]}
    args = {'batch': [{'path': 'a.py'}, {'path': 'missing.py'}]}
    publication = event('publish', {'content': 'alpha is 1; beta is 2', 'applicability': 'scope',
                        'evidence_refs': ['a' * 24]}, {'stored': True, 'id': 'memory'}, 2)
    events['learn'] = [event('read', args, value), publication]
    answer['content'] = 'scope\nalpha is 1; beta is 2'
    events['reuse'] = [event('research', {}, answer)]
    proof = evaluate()
    assert proof['learn']['publication'] and proof['reuse']['useful_reuse']
    assert proof['learn']['research_attempts'] == 2 and proof['learn']['successful_research'] == 1
    publication['item']['arguments']['evidence_refs'] = ['b' * 24]
    assert not evaluate()['learn']['publication']
    publication['item']['arguments']['evidence_refs'] = ['a' * 24]
    events['learn'].reverse()
    assert not evaluate()['learn']['publication']


def receipt_packet(item, files):
    return tool_receipts.collect([{'type': 'item.completed', 'item': item},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'answer'}}], files)


def test_review_receipts_keep_per_entry_errors_offsets_and_parent_hash(frozen):
    _, files, item, value = frozen
    item['arguments']['batch'][1]['offset'] = 9
    value['results'][1] = {'error': 'SECRET malicious instructions', 'evidence_ref': None}
    item['result']['content'][0]['text'] = json.dumps(value)
    packet = receipt_packet(item, files)
    receipts = packet['tool_receipts']
    tool_receipts.validate(receipts, packet['tool_receipt_limits'], files)
    assert [r['batch_index'] for r in receipts] == [0, 1]
    assert all(r['batch_size'] == 2 for r in receipts)
    assert receipts[0]['event_sha256'] == receipts[1]['event_sha256']
    assert receipts[0]['outcome']['returned_paths'] == ['a.py']
    assert receipts[1]['arguments']['offset'] == 9
    assert receipts[1]['outcome']['error'] == 'tool_error'
    assert not receipts[1]['outcome']['result_available']
    assert 'SECRET' not in str(packet) and 'a' * 24 not in str(packet)
    for key, bad in [('batch_index', True), ('batch_index', 2), ('batch_size', 9)]:
        changed = copy.deepcopy(receipts)
        changed[0][key] = bad
        with pytest.raises(ValueError, match='unsafe tool receipt'):
            tool_receipts.validate(changed, packet['tool_receipt_limits'], files)


@pytest.mark.parametrize('failed', [False, True])
def test_batch_missing_results_do_not_claim_success(frozen, failed):
    _, files, item, _ = frozen
    item['result'] = {'isError': failed, 'content': []}
    receipts = receipt_packet(item, files)['tool_receipts']
    assert len(receipts) == 2
    assert all(not r['outcome']['result_available'] for r in receipts)
    assert all(r['outcome']['is_error'] is failed for r in receipts)


def test_receipt_cap_counts_omitted_entries(frozen):
    _, files, item, _ = frozen
    events = [{'type': 'item.completed', 'item': {**item, 'id': str(n)}} for n in range(33)]
    events.append({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'answer'}})
    packet = tool_receipts.collect(events, files)
    assert len(packet['tool_receipts']) == 64
    assert packet['tool_receipt_limits']['omitted_calls'] == 2
    tool_receipts.validate(packet['tool_receipts'], packet['tool_receipt_limits'], files)


def test_review_packet_and_grade_keep_batch_evidence_and_gross_usage(run):
    original_usage = {}
    def replace_read(root, _quality):
        frozen = json.loads((root / 'run.json').read_text())
        frozen['protocol']['target']['adapter'] = 'task'
        frozen['protocol_sha256'] = report.digest(frozen['protocol'])
        put(root / 'run.json', frozen)
        # This control rewrites synthetic tool events; it is not a native launch receipt.
        put(root / 'collection.json', {'native_evidence': False, 'synthetic_schema_control': True})
        args = {'batch': [{'path': 'settings.py'}, {'path': 'extra.py'}]}
        value = {'results': [page('settings.py', 'TIMEOUT = 1500'), page('extra.py', 'EXTRA = 1', 'b')]}
        path = root / 'logs/b.events.jsonl'
        original_usage.update(native_usage(path)['known_usage'])
        def replace(events):
            item = next(e['item'] for e in events if e.get('item', {}).get('id') == 'observed-source')
            item.update(event('read', args, value)['item'])
        rewrite_events(root, 'b', replace)

    root, public, judgments = build_reviewed_run(run, replace_read, extra_sources={'extra.py': b'EXTRA = 1\n'})
    batch_case = next(row for row in public if any('batch_index' in r for r in row['tool_receipts']))
    assert {s['path'] for s in batch_case['observed_current_evidence']} == {'settings.py', 'extra.py'}
    assert len(batch_case['tool_receipts']) == 2
    assert native_usage(root / 'logs/b.events.jsonl')['known_usage'] == original_usage
    result = score(root, judgments)  # Fabricated schema controls, no reviewer model.
    assert all(row['quality_state'] == 'passed' for row in result['attempts'])
    assert result['arms']['colony']['known_usage']['gross_tokens'] == original_usage['gross_tokens']
