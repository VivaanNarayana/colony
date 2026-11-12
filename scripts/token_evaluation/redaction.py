"""Frozen source-rendering rules, hashed with the evaluator rather than imported from a target.

This recognizes Colony's line-preserving source scrubber. It does not authorize
arbitrary markers, certify a saved answer, or make redacted text observed evidence.
Actual-producer parity tests guard this copy of the supported rendering contract.
"""
import re
from bisect import bisect_right

PATTERNS = (
    r"-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----[\s\S]*?(?:-----END (?:[A-Z ]*PRIVATE KEY)-----|\Z)",
    r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqps?)://[^\s\"'<>]+",
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b",
    r"(?i)\b(?:bearer)\s+[A-Za-z0-9._~+/=-]{8,}",
    r"(?i)(?:[\"']?\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|client[_-]?secret)[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
)
LINE_BREAK = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


def redacted_source_lines(text):
    """Return the exact rendered lines and touched zero-based lines, or no safe mapping."""
    touched = set()
    for pattern in PATTERNS:
        offsets, offset = [], 0
        for line in text.splitlines(keepends=True):
            offsets.append(offset)
            offset += len(line)
        mapping_safe = True

        def replace(match, offsets=offsets):
            nonlocal mapping_safe
            # The producer preserves LF count. Other line boundaries inside a
            # match can collapse; refuse rather than map evidence to shifted lines.
            if len(LINE_BREAK.findall(match[0])) != match[0].count('\n'):
                mapping_safe = False
            first = bisect_right(offsets, match.start()) - 1
            last = bisect_right(offsets, match.end() - 1) - 1
            touched.update(range(first, last + 1))
            return '[REDACTED]' + '\n' * match[0].count('\n')

        text = re.sub(pattern, replace, text)
        if not mapping_safe:
            return None
    return text.splitlines(), touched
