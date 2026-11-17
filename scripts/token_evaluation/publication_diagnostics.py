"""Optional host metadata. Never input to semantic grading or performance acceptance."""
import hashlib
import json
from pathlib import Path


def observer_path():
    return Path(__file__).resolve().parents[1] / 'native_host_trace.py'


def trace_path(root, identity):
    return Path(root) / 'control' / (identity + '.publication.jsonl')


def _read(path):
    try:
        if path.is_symlink() or path.stat().st_size > 1 << 20:
            return None
        with path.open('rb') as stream:
            raw = stream.read((1 << 20) + 1)
        return raw if len(raw) <= 1 << 20 else None
    except OSError:
        return None


def trace_hash(path):
    raw = _read(path)
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def _partial_observation(lines):
    """Recover only observed metadata, never a completed-task or abstention claim."""
    schemas = {
        'tool_catalog': {'publish_present', 'after_notification'},
        'tools_list_changed': set(),
        'source_read': {'reference_returned'},
        'publication_attempt': set(),
        'publication_outcome': {'tool_error', 'stored', 'queued'},
    }
    if len(lines) > 256:
        raise ValueError('Too many metadata events')
    referenced = notified = refreshed = False
    attempts = outcomes = 0
    for line in lines:
        value = json.loads(line)
        if not isinstance(value, dict) or not isinstance(value.get('event'), str):
            raise ValueError('Invalid metadata event')
        event = value['event']
        keys = schemas.get(event)
        if (keys is None or set(value) != keys | {'event'}
                or any(type(value[k]) is not bool for k in keys)):
            raise ValueError('Invalid metadata fields')
        if event == 'source_read':
            referenced |= value['reference_returned']
        elif event == 'tools_list_changed':
            notified, refreshed = True, False
        elif event == 'tool_catalog':
            if value['after_notification'] and not notified:
                raise ValueError('Missing notification')
            refreshed |= value['after_notification'] and value['publish_present']
        elif event == 'publication_attempt':
            attempts += 1
        elif event == 'publication_outcome':
            outcomes += 1
            if outcomes > attempts:
                raise ValueError('Missing attempt')
    return {
        'status': 'partial_metadata', 'summary_present': False,
        'publication_activity': 'attempt_observed' if attempts else 'no_attempt_observed',
        'observed': {'referenced_read': referenced, 'refreshed_with_publish': refreshed,
                     'publication_attempts': attempts, 'publication_outcomes': outcomes},
    }


def observation(root, identity, expected):
    unavailable = {'status': 'unavailable', 'model_visible_catalog': 'unobserved',
                   'abstention_reason': 'unobserved'}
    path = trace_path(root, identity)
    raw = _read(path)
    if not expected or raw is None or hashlib.sha256(raw).hexdigest() != expected:
        return unavailable
    try:
        lines = raw.decode('utf-8').splitlines()
        if not 1 <= len(lines) <= 257:
            return unavailable
        value = json.loads(lines[-1])
        if isinstance(value, dict) and value.get('event') != 'summary':
            # An abrupt host shutdown can leave valid, hash-bound events without
            # a final summary. Unknown/malformed events fail closed. No source,
            # eligibility, pending-count or model-decision inference is made.
            return {**unavailable, **_partial_observation(lines), 'sha256': expected}
        statuses = {'not_eligible', 'no_referenced_read', 'ready_refresh_unobserved',
                    'refreshed_with_publish_no_attempt', 'attempted_with_receipt',
                    'attempted_outcome_unobserved'}
        if (not isinstance(value, dict) or value.get('event') != 'summary'
                or value.get('contract') != 'colony.host-publication-trace/1'
                or value.get('status') not in statuses or type(value.get('eligible')) is not bool
                or any(type(value.get(k)) is not int or value[k] < 0
                       for k in ('attempts', 'metadata_dropped', 'pending_requests'))
                or value.get('model_visible_catalog') != 'unobserved'
                or value.get('abstention_reason') != 'unobserved'):
            return unavailable
        # Never forward arbitrary diagnostic text into reports or reviewer packets.
        return {**unavailable, 'status': ('incomplete_metadata' if value['metadata_dropped']
                                         else value['status']),
                'attempts': value['attempts'], 'eligible': value['eligible'],
                'pending_requests': value['pending_requests'], 'sha256': expected}
    except (OSError, ValueError, TypeError, RecursionError):
        return unavailable
