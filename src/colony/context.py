"""Bounded, scrubbed excerpts of a trail's current, fingerprinted evidence."""

import hashlib
import re
from pathlib import Path

from colony.models import Source
from colony.retrieval import rank
from colony.security import PATTERNS
from colony.validation import repo_file


def excerpts(root: Path, sources: list[Source], question: str) -> list[dict]:
    candidates = []
    # Only files explicitly attached to this scoped trail can supply context. Never search
    # the checkout on a miss, follow links, or expose unrelated repository content.
    for source in sources[:8]:
        if source.kind != "file":
            continue
        try:
            data = repo_file(root, source.ref).read_bytes()
            if hashlib.sha256(data).hexdigest() != source.content_hash:
                continue
            text = data.decode("utf-8")
        except (OSError, ValueError, UnicodeError):
            continue
        # Scrub the entire file before chunking so multiline secrets cannot straddle
        # excerpt boundaries. Keep original line numbers for useful citations.
        for pattern in PATTERNS:
            text = re.sub(pattern, lambda m: "[REDACTED]" + "\n" * m[0].count("\n"), text)
        lines = text.splitlines()
        for start in range(0, min(len(lines), 2000), 16):
            block = "\n".join(lines[start : start + 24])
            if block.strip():
                candidates.append((source.ref, start + 1, block[:2400]))
            if len(candidates) >= 96:
                break
        if len(candidates) >= 96:
            break
    scores = rank(question, [c[2] for c in candidates])
    selected, budget = [], 4800
    for index in sorted(range(len(scores)), key=scores.__getitem__, reverse=True):
        ref, start, text = candidates[index]
        # Avoid spending the response on overlapping windows from the same file.
        if any(s["ref"] == ref and abs(s["line"] - start) < 24 for s in selected):
            continue
        if len(selected) >= 3 or budget < 300:
            break
        text = text[:budget]
        selected.append({"ref": ref, "line": start, "text": text})
        budget -= len(text)
    return selected
