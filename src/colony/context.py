"""Bounded, scrubbed excerpts of a trail's current, fingerprinted evidence."""

import hashlib
import re
from pathlib import Path

from colony.models import Source
from colony.retrieval import rank
from colony.security import PATTERNS
from colony.validation import repo_file


def source_lines(root: Path, source: Source) -> list[str]:
    if source.kind != "file":
        return []
    try:
        data = repo_file(root, source.ref).read_bytes()
        if hashlib.sha256(data).hexdigest() != source.content_hash:
            return []
        text = data.decode("utf-8")
    except (OSError, ValueError, UnicodeError):
        return []
    # Scrub before chunking; multiline secrets cannot straddle excerpt boundaries.
    # Preserve original line numbers even when replacing a private key block.
    for pattern in PATTERNS:
        text = re.sub(pattern, lambda m: "[REDACTED]" + "\n" * m[0].count("\n"), text)
    return text.splitlines()


def source_chunks(root: Path, sources: list[Source]) -> list[tuple[str, int, str]]:
    candidates = []
    # Only explicitly attached, matching files supply context. Never crawl uncited files.
    for source in sources[:8]:
        lines = source_lines(root, source)
        for start in range(0, min(len(lines), 2000), 8):
            block = "\n".join(lines[start : start + 16])
            if block.strip():
                candidates.append((source.ref, start + 1, block[:2400]))
            if len(candidates) >= 96:
                break
        if len(candidates) >= 96:
            break
    return candidates


def context_window(ref: str, lines: list[str], line: int) -> tuple[int, str]:
    """Retrieve narrowly; display the containing section or surrounding source lines."""
    center = min(line - 1 + 8, len(lines) - 1)
    low, high = 0, len(lines)
    if Path(ref).suffix.lower() in {".md", ".markdown"}:
        headings, fenced = [], False
        for i, text in enumerate(lines):
            if text.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            elif not fenced and re.match(r"^#{1,6} ", text):
                headings.append(i)
        low = max((i for i in headings if i <= center), default=0)
        high = min((i for i in headings if i > center), default=len(lines))
    if len("\n".join(lines[low:high])) > 2400:
        low = max(low, line - 1 - 8)
        high = min(high, low + 32)
    return low + 1, "\n".join(lines[low:high])[:2400]


def excerpts(
    root: Path,
    sources: list[Source],
    question: str,
    *,
    preferred: list[tuple[str, int]] | None = None,
) -> list[dict]:
    candidates = source_chunks(root, sources)
    if preferred is not None:
        # A second reranking must not replace the globally selected passage
        # with unrelated text from the same file.
        candidates = [c for c in candidates if (c[0], c[1]) in preferred]
        scores = [0.0] * len(candidates)
    else:
        scores = rank(question, [c[2] for c in candidates])
    selected, occupied, cache, budget = [], [], {}, 2800
    best = max(scores, default=0)
    by_ref = {s.ref: s for s in sources}
    for index in sorted(range(len(scores)), key=scores.__getitem__, reverse=True):
        ref, line, _ = candidates[index]
        if scores[index] < best - 2:
            continue
        if ref not in cache:
            cache[ref] = source_lines(root, by_ref[ref])
        if not cache[ref]:
            continue
        start, text = context_window(ref, cache[ref], line)
        end = start + text.count("\n")
        if any(r == ref and start <= b and end >= a for r, a, b in occupied):
            continue
        if len(selected) >= 2 or budget < 300:
            break
        text = text[:budget]
        selected.append({"ref": ref, "line": start, "text": text})
        occupied.append((ref, start, end))
        budget -= len(text)
    return selected
