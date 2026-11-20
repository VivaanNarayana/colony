"""Coordinates of cited evidence. No authored answer or source body is persisted."""

import hashlib
import re
import time
from collections import defaultdict
from itertools import groupby
from pathlib import Path
from typing import Annotated

from pydantic import Field, StrictInt, model_validator

from colony.models import MemoryAvailability, Model
from colony.security import PATTERNS, scrub
from colony.validation import repo_contents

MAX_RANGES = 32
MAX_LINES = 1024
Line = Annotated[StrictInt, Field(ge=1, le=2_000_000)]


class Citation(Model):
    path: str = Field(min_length=1, max_length=1024)
    start: Line
    end: Line

    @model_validator(mode="after")
    def bounded(self):
        if not 1 <= self.end - self.start + 1 <= MAX_LINES:
            raise ValueError("Invalid citation range")
        return self


class CitedSpan(Citation):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CitationQuery(MemoryAvailability):
    question: str = Field(min_length=1, max_length=2000)


class BundleMatch(Model):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    spans: list[CitedSpan] = Field(min_length=1, max_length=MAX_RANGES)


class CitationBundle(CitationQuery):
    spans: list[CitedSpan] = Field(min_length=1, max_length=MAX_RANGES)


def source_lines(data: bytes) -> list[str]:
    text = data.decode("utf-8")
    for pattern in PATTERNS:
        text = re.sub(pattern, lambda m: "[REDACTED]" + "\n" * m[0].count("\n"), text)
    return text.splitlines()


def final_citations(sources) -> list[Citation]:
    """Accept common host citation shapes. A path alone identifies no observed line."""
    if not isinstance(sources, list) or len(sources) > MAX_RANGES * 4:
        return []
    result = []
    for item in sources:
        if isinstance(item, str):
            path, _, lines = item.rpartition(":")
        elif isinstance(item, dict):
            path, lines = item.get("path"), item.get("lines")
        else:
            continue
        span = re.fullmatch(r"L?(\d{1,7})(?:\s*[-–]\s*L?(\d{1,7}))?", lines) if isinstance(lines, str) else None
        if span:
            start, end = int(span[1]), int(span[2] or span[1])
        elif isinstance(item, dict):
            start, end = item.get("start", item.get("start_line")), item.get("end", item.get("end_line"))
            if type(lines) is int:
                start = end = lines
            elif isinstance(lines, list) and len(lines) == 2:
                start, end = lines
        else:
            continue
        try:
            result.append(Citation(path=path, start=start, end=end))
        except ValueError:
            continue
    return result


def observed_spans(repo, citations, observations, path_allowed):
    """Intersect citations with actually delivered characters of the same current bytes."""
    spans = []
    for path, group in groupby(sorted(citations, key=lambda c: c.path), lambda c: c.path):
        observed = [o for o in observations if o.ref == path and o.expires > time.monotonic()]
        if not observed or scrub(path) != path:
            continue
        try:
            target, raw = repo_contents(repo, path, path_allowed=path_allowed)
            digest = hashlib.sha256(raw).hexdigest()
            lines = source_lines(raw)
        except (OSError, ValueError):
            continue
        starts = [0]
        for line in lines:
            starts.append(starts[-1] + len(line) + 1)
        intervals = []
        for o in observed:
            if o.digest == digest and (o.target is None or o.target == str(target)):
                origin = starts[o.start - 1]
                intervals.append((origin + o.start_offset, origin + o.end_offset))
        merged = []
        for left, right in sorted(intervals):
            if merged and left <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], right))
            else:
                merged.append((left, right))
        wanted = {n for c in group for n in range(c.start, min(len(lines), c.end) + 1)}
        present = [n for n in sorted(wanted) if "[REDACTED]" not in lines[n - 1]
                   and any(a <= starts[n - 1] and starts[n] - 1 <= b for a, b in merged)]
        # Preserve observed blank lines inside citations, but never bridge unread characters.
        for _, run in groupby(enumerate(present), lambda x: x[1] - x[0]):
            numbers = [n for _, n in run]
            for index in range(0, len(numbers), MAX_LINES):
                chunk = numbers[index:index + MAX_LINES]
                if any(lines[n - 1].strip() for n in chunk):
                    spans.append(CitedSpan(path=path, start=chunk[0], end=chunk[-1], sha256=digest))
    return spans if len(spans) <= MAX_RANGES else []


def current_reads(repo: Path, spans, *, path_allowed=None):
    grouped = defaultdict(list)
    for span in spans:
        grouped[span.path].append(span)
    reads, sources = [], []
    size = 0
    for path, ranges in grouped.items():
        _, raw = repo_contents(repo, path, path_allowed=path_allowed or _allow_repo_path)
        digest = hashlib.sha256(raw).hexdigest()
        lines = source_lines(raw)
        sources.append({"ref": path, "content_hash": digest})
        for span in ranges:
            if span.sha256 != digest or span.end > len(lines):
                raise ValueError("Cited evidence changed")
            selected = lines[span.start - 1:span.end]
            content = "\n".join(selected)
            size += len(content.encode())
            if not content.strip() or "[REDACTED]" in content or size > 16000:
                raise ValueError("Cited evidence unavailable")
            reads.append({"path": path, "sha256": digest, "start_line": span.start,
                          "end_line": span.end, "content": content,
                          "complete_file": span.start == 1 and span.end == len(lines)})
    return sources, reads


def _allow_repo_path(ref):
    return True


SCHEMA = """
CREATE TABLE IF NOT EXISTS cited_bundles_v1 (
 id TEXT PRIMARY KEY, namespace TEXT NOT NULL, repo_id TEXT NOT NULL, created REAL NOT NULL,
 model TEXT NOT NULL, embedding BLOB NOT NULL, payload BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS cited_bundles_scope ON cited_bundles_v1(namespace,repo_id,created);
"""


def save_bundle(store, bundle, producer):
    from colony.retrieval import embed, index_version

    bundle = bundle.model_copy(update={"question": scrub(bundle.question)})
    if any(scrub(s.path) != s.path for s in bundle.spans):
        raise ValueError("Invalid cited source")
    current_reads(store.repo, bundle.spans)
    payload = bundle.model_dump_json().encode()
    identity = hashlib.sha256(producer.encode() + b"\0" + payload).hexdigest()[:32]
    vector, model = embed(bundle.question), index_version()
    with store.connect() as db:
        db.execute("INSERT INTO cited_bundles_v1 VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                   "created=excluded.created, model=excluded.model, embedding=excluded.embedding",
                   (identity, bundle.namespace, bundle.repository_id or "", time.time(), model, vector,
                    store.cipher.encrypt(payload)))
    return {"stored": True, "id": identity}


def lookup_bundle(store, query, max_age, threshold):
    from colony.retrieval import embed, index_version, rank

    question = scrub(query.question)
    with store.connect() as db:
        rows = db.execute(
            "SELECT id,payload,1-vec_distance_cosine(embedding,?) AS similarity "
            "FROM cited_bundles_v1 WHERE namespace=? AND repo_id=? AND model=? AND created>=? "
            "ORDER BY similarity DESC,created DESC,id LIMIT 12",
            (embed(question, query=True), query.namespace, query.repository_id or "",
             index_version(), time.time() - max_age)).fetchall()
    candidates = [(row, CitationBundle.model_validate_json(store.cipher.decrypt(row["payload"])))
                  for row in rows]
    # Questions retrieve source coordinates. Apply the configured similarity floor
    # and reject weak reranker matches as in the existing evidence fallback.
    eligible = [(row, b) for row, b in candidates
                if b.question == question or row["similarity"] >= max(0.55, threshold)]
    logits = rank(question, [b.question for _, b in eligible])
    ordered = sorted(zip(eligible, logits, strict=True),
                     key=lambda entry: (entry[0][1].question == question, entry[1]), reverse=True)
    for (row, bundle), logit in ordered:
        if bundle.question != question and logit < -5.0:
            continue
        try:
            current_reads(store.repo, bundle.spans)
        except (OSError, ValueError):
            continue
        return {"id": row["id"], "spans": [s.model_dump() for s in bundle.spans]}
    return None
