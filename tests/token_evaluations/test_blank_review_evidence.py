"""Actual blank-file rendering and synthetic report controls; no model evaluations."""
import copy
import hashlib
import json

import pytest

from scripts.token_evaluation.evidence import verify_current_reads
from tests.token_evaluations.test_agent_quality import build_reviewed_run, score
from tests.token_evaluations.test_path_alias_evidence import make_directory_link
from tests.token_evaluations.test_redacted_review_evidence import actual_reads
from tests.token_evaluations.test_report import rewrite_events
from tests.token_evaluations.test_report import run as run


def observed(root, files, reads):
    return verify_current_reads(reads, root, files, allow_clipped_tail=True,
                                allow_redaction=True, allow_blank_file=True)


@pytest.fixture
def bundle(tmp_path):
    (tmp_path / 'settings.py').write_bytes(b'TIMEOUT = 1500\n')
    (tmp_path / 'blank.py').write_bytes(b'\n')
    files, reads = actual_reads(tmp_path, ('settings.py', 'blank.py'))
    assert reads[1]['content'] == '' and reads[1]['complete_file'] is True
    return tmp_path, files, reads


@pytest.mark.parametrize('raw', [b'\n', b'\r\n', b'\r'])
@pytest.mark.parametrize('reverse', [False, True])
def test_actual_blank_file_keeps_valid_siblings_without_earning_credit(bundle, raw, reverse):
    root, _, _ = bundle
    (root / 'blank.py').write_bytes(raw)
    files, reads = actual_reads(root, ('settings.py', 'blank.py'))
    blank = reads[1]
    if reverse:
        reads.reverse()
    before = copy.deepcopy(reads)
    assert observed(root, files, reads) == [{'path': 'settings.py', 'start': 1, 'end': 1}]
    assert observed(root, files, [blank]) == []
    assert verify_current_reads(reads, root, files) == []  # default proof behavior is unchanged
    assert reads == before


@pytest.mark.parametrize('mutation', ['hash', 'path', 'absolute', 'alias', 'start', 'bool_start', 'end',
                                     'bool_end', 'float_end', 'false_marker', 'numeric_marker', 'missing_marker',
                                     'null_content', 'nonblank', 'zero_bytes', 'multiple_lines', 'changed_source'])
def test_empty_claims_do_not_bypass_source_and_full_file_checks(bundle, mutation):
    root, files, reads = bundle
    blank = reads[1]
    if mutation == 'hash':
        blank['sha256'] = '0' * 64
    elif mutation in {'path', 'absolute', 'alias'}:
        blank['path'] = {'path': '../blank.py', 'absolute': str(root / 'blank.py'), 'alias': './blank.py'}[mutation]
    elif mutation in {'start', 'bool_start'}:
        blank['start_line'] = 2 if mutation == 'start' else True
    elif mutation in {'end', 'bool_end', 'float_end'}:
        blank['end'] = {'end': 2, 'bool_end': True, 'float_end': 1.0}[mutation]
    elif mutation in {'false_marker', 'numeric_marker'}:
        blank['complete_file'] = False if mutation == 'false_marker' else 1
    elif mutation == 'missing_marker':
        blank.pop('complete_file')
    elif mutation == 'null_content':
        blank['content'] = None
    else:
        raw = {'nonblank': b'X = 1\n', 'zero_bytes': b'', 'multiple_lines': b'\n\n',
               'changed_source': b'changed\n'}[mutation]
        (root / 'blank.py').write_bytes(raw)
        if mutation != 'changed_source':
            blank['sha256'] = files['blank.py'] = hashlib.sha256(raw).hexdigest()
    # A valid sibling cannot rescue a malformed empty read, in either order.
    assert observed(root, files, reads) == []
    assert observed(root, files, list(reversed(reads))) == []


def test_explicit_valid_end_and_existing_multiline_blank_behavior(bundle):
    root, files, reads = bundle
    reads[1]['end'] = 1
    assert observed(root, files, reads) == [{'path': 'settings.py', 'start': 1, 'end': 1}]
    (root / 'blank.py').write_bytes(b'\n\n')
    files, reads = actual_reads(root, ('settings.py', 'blank.py'))
    assert reads[1]['content'] == '\n'
    assert observed(root, files, reads) == verify_current_reads(reads, root, files)


def test_blank_read_does_not_bypass_root_link_checks(bundle):
    root, files, reads = bundle
    link = root.parent / (root.name + '-link')
    make_directory_link(link, root)
    assert observed(link, files, reads) == []


def with_blank_bundle(root, _quality):
    _, reads = actual_reads(root / 'workspaces/colony/gateway/v1', ('settings.py', 'blank.py'))
    def replace(events):
        target = next(e['item'] for e in events if e.get('item', {}).get('id') == 'observed-source')
        target['result']['content'][0]['text'] = json.dumps({'hit': True, 'status': 'validated',
                                                          'current_repository_reads': reads})
    rewrite_events(root, 'b', replace)


@pytest.mark.parametrize('raw', [b'\n', b'\r\n'])
def test_full_review_report_gives_both_arms_equal_intact_evidence_credit(run, raw):
    root, public, submission = build_reviewed_run(run, with_blank_bundle, extra_sources={'blank.py': raw})
    assert public[0]['observed_current_evidence'] == public[1]['observed_current_evidence']
    assert [(e['path'], e['content']) for e in public[0]['observed_current_evidence']] == [
        ('settings.py', 'TIMEOUT = 1500')]
    result = score(root, submission)
    assert result['quality_complete'] and result['tool_policy_compliant']
    assert all(row['answer_quality_successful'] for row in result['attempts'])
    assert result['arms']['colony']['total_usage']['gross_tokens'] == 100
    assert result['arms']['no_colony']['total_usage']['gross_tokens'] == 120
    assert not result['certifying']


@pytest.mark.parametrize('identity', ['a', 'b'])
def test_blank_read_cannot_support_a_grounding_pass_for_either_arm(run, identity):
    root, public, submission = build_reviewed_run(run, with_blank_bundle, extra_sources={'blank.py': b'\n'})
    private = json.loads((root / 'reviewed/private-map.json').read_text())
    case_id = next(row['case'] for row in private if row['id'] == identity)
    subject = next(row for row in public if row['case'] == case_id)
    blank = next(s for s in subject['current_sources'] if s['path'] == 'blank.py')
    for review in submission['reviews']:
        case = next(case for case in review['cases'] if case['case'] == case_id)
        case['findings'][1]['sources'] = [{'path': 'blank.py', 'sha256': blank['sha256'],
                                         'start': 1, 'end': 1, 'text': ''}]
    with pytest.raises(ValueError, match='not observed'):
        score(root, submission)
