"""Verify task read pages against frozen bytes; handles never certify truth."""
import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, StrictInt, ValidationError

from .redaction import redacted_source_lines
from .research import Research


class _ReadArguments(BaseModel):
    """Frozen producer signature, independent of the installed target's code."""

    path: str | None = None
    start: int = 1
    end: int | None = None
    offset: StrictInt = 0


def arguments(item):
    value = item.get('arguments', {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return None
    if not isinstance(value, dict):
        return None
    value = value.copy()
    # FastMCP pre-parses non-scalar JSON strings for these non-str annotations
    # before Pydantic validates them (notably nullable path/end "null"). Keep
    # scalar strings intact: StrictInt offsets must never gain coercion here.
    for name in _ReadArguments.model_fields:
        if isinstance(value.get(name), str):
            try:
                decoded = json.loads(value[name])
            except RecursionError:
                return None
            except ValueError:
                continue
            if not isinstance(decoded, (str, int, float)):
                value[name] = decoded
    try:
        return _ReadArguments.model_validate(value)
    except ValidationError:
        return None


def verified_page(item, value, root, files, task, *, pages=None):
    """Credit fully delivered, unchanged lines only, never redacted or partial lines."""
    args = arguments(item)
    if args is None:
        return [], None
    path = args.path if args.path is not None else task.get('host_intent', {}).get('direct_path')
    start, end, offset = args.start, args.end, args.offset
    if (value.get('path') != path or type(start) is not int or start < 1
            or type(offset) is not int or offset < 0 or (end is not None and type(end) is not int)):
        return [], None
    try:
        research = Research(Path(root), files=files)
        source = research._path(path)
        canonical = source.relative_to(research.root).as_posix()
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != files[canonical]:
            return [], None
        rendered = redacted_source_lines(raw.decode('utf-8'))
        if rendered is None:
            return [], None
        lines, redacted = rendered
        end = len(lines) if end is None else end
        if (end < start and lines) or end > len(lines) or (not lines and (start != 1 or args.end is not None)):
            return [], None
        span = '\n'.join(lines[start - 1:end])
        if offset and offset >= len(span):
            return [], None
        content = span[offset:offset + 16000].encode('utf-8')[:16000].decode('utf-8', errors='ignore')
        stop = offset + len(content)
        complete = start == 1 and end == len(lines) and offset == 0 and stop == len(span)
        if (value.get('content') != content or type(value.get('start_line')) is not int
                or value['start_line'] != start or type(value.get('end_line')) is not int
                or value['end_line'] != end or value.get('complete_file') is not complete):
            return [], None
        if offset or stop < len(span):
            if (type(value.get('start_offset')) is not int or value['start_offset'] != offset
                    or type(value.get('end_offset')) is not int or value['end_offset'] != stop
                    or value.get('next_offset') != (stop if stop < len(span) else None)
                    or isinstance(value.get('next_offset'), bool)):
                return [], None
        elif any(k in value for k in ('start_offset', 'end_offset', 'next_offset')):
            return [], None
        intervals = [(offset, stop)]
        if pages is not None:
            key = (canonical, digest, start, end)
            intervals = [*pages.get(key, []), (offset, stop)]
            merged = []
            for begin, finish in sorted(intervals):
                if merged and begin <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], finish))
                else:
                    merged.append((begin, finish))
            pages[key] = intervals = merged
        spans, position = [], 0
        for n in range(start, end + 1):
            length = len(lines[n - 1])
            if (any(begin <= position and position + length <= finish for begin, finish in intervals)
                    and n - 1 not in redacted):
                spans.append({'path': canonical, 'start': n, 'end': n})
            position += length + 1
        return spans, {'ref': canonical, 'content_hash': digest}
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        return [], None


def lookup_value(value):
    """Normalize legacy task lookup evidence without trusting saved answer text."""
    if 'current_repository_reads' in value or value.get('status') != 'validated':
        return value
    sources = value.get('sources', [])
    if not isinstance(sources, list) or not isinstance(value.get('evidence'), list):
        return value
    hashes = {s.get('ref'): s.get('content_hash') for s in sources if isinstance(s, dict) and isinstance(s.get('ref'), str)}
    reads = [{'path': e.get('ref'), 'sha256': hashes.get(e.get('ref')),
              'start_line': e.get('line'), 'content': e.get('text'),
              'complete_file': e.get('complete_file', False)}
             for e in value['evidence'] if isinstance(e, dict) and isinstance(e.get('ref'), str)]
    return {**value, 'current_repository_reads': reads}


def handle(value):
    ref = value.get('evidence_ref')
    return ref if isinstance(ref, str) and re.fullmatch(r'[A-Za-z0-9_-]{24}', ref) else None
