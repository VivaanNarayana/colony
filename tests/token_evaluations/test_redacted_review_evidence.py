"""Real rendering and offline synthetic reports; no model evaluations or real secrets."""
import copy
import hashlib
import json

import pytest

from colony.context import excerpts, source_lines
from colony.models import Source
from colony.security import PATTERNS as PRODUCER_PATTERNS
from scripts.token_evaluation.evidence import verify_current_reads
from scripts.token_evaluation.protocol import collector_hashes
from scripts.token_evaluation.redaction import PATTERNS, redacted_source_lines
from tests.token_evaluations.test_agent_quality import build_reviewed_run, score
from tests.token_evaluations.test_path_alias_evidence import make_directory_link
from tests.token_evaluations.test_report import rewrite_events
from tests.token_evaluations.test_report import run as run

ASSIGNMENT = 'PASSWORD = "synthetic-nonsecret"'
KEY = '-----BEGIN PRIVATE KEY-----\nfake-data\n\n-----END PRIVATE KEY-----'
RAW = ('TIMEOUT = 1500\n' + ASSIGNMENT + '\n').encode()


def actual_reads(root, names=('settings.py',), *, whole=True):
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}
    sources = [Source(kind='file', ref=name, content_hash=sha) for name, sha in files.items()]
    values = excerpts(root, sources, 'What is TIMEOUT?', whole_files=whole,
                      preferred=[(name, 1) for name in names])
    return files, [{'path': e['ref'], 'sha256': files[e['ref']], 'start_line': e['line'],
                    'complete_file': e['complete_file'], 'content': e['text']} for e in values]


def verify(root, files, reads):
    return verify_current_reads(reads, root, files, allow_clipped_tail=True, allow_redaction=True)


def test_rendering_rules_are_collector_pinned_and_match_supported_producer():
    assert tuple(PRODUCER_PATTERNS) == PATTERNS
    assert any(path.endswith('redaction.py') for path in collector_hashes())


@pytest.mark.parametrize('secret', [ASSIGNMENT, 'postgres://synthetic:fake@localhost/db',
    'sk-' + 'x' * 20, 'Bearer ' + 'x' * 20, KEY])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_actual_rendering_keeps_prefix_and_suffix_but_never_redacted_lines(tmp_path, secret, newline):
    text = 'TIMEOUT = 1500\n' + secret + '\nRETRIES = 3\n'
    raw = text.replace('\n', newline).encode()
    (tmp_path / 'settings.py').write_bytes(raw)
    files, reads = actual_reads(tmp_path)
    source = Source(kind='file', ref='settings.py', content_hash=files['settings.py'])
    rendered, touched = redacted_source_lines(raw.decode())
    assert rendered == source_lines(tmp_path, source)
    last = len(raw.decode().splitlines())
    assert touched == set(range(1, last - 1))
    assert verify(tmp_path, files, reads) == [
        {'path': 'settings.py', 'start': 1, 'end': 1},
        {'path': 'settings.py', 'start': last, 'end': last}]
    # The default proof verifier remains raw-source strict.
    assert verify_current_reads(reads, tmp_path, files) == []


@pytest.mark.parametrize('ending', ['', '\n'])
def test_multiline_redaction_at_eof_keeps_only_intact_prefix(tmp_path, ending):
    raw = ('TIMEOUT = 1500\n' + KEY + ending).encode()
    (tmp_path / 'settings.py').write_bytes(raw)
    files, reads = actual_reads(tmp_path)
    assert reads[0]['complete_file'] is True
    # splitlines can remove the final empty placeholder; no following raw line
    # is invented and full-file means the complete supported rendered view.
    assert verify(tmp_path, files, reads) == [{'path': 'settings.py', 'start': 1, 'end': 1}]


@pytest.mark.parametrize('separator', ['\r', '\u2028', '\x85'])
def test_shifted_source_line_mapping_fails_closed(tmp_path, separator):
    raw = ('TIMEOUT = 1500\n' + KEY.replace('\n', separator) + '\nRETRIES = 3\n').encode()
    (tmp_path / 'settings.py').write_bytes(raw)
    files, reads = actual_reads(tmp_path)
    assert redacted_source_lines(raw.decode()) is None
    assert verify(tmp_path, files, reads) == []


def test_redaction_only_file_does_not_discard_intact_other_file(tmp_path):
    (tmp_path / 'settings.py').write_text('TIMEOUT = 1500\n', encoding='utf-8')
    (tmp_path / 'other.py').write_text(ASSIGNMENT + '\n', encoding='utf-8')
    files, reads = actual_reads(tmp_path, ('settings.py', 'other.py'))
    assert len(reads) == 2
    assert verify(tmp_path, files, reads) == [{'path': 'settings.py', 'start': 1, 'end': 1}]
    assert verify(tmp_path, files, reads[1:]) == []


@pytest.mark.parametrize('whole', [False, True])
def test_redaction_combined_with_real_character_clipping(tmp_path, whole):
    (tmp_path / 'settings.py').write_bytes(RAW + b'# ' + b'x' * 20000 + b'\nUNSEEN = 7\n')
    files, reads = actual_reads(tmp_path, whole=whole)
    assert len(reads[0]['content']) == 2400 and reads[0]['complete_file'] is False
    assert '[REDACTED]' in reads[0]['content']
    assert verify(tmp_path, files, reads) == [{'path': 'settings.py', 'start': 1, 'end': 1}]
    reads[0]['content'] = reads[0]['content'][:-1] + 'z'
    assert verify(tmp_path, files, reads) == []


def test_literal_marker_and_actual_raw_delivery_still_receive_credit(tmp_path):
    raw = b'TIMEOUT = 1500\n[REDACTED]\n'
    (tmp_path / 'settings.py').write_bytes(raw)
    files, reads = actual_reads(tmp_path)
    assert verify(tmp_path, files, reads) == [{'path': 'settings.py', 'start': 1, 'end': 2}]
    (tmp_path / 'settings.py').write_bytes(RAW)
    files, reads = actual_reads(tmp_path)
    reads[0]['content'] = RAW.decode().rstrip('\n')
    # Actual raw bytes can be observed too; the scrubber cannot erase them.
    assert verify(tmp_path, files, reads) == [{'path': 'settings.py', 'start': 1, 'end': 2}]


@pytest.mark.parametrize('mutation', ['prefix', 'marker', 'missing_line', 'extra_line', 'hash', 'path', 'start', 'end',
                                     'partial_full_file', 'mixed_representations', 'changed_source'])
def test_redaction_cannot_salvage_forged_or_stale_bundle(tmp_path, mutation):
    raw = RAW + b'RETRIES = 3\nAPI_KEY = "another-synthetic-value"\n'
    (tmp_path / 'settings.py').write_bytes(raw)
    files, reads = actual_reads(tmp_path)
    good = {**copy.deepcopy(reads[0]), 'content': 'TIMEOUT = 1500', 'complete_file': False}
    bad = reads[0]
    if mutation == 'prefix':
        bad['content'] = bad['content'].replace('1500', '9000')
    elif mutation == 'marker':
        bad['content'] = bad['content'].replace('[REDACTED]', '[HIDDEN]')
    elif mutation == 'missing_line':
        bad['content'] = bad['content'].replace('[REDACTED]\n', '', 1)
    elif mutation == 'extra_line':
        bad['content'] += '\nINVENTED = 1'
    elif mutation == 'hash':
        bad['sha256'] = '0' * 64
    elif mutation == 'path':
        bad['path'] = '../settings.py'
    elif mutation == 'start':
        bad['start_line'] = 2
    elif mutation == 'end':
        bad['end'] = 99
    elif mutation == 'partial_full_file':
        bad['content'] = 'TIMEOUT = 1500\n[REDACTED]'
    elif mutation == 'mixed_representations':
        bad['content'] = bad['content'].replace('[REDACTED]', ASSIGNMENT, 1)
    else:
        (tmp_path / 'settings.py').write_bytes(raw.replace(b'1500', b'9000'))
    assert verify(tmp_path, files, [good, bad]) == []


def test_redacted_reads_cannot_bypass_link_checks(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'settings.py').write_bytes(RAW)
    files, reads = actual_reads(root)
    link = tmp_path / 'linked'
    make_directory_link(link, root)
    assert verify(link, files, reads) == []


def replace_with_redacted(root, _quality):
    _, reads = actual_reads(root / 'workspaces/colony/gateway/v1')
    def replace(events):
        target = next(e['item'] for e in events if e.get('item', {}).get('id') == 'observed-source')
        target['result']['content'][0]['text'] = json.dumps({'hit': True, 'status': 'validated',
                                                          'current_repository_reads': reads})
    rewrite_events(root, 'b', replace)


def test_full_review_and_report_credit_equivalent_intact_evidence(run):
    root, public, submission = build_reviewed_run(run, replace_with_redacted, source_bytes=RAW)
    assert public[0]['observed_current_evidence'] == public[1]['observed_current_evidence']
    assert [e['content'] for e in public[0]['observed_current_evidence']] == ['TIMEOUT = 1500']
    value = score(root, submission)
    assert value['quality_complete'] and value['tool_policy_compliant']
    assert all(row['answer_quality_successful'] for row in value['attempts'])
    assert value['arms']['colony']['total_usage']['gross_tokens'] == 100
    assert value['arms']['no_colony']['total_usage']['gross_tokens'] == 120
    assert not value['certifying']


@pytest.mark.parametrize('identity', ['a', 'b'])
def test_unobserved_redacted_source_cannot_support_either_arms_grounding(run, identity):
    root, public, submission = build_reviewed_run(run, replace_with_redacted, source_bytes=RAW)
    private = json.loads((root / 'reviewed/private-map.json').read_text())
    case_id = next(row['case'] for row in private if row['id'] == identity)
    source = next(row for row in public if row['case'] == case_id)['current_sources'][0]
    for review in submission['reviews']:
        case = next(case for case in review['cases'] if case['case'] == case_id)
        case['findings'][1]['sources'] = [{'path': source['path'], 'sha256': source['sha256'],
            'start': 2, 'end': 2, 'text': source['content'].splitlines()[1]}]
    with pytest.raises(ValueError, match='not observed'):
        score(root, submission)
