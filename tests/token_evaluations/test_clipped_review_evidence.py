"""Actual excerpt producer and synthetic grading controls; no model evaluations."""
import copy
import hashlib
import json

import pytest

from colony.context import excerpts
from colony.models import Source
from scripts.token_evaluation.evidence import _research, verify_current_reads
from tests.token_evaluations.test_agent_quality import build_reviewed_run, score
from tests.token_evaluations.test_path_alias_evidence import item, make_directory_link
from tests.token_evaluations.test_report import rewrite_events
from tests.token_evaluations.test_report import run as run

RAW = b'TIMEOUT = 1500\n# ' + b'x' * 20000 + b'\nUNSEEN = 3\n'


def produced_reads(root):
    sha = hashlib.sha256((root / 'settings.py').read_bytes()).hexdigest()
    source = Source(kind='file', ref='settings.py', content_hash=sha)
    result = excerpts(root, [source], 'What is TIMEOUT?', whole_files=True,
                      preferred=[('settings.py', 1)])
    # The MCP adapter maps the real excerpt without changing its text.
    return [{'path': e['ref'], 'sha256': sha, 'start_line': e['line'],
             'complete_file': e['complete_file'], 'content': e['text']} for e in result]


@pytest.fixture
def clipped(tmp_path):
    (tmp_path / 'settings.py').write_bytes(RAW)
    reads = produced_reads(tmp_path)
    assert len(reads) == 1 and len(reads[0]['content']) == 2400
    assert reads[0]['complete_file'] is False
    return tmp_path, {'settings.py': hashlib.sha256(RAW).hexdigest()}, reads


def observed(root, files, reads):
    return verify_current_reads(reads, root, files, allow_clipped_tail=True)


def test_real_producer_retains_only_complete_lines_without_changing_strict_proofs(clipped):
    root, files, reads = clipped
    original = copy.deepcopy(reads)
    assert observed(root, files, reads) == [{'path': 'settings.py', 'start': 1, 'end': 1}]
    assert verify_current_reads(reads, root, files) == []
    assert reads == original
    # A repository result cannot use the semantic-only partial-tail allowance.
    result = {**reads[0], 'start': 1, 'end': 2}
    assert _research(item('settings.py', result, lines=2), root, files) == []


@pytest.mark.parametrize('content,expected_end', [
    ('TIME', None),  # no complete line delivered
    ('TIMEOUT = 1500', 1),  # exact end of a complete line
    ('TIMEOUT = 1500\n', 1),  # do not invent the next line
    ('TIMEOUT = 1500\n# x', 1),
])
def test_character_boundaries_do_not_credit_the_clipped_line(clipped, content, expected_end):
    root, files, reads = clipped
    reads[0]['content'] = content
    spans = observed(root, files, reads)
    assert spans == ([] if expected_end is None else [{'path': 'settings.py', 'start': 1, 'end': expected_end}])


def test_partial_only_read_does_not_discard_other_valid_observations(clipped):
    root, files, reads = clipped
    partial = {**reads[0], 'content': 'TIME'}
    full_line = {**reads[0], 'start_line': 3, 'content': 'UNSEEN = 3'}
    assert observed(root, files, [partial, full_line]) == [{'path': 'settings.py', 'start': 3, 'end': 3}]


@pytest.mark.parametrize('mutation', ['hash', 'prefix', 'tail', 'start', 'bool_start', 'end', 'outside',
                                     'absolute', 'alias', 'unlisted', 'full_file', 'missing_marker', 'false_like_marker'])
def test_malformed_clipped_read_rejects_the_entire_bundle(clipped, mutation):
    root, files, reads = clipped
    valid = {**reads[0], 'content': 'TIMEOUT = 1500'}
    bad = reads[0]
    if mutation == 'hash':
        bad['sha256'] = '0' * 64
    elif mutation == 'prefix':
        bad['content'] = bad['content'].replace('1500', '9000')
    elif mutation == 'tail':
        bad['content'] = bad['content'][:-1] + 'z'
    elif mutation == 'start':
        bad['start_line'] = 2
    elif mutation == 'bool_start':
        bad['start_line'] = True
    elif mutation == 'end':
        bad['end'] = 1
    elif mutation in {'outside', 'absolute', 'alias', 'unlisted'}:
        bad['path'] = {'outside': '../settings.py', 'absolute': str(root / 'settings.py'),
                       'alias': './settings.py', 'unlisted': 'other.py'}[mutation]
    elif mutation == 'full_file':
        bad['complete_file'] = True
    elif mutation == 'missing_marker':
        bad.pop('complete_file')
    else:
        bad['complete_file'] = 0
    assert observed(root, files, [valid, bad]) == []


def test_complete_file_claim_cannot_hide_clipping_even_when_line_count_matches(clipped):
    root, files, reads = clipped
    reads[0].update(complete_file=True, content=RAW.decode().rstrip('\n')[:-1])
    assert observed(root, files, reads) == []


def test_changed_source_and_linked_root_are_rejected(clipped):
    root, files, reads = clipped
    link = root.parent / (root.name + '-link')
    make_directory_link(link, root)
    assert observed(link, files, reads) == []
    (root / 'settings.py').write_bytes(RAW.replace(b'1500', b'9000'))
    assert observed(root, files, reads) == []


def with_real_excerpt(root, _quality):
    reads = produced_reads(root / 'workspaces/colony/gateway/v1')
    def replace(events):
        target = next(e['item'] for e in events if e.get('item', {}).get('id') == 'observed-source')
        target['result']['content'][0]['text'] = json.dumps({'hit': True, 'status': 'validated',
                                                          'current_repository_reads': reads})
    rewrite_events(root, 'b', replace)


def test_actual_clipped_excerpt_and_repository_read_get_equal_grading_credit(run):
    root, public, submission = build_reviewed_run(run, with_real_excerpt, source_bytes=RAW)
    assert public[0]['observed_current_evidence'] == public[1]['observed_current_evidence']
    assert [(e['start'], e['end'], e['content']) for e in public[0]['observed_current_evidence']] == [
        (1, 1, 'TIMEOUT = 1500')]
    value = score(root, submission)
    assert all(row['answer_quality_successful'] for row in value['attempts'])
    assert value['quality_complete'] and value['tool_policy_compliant']
    assert value['arms']['colony']['total_usage']['gross_tokens'] == 100
    assert value['arms']['no_colony']['total_usage']['gross_tokens'] == 120
    assert not value['certifying']


@pytest.mark.parametrize('line', [2, 3])
@pytest.mark.parametrize('identity', ['a', 'b'])
def test_clipped_line_and_unseen_suffix_cannot_support_grounding_in_either_arm(run, line, identity):
    root, public, submission = build_reviewed_run(run, with_real_excerpt, source_bytes=RAW)
    private = json.loads((root / 'reviewed/private-map.json').read_text())
    case_id = next(row['case'] for row in private if row['id'] == identity)
    source = next(row for row in public if row['case'] == case_id)['current_sources'][0]
    for review in submission['reviews']:
        case = next(case for case in review['cases'] if case['case'] == case_id)
        case['findings'][1]['sources'] = [{'path': source['path'], 'sha256': source['sha256'],
            'start': line, 'end': line, 'text': source['content'].splitlines()[line - 1]}]
    with pytest.raises(ValueError, match='not observed'):
        score(root, submission)


@pytest.mark.parametrize('failure', ['failed', 'error', 'after_answer'])
def test_clipped_excerpts_still_require_successful_delivery_before_answer(run, failure):
    def alter(root, quality):
        with_real_excerpt(root, quality)
        def change(events):
            event = next(e for e in events if e.get('item', {}).get('id') == 'observed-source')
            if failure == 'failed':
                event['item']['status'] = 'failed'
            elif failure == 'error':
                event['item']['result']['isError'] = True
            else:
                events.remove(event)
                events.append(event)
        rewrite_events(root, 'b', change)
    root, public, submission = build_reviewed_run(run, alter, source_bytes=RAW)
    assert sorted(len(row['observed_current_evidence']) for row in public) == [0, 1]
    with pytest.raises(ValueError, match='not observed'):
        score(root, submission)
