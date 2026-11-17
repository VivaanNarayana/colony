"""Incomplete metadata is observation, never abstention or a quality verdict."""
import hashlib
import json

import pytest
from test_protocol import manifest
from test_report import run as run

from scripts.token_evaluation.protocol import validate_contract
from scripts.token_evaluation.publication_diagnostics import observation, trace_hash, trace_path


EVENTS = [
    {'event': 'tool_catalog', 'publish_present': False, 'after_notification': False},
    {'event': 'tools_list_changed'},
    {'event': 'source_read', 'reference_returned': True},
    {'event': 'tool_catalog', 'publish_present': True, 'after_notification': True},
]


def observe(tmp_path, events, *, suffix=''):
    path = trace_path(tmp_path, 'attempt')
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = ('\n'.join(json.dumps(e) for e in events) + suffix).encode()
    path.write_bytes(raw)
    before = path.stat().st_mtime_ns
    result = observation(tmp_path, 'attempt', trace_hash(path))
    assert path.read_bytes() == raw and path.stat().st_mtime_ns == before
    return result


def test_abrupt_shutdown_retains_only_observed_facts(tmp_path):
    result = observe(tmp_path, EVENTS, suffix='\n')
    assert result['status'] == 'partial_metadata' and not result['summary_present']
    assert result['publication_activity'] == 'no_attempt_observed'
    assert result['observed'] == {'referenced_read': True, 'refreshed_with_publish': True,
                                  'publication_attempts': 0, 'publication_outcomes': 0}
    assert result['model_visible_catalog'] == result['abstention_reason'] == 'unobserved'
    assert not {'eligible', 'pending_requests', 'quality', 'stored'} & result.keys()
    # Hash mismatch cannot turn captured metadata into trustworthy observations.
    assert observation(tmp_path, 'attempt', '0' * 64)['status'] == 'unavailable'


def test_partial_attempts_do_not_invent_outcomes_or_preserve_stale_refresh(tmp_path):
    events = EVENTS + [{'event': 'publication_attempt'}, {'event': 'tools_list_changed'}]
    result = observe(tmp_path, events)
    assert result['publication_activity'] == 'attempt_observed'
    assert result['observed']['publication_attempts'] == 1
    assert result['observed']['publication_outcomes'] == 0
    assert not result['observed']['refreshed_with_publish']
    result = observe(tmp_path, events + [
        {'event': 'publication_outcome', 'tool_error': False, 'stored': True, 'queued': False}])
    assert result['observed']['publication_outcomes'] == 1
    assert result['status'] == 'partial_metadata'  # Still not a complete task trace.


@pytest.mark.parametrize('extra', [
    {'event': 'source_read', 'reference_returned': 1},
    {'event': 'source_read', 'reference_returned': True, 'secret': 'PRIVATE'},
    {'event': 'abstained', 'reason': 'PRIVATE'},
    {'event': 'summary', 'status': 'PRIVATE'},
    {'event': ['source_read']}, None, 'PRIVATE',
])
def test_invalid_metadata_never_falls_back_to_an_abstention_or_echo(tmp_path, extra):
    result = observe(tmp_path, EVENTS + [extra])
    assert result['status'] == 'unavailable'
    assert 'PRIVATE' not in str(result) and result['abstention_reason'] == 'unobserved'


def test_event_order_bounds_and_truncation_fail_closed(tmp_path):
    for events in ([EVENTS[-1]], [
        {'event': 'publication_outcome', 'tool_error': False, 'stored': True, 'queued': False}],
        [EVENTS[0]] * 257):
        assert observe(tmp_path, events)['status'] == 'unavailable'
    assert observe(tmp_path, EVENTS, suffix='\n{"event":')['status'] == 'unavailable'
    path = trace_path(tmp_path, 'attempt')
    raw = b' ' * ((1 << 20) + 1)
    path.write_bytes(raw)
    assert observation(tmp_path, 'attempt', hashlib.sha256(raw).hexdigest())['status'] == 'unavailable'


def test_new_collector_does_not_reinterpret_historical_protocols(tmp_path):
    spec = manifest(tmp_path)
    assert spec['contract'] == 'colony.token-evaluation/17'
    for version in (15, 16):
        with pytest.raises(ValueError, match='original pinned collector'):
            validate_contract({**spec, 'contract': f'colony.token-evaluation/{version}'})


def test_partial_diagnostics_do_not_change_grades_usage_or_reviewer_packet(run, tmp_path, monkeypatch):
    from scripts.token_evaluation.report import mask, report

    root, _, _ = run
    original = report(root, root / 'quality.json', root / 'before.json')
    packet_before = (root / 'masked/answers.json').read_bytes()
    partial = observe(tmp_path / 'sidecar', EVENTS)
    monkeypatch.setattr('scripts.token_evaluation.publication_diagnostics.observation',
                        lambda *args: partial)
    current = report(root, root / 'quality.json', root / 'after.json')
    for result in (original, current):
        for attempt in result['attempts']:
            attempt.pop('publication_diagnostics')
    assert current == original
    mask(root, root / 'packet-after')
    def subjects(directory):
        identities = {entry['case']: entry['id'] for entry in json.loads(
            (directory / 'private-map.json').read_text())}
        return {identities[entry['case']]: {k: v for k, v in entry.items() if k != 'case'}
                for entry in json.loads((directory / 'answers.json').read_text())}
    assert subjects(root / 'packet-after') == subjects(root / 'masked')
    assert (root / 'masked/answers.json').read_bytes() == packet_before
