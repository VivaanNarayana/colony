"""Local synthetic/transport checks only; no model evaluation or historical replay."""
import asyncio
import copy
import hashlib
import json

import pytest
from test_batch_adapter_integration import batch_server as batch_server
from test_batch_adapter_integration import call
from test_batch_adapter_integration import identity as identity

from scripts.token_evaluation import report, tool_receipts
from scripts.token_evaluation.evidence import _objects, _research
from scripts.token_evaluation.protocol import validate_contract
from scripts.token_evaluation.research_budget import receipt
from scripts.token_evaluation.task_evidence import verified_reads
from scripts.token_evaluation.usage import native_usage


def event(tool, args, value):
    return {'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'id': '1',
            'server': 'colony', 'tool': tool, 'arguments': args, 'status': 'completed',
            'result': {'content': [{'type': 'text', 'text': json.dumps(value)}]}}}


def blocks(server, name, **args):
    value = asyncio.run(server.call_tool(name, args))
    return [b.model_dump() for b in (value.content if hasattr(value, 'content') else value)]


@pytest.mark.parametrize('serialized', [False, True])
def test_two_prior_units_then_five_entries_admits_four_and_denies_one(batch_server, identity, serialized):
    server = batch_server()
    blocks(server, 'fixture_files')
    blocks(server, 'fixture_search', query='TIMEOUT')
    entries = [{'path': 'settings.py'}] * 5
    args = {'batch': json.dumps(entries) if serialized else entries}
    result = blocks(server, 'read', **args)
    value = json.loads(result[0]['text'])
    assert len(value['results']) == 5
    assert all(v['evidence_ref'] for v in value['results'][:4])
    assert value['results'][4] == {'error': 'Research call limit reached', 'evidence_ref': None}
    assert receipt(result[-1]) == {'admitted': 4, 'denied': 1, 'succeeded': 4, 'remaining': 0}
    assert len(server.evidence._observations) == 4
    denied = blocks(server, 'read', batch=[{'path': 'README.md'}])
    assert receipt(denied[-1]) == {'admitted': 0, 'denied': 1, 'succeeded': 0, 'remaining': 0}
    assert len(server.evidence._observations) == 4


def test_invalid_admitted_entry_consumes_unit_without_blocking_other_entries(batch_server):
    server = batch_server(max_calls=2)
    result = blocks(server, 'read', batch=[{'path': '../outside'}, {'path': 'settings.py'},
                                         {'path': 'README.md'}])
    values = json.loads(result[0]['text'])['results']
    assert values[0]['evidence_ref'] is None and values[1]['evidence_ref']
    assert values[2]['error'] == 'Research call limit reached'
    assert receipt(result[-1]) == {'admitted': 2, 'denied': 1, 'succeeded': 1, 'remaining': 0}
    assert 'outside' not in str(result)


def test_invalid_schema_is_charged_and_receipt_does_not_echo_arguments(batch_server):
    server = batch_server(max_calls=2)
    with pytest.raises(Exception) as exc:
        call(server, 'read', batch=[{'path': 'SECRET', 'offset': 'bad'}] * 3)
    value = json.loads(str(exc.value))
    assert value['research_budget'] == {'admitted': 2, 'denied': 1, 'succeeded': 0, 'remaining': 0}
    assert 'SECRET' not in str(exc.value) and not server.evidence._observations


def test_zero_budget_cannot_unlock_publication(batch_server):
    server = batch_server(max_calls=1)
    blocks(server, 'fixture_files')
    result = blocks(server, 'read', batch=[{'path': 'settings.py'}])
    assert not json.loads(result[0]['text'])['results'][0]['evidence_ref']
    assert 'publish' not in {t.name for t in asyncio.run(server.list_tools())}


def test_partial_batch_can_publish_only_admitted_evidence(batch_server, identity, monkeypatch):
    client, repo, _ = identity
    server = batch_server(max_calls=1)
    values = call(server, 'read', batch=[{'path': 'settings.py'}, {'path': 'README.md'}])['results']
    sent = []
    monkeypatch.setattr(client, 'push', lambda trail, **kw: sent.append(trail) or {'stored': True})
    assert values[1]['evidence_ref'] is None
    assert call(server, 'publish', content='Supported behavior', applicability='settings',
                evidence_refs=[values[0]['evidence_ref']])['stored']
    assert len(sent) == 1
    assert [(s.ref, s.content_hash) for s in sent[0].sources] == [
        ('settings.py', hashlib.sha256((repo / 'settings.py').read_bytes()).hexdigest())]
    assert not call(server, 'publish', content='again', applicability='settings',
                    evidence_refs=[values[0]['evidence_ref']])['stored']


def test_concurrent_batches_share_one_budget_without_double_charging(batch_server):
    server = batch_server(max_calls=3)
    async def run():
        return await asyncio.gather(*(server.call_tool('read', {'batch': [{'path': 'settings.py'}] * 2})
                                      for _ in range(2)))
    results = asyncio.run(run())
    budgets = [receipt(r[-1].model_dump()) for r in results]
    assert sum(b['admitted'] for b in budgets) == 3
    assert sum(b['denied'] for b in budgets) == 1
    assert len(server.evidence._observations) == 3


def test_partial_batch_receipts_evidence_accounting_and_native_cost(batch_server, identity, tmp_path):
    server = batch_server(max_calls=2)
    args = {'batch': [{'path': 'settings.py'}, {'path': '../outside'}, {'path': 'README.md'}]}
    result = blocks(server, 'read', **args)
    done = event('read', args, {})
    done['item']['result']['content'] = result
    start = copy.deepcopy(done)
    start['type'] = 'item.started'
    start['item'].pop('result')
    finish = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'answer'}}
    usage = {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 80,
                                               'output_tokens': 5}}
    events = [{'type': 'turn.started'}, start, done, finish, usage]
    path = tmp_path / 'events.jsonl'
    path.write_text('\n'.join(map(json.dumps, events)))
    counts = report.activity(path, 'task')
    assert counts['research_calls'] == 3  # Still exceeds the two-unit policy.
    assert counts['colony_calls'] == 1
    assert (counts['research_admitted'], counts['research_denied'], counts['research_succeeded'],
            counts['research_failed'], counts['research_unresolved_units']) == (2, 1, 1, 1, 0)
    assert native_usage(path)['known_usage']['gross_tokens'] == 105
    repo = identity[1]
    files = {p: hashlib.sha256((repo / p).read_bytes()).hexdigest() for p in ['settings.py', 'README.md']}
    verified = verified_reads(done['item'], json.loads(result[0]['text']), repo, files, {})
    assert len(verified) == 1 and verified[0][1]['ref'] == 'settings.py'
    packet = tool_receipts.collect(events, files)
    tool_receipts.validate(packet['tool_receipts'], packet['tool_receipt_limits'], files)
    outcomes = [r['outcome'] for r in packet['tool_receipts']]
    assert outcomes[0]['result_available'] and not outcomes[1]['result_available']
    assert outcomes[2]['error'] == 'research_call_limit'


@pytest.mark.parametrize('tool,args', [('fixture_files', {}), ('fixture_search', {'query': 'absent'})])
def test_telemetry_does_not_hide_listing_or_empty_search(batch_server, identity, tool, args):
    result = blocks(batch_server(), tool, **args)
    done = event(tool, args, {})
    done['item']['result']['content'] = result
    packet = tool_receipts.collect([done, {'type': 'item.completed', 'item': {
        'type': 'agent_message', 'text': 'answer'}}], ['settings.py', 'README.md'])
    outcome = packet['tool_receipts'][0]['outcome']
    assert outcome['result_available']
    if tool == 'fixture_search':
        assert outcome['empty'] and outcome['match_count'] == 0


@pytest.mark.parametrize('contract', ['/14', '/15'])
def test_old_protocols_are_rejected_without_mutation(contract):
    old = {'contract': 'colony.token-evaluation' + contract, 'historical': {'grade': 'failed', 'gross': 123}}
    original = copy.deepcopy(old)
    with pytest.raises(ValueError, match='original pinned collector'):
        validate_contract(old)
    assert old == original


@pytest.mark.parametrize('changed', [{'admitted': True}, {'succeeded': 2}, {'remaining': 13}, {'denied': -1}])
def test_invalid_budget_receipts_never_gain_accounting(changed):
    budget = {'admitted': 1, 'denied': 0, 'succeeded': 1, 'remaining': 5, **changed}
    assert receipt({'type': 'text', 'text': json.dumps({'research_budget': budget})}) is None


@pytest.mark.parametrize('batch', [False, True])
def test_clipped_evidence_credits_actual_lines_only(tmp_path, batch):
    text = 'first\nsecond'
    (tmp_path / 'a.py').write_bytes(text.encode())
    files = {'a.py': hashlib.sha256(text.encode()).hexdigest()}
    request = {'path': 'a.py', 'end': 120}
    page = {'path': 'a.py', 'start_line': 1, 'end_line': 2, 'content': text,
            'complete_file': True, 'evidence_ref': 'a' * 24}
    args = {'batch': [request]} if batch else request
    value = {'results': [page]} if batch else page
    item = event('read', args, value)['item']
    verified = verified_reads(item, value, tmp_path, files, {})
    assert verified[0][1]['ref'] == 'a.py'
    assert verified[0][0][-1] == {'path': 'a.py', 'start': 2, 'end': 2}
    page['end_line'] = 120
    assert not any(source for _, source, _ in verified_reads(item, value, tmp_path, files, {}))


def test_clipped_unicode_continuation_and_empty_file(tmp_path):
    text = 'é' * 9000
    (tmp_path / 'a.py').write_bytes(text.encode())
    files = {'a.py': hashlib.sha256(text.encode()).hexdigest()}
    pages = {}
    for begin, end in [(0, 8000), (8000, 9000)]:
        args = {'path': 'a.py', 'end': 120, 'offset': begin}
        value = {'path': 'a.py', 'start_line': 1, 'end_line': 1, 'content': text[begin:end],
                 'complete_file': False, 'evidence_ref': 'a' * 24,
                 'start_offset': begin, 'end_offset': end, 'next_offset': end if end < 9000 else None}
        verified = verified_reads(event('read', args, value)['item'], value, tmp_path, files, {}, pages=pages)
        assert verified[0][1]
    assert verified[0][0] == [{'path': 'a.py', 'start': 1, 'end': 1}]
    (tmp_path / 'a.py').write_bytes(b'')
    files['a.py'] = hashlib.sha256(b'').hexdigest()
    value = {'path': 'a.py', 'start_line': 1, 'end_line': 0, 'content': '',
             'complete_file': True, 'evidence_ref': 'b' * 24}
    item = event('read', {'path': 'a.py', 'end': 120}, value)['item']
    verified = verified_reads(item, value, tmp_path, files, {})
    assert verified[0][0] == [] and verified[0][1]


@pytest.mark.parametrize('variant', ['absent', 'duplicate', 'mismatch', 'unfinished', 'failed_success'])
def test_missing_or_ambiguous_receipts_remain_unresolved(tmp_path, variant):
    done = event('read', {'path': 'a.py'}, {})
    budget = {'admitted': 1, 'denied': 0, 'succeeded': 1, 'remaining': 5}
    if variant == 'mismatch':
        budget['denied'] = 1
    receipt_block = {'type': 'text', 'text': json.dumps({'research_budget': budget})}
    done['item']['result']['content'] = ([] if variant == 'absent' else
                                         [receipt_block] * (2 if variant == 'duplicate' else 1))
    if variant == 'unfinished':
        done['type'] = 'item.started'
    if variant == 'failed_success':
        done['item']['status'] = 'failed'
    path = tmp_path / 'events.jsonl'
    path.write_text(json.dumps(done))
    counts = report.activity(path, 'task')
    assert counts['research_calls'] == counts['research_unresolved_units'] == 1
    assert counts['research_admitted'] == counts['research_denied'] == 0


def test_budget_block_preserves_repository_evidence_without_adding_coverage(tmp_path):
    raw = b'VALUE = 42\n'
    (tmp_path / 'a.py').write_bytes(raw)
    files = {'a.py': hashlib.sha256(raw).hexdigest()}
    value = {'path': 'a.py', 'start': 1, 'end': 1, 'content': 'VALUE = 42', 'sha256': files['a.py']}
    item = event('fixture_read', {'path': 'a.py'}, value)['item']
    budget = {'type': 'text', 'text': json.dumps({'research_budget': {
        'admitted': 1, 'denied': 0, 'succeeded': 1, 'remaining': 5}})}
    item['result']['content'].append(budget)
    assert _research(item, tmp_path, files) == [{'path': 'a.py', 'start': 1, 'end': 1}]
    item['result']['content'] = [budget]
    assert _research(item, tmp_path, files) == []
    item['result']['content'].insert(0, {'type': 'text', 'text': json.dumps(value)})
    item['status'] = 'failed'
    assert _objects(item) == []
