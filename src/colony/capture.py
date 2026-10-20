"""Redact secret spans with full output context, retaining a bounded capture tail."""

import mmap
import os
import re
from typing import BinaryIO

from colony.security import PATTERNS, scrub


def scrubbed_tail(output: BinaryIO, limit: int = 90000) -> tuple[str, bool]:
    if limit <= 0:
        raise ValueError("Capture limit must be positive")
    output.flush()
    size = os.fstat(output.fileno()).st_size
    if not size:
        return "", False
    cutoff = max(0, size - limit)
    spans = []
    # Scan the full spool without copying it into a Python string. A secret's
    # opener can precede the tail, so matching only the retained bytes is unsafe.
    # Only spans intersecting the bounded tail are retained in memory.
    with mmap.mmap(output.fileno(), 0, access=mmap.ACCESS_READ) as data:
        for pattern in PATTERNS:
            for match in re.finditer(pattern.encode("ascii"), data):
                start, end = match.span()
                if end > cutoff:
                    spans.append((max(start, cutoff) - cutoff, end - cutoff))
        tail = data[cutoff:]
    # Union overlapping matches from different patterns before replacing them.
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    parts, previous = [], 0
    for start, end in merged:
        parts.extend((tail[previous:start], b"[REDACTED]"))
        previous = end
    parts.append(tail[previous:])
    redacted = b"".join(parts)[-limit:].decode("utf-8", errors="replace")
    # Retain the normal Unicode-aware scrub pass too, before client sanitization.
    return scrub(redacted), size > limit
