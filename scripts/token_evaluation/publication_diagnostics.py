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
