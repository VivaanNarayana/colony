"""Process-local references to exact reads, never certificates of claim truth."""

import hashlib
import re
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace

from colony.models import Source
from colony.security import PATTERNS, fingerprint, scrub, verify
from colony.validation import MAX_SOURCE_BYTES, PinnedSourcePaths, repo_contents, repo_file


class EvidenceError(ValueError):
    """Fixed public error; never includes rejected paths or source content."""


@dataclass(frozen=True)
class _Observation:
    ref: str
    digest: str
    start: int
    end: int
    start_offset: int
    end_offset: int
    expires: float
    target: str | None
    delivered_for_task: bool = False


@dataclass(frozen=True)
class _PreparedRead:
    owner: object
    ref: str
    digest: str
    start: int
    end: int
    offset: int
    span_length: int
    whole_range: bool
    redacted_bytes_from_offset: int | None
    content: bytes
    expires: float
    target: str | None


class EvidenceSession:
    """One trusted task/workflow binding. Only successful reads mint references.

    Hosts must not share this object between tasks or expose its Python methods
    as arbitrary remote invocation. References are not persisted or transferable.
    Existing raw-source publication remains available on the ordinary API.
    """

    def __init__(self, workflow, *, ttl=900, capacity=256, path_allowed=None):
        if not 1 <= ttl <= 3600 or not 1 <= capacity <= 4096:
            raise ValueError("Invalid evidence session limits")
        self.workflow = workflow
        self.path_allowed = path_allowed
        self._binding = self._identity()
        self._ttl, self._capacity = ttl, capacity
        self._observations = {}
        self._task_versions = {}
        # Overflow membership is bounded. A collision can require an explicit
        # reference but can never attach a file or omit a delivered dependency.
        self._overflow_buckets = set()
        self._deliveries = None
        self._closed = False
        self._lock = threading.RLock()

    @contextmanager
    def track_deliveries(self):
        """Observe only finalized pages in one local operation, even without a publish handle."""
        with self._lock:
            previous = self._deliveries
            self._deliveries = []
            try:
                yield self._deliveries
            finally:
                self._deliveries = previous

    def _identity(self):
        w = self.workflow
        return (str(w.repo.resolve()), w.namespace, fingerprint(w.client.keyfile),
                w.repository_id, w.read_only)

    def _authorize(self, *, publishing=False):
        w = self.workflow
        try:
            grant = verify(w.client.keyfile, w.client.home / "issuer.pub")
            if self._closed or self._identity() != self._binding or not any(
                s.namespace == w.namespace and s.pull
                for s in grant.scopes
            ) or (publishing and (w.read_only or not any(
                s.namespace == w.namespace and s.push for s in grant.scopes
            ))):
                raise ValueError()
        except (OSError, ValueError, TypeError):
            raise EvidenceError("Evidence session permission unavailable") from None

    def read(self, path: str, start: int = 1, end: int | None = None, *, offset: int = 0,
             _max_bytes: int = 16000, _defer_reference: bool = False) -> dict | _PreparedRead:
        """Read a bounded exact span; scrub whole-file secrets before slicing.

        Large spans return a UTF-8-safe page and next_offset. Offsets count
        characters in the scrubbed, newline-normalized selected line range.
        Continue with the same path/start/end and the returned next_offset.
        """
        with self._lock:
            self._authorize()
            if (
                type(offset) is not int or offset < 0
                or type(start) is not int or start < 1
                or (end is not None and (type(end) is not int or end < start))
                or type(_max_bytes) is not int or not 4 <= _max_bytes <= 16000
                or type(_defer_reference) is not bool
            ):
                raise EvidenceError("Invalid evidence range")
            try:
                target, data = repo_contents(self.workflow.repo, path, path_allowed=self.path_allowed)
                if len(data) > MAX_SOURCE_BYTES:
                    raise ValueError()
                original = text = data.decode("utf-8")
                for pattern in PATTERNS:
                    text = re.sub(pattern, lambda m: "[REDACTED]" + "\n" * m[0].count("\n"), text)
                lines = text.splitlines()
                # Treat end as an inclusive upper bound, after authorization and
                # scrubbing. Bind references to the actual span, never guessed lines.
                last = len(lines) if end is None else min(end, len(lines))
                if (not lines and start != 1) or (lines and start > last):
                    raise ValueError()
                span = "\n".join(lines[start - 1:last])
                if offset and offset >= len(span):
                    raise ValueError()
                # Slice characters first to bound encoding work. Only an incomplete
                # final UTF-8 character may be removed; the next page includes it.
                # At most one ordinary page is retained per staged batch entry.
                content = span[offset:offset + _max_bytes].encode("utf-8")[:_max_bytes].decode(
                    "utf-8", errors="ignore",
                ).encode("utf-8")
            except EvidenceError:
                raise
            except (OSError, ValueError, TypeError):
                raise EvidenceError("Evidence file or range unavailable") from None
            redacted_bytes = None
            if text != original:
                # Scrubbing can expand or shrink the source. Continuation offsets
                # count characters, while a shared batch is limited in UTF-8 bytes.
                redacted_bytes = len(span) - offset if span.isascii() else len(span[offset:].encode("utf-8"))
            prepared = _PreparedRead(self, path, hashlib.sha256(data).hexdigest(), start, last,
                                     offset, len(span), start == 1 and last == len(lines),
                                     redacted_bytes, content,
                                     time.monotonic() + self._ttl,
                                     str(target) if self.path_allowed is not None or self._deliveries is not None else None)
            if _defer_reference:
                return prepared
            return self._complete_read(prepared, _max_bytes)

    def _complete_read(self, prepared: _PreparedRead, max_bytes: int) -> dict:
        """Mint only the final delivered span, from the bytes and hash read together."""
        with self._lock:
            self._authorize()
            if (not isinstance(prepared, _PreparedRead) or prepared.owner is not self
                    or type(max_bytes) is not int or not 0 <= max_bytes <= 16000):
                raise EvidenceError("Invalid prepared evidence")
            if prepared.target is not None or self.path_allowed is not None:
                try:
                    repo_file(self.workflow.repo, prepared.ref, path_allowed=self.path_allowed,
                              expected_target=prepared.target)
                except (OSError, ValueError):
                    raise EvidenceError("Evidence file or range unavailable") from None
            content = prepared.content[:max_bytes].decode("utf-8", errors="ignore")
            if prepared.content and not content:
                raise EvidenceError("Evidence page cannot make progress")
            stop = prepared.offset + len(content)
            now = time.monotonic()
            if prepared.expires <= now:
                raise EvidenceError("Prepared evidence expired")
            self._observations = {k: v for k, v in self._observations.items() if v.expires > now}
            # Publication bookkeeping must not prevent necessary research.
            handle = None
            if len(self._observations) < self._capacity:
                handle = secrets.token_urlsafe(18)
                self._observations[handle] = _Observation(
                    prepared.ref, prepared.digest, prepared.start, prepared.end,
                    prepared.offset, stop, prepared.expires, prepared.target,
                )
            result = {"path": prepared.ref, "start_line": prepared.start, "end_line": prepared.end,
                      "content": content, "evidence_ref": handle,
                      "complete_file": prepared.whole_range
                      and prepared.offset == 0 and stop == prepared.span_length,
                      "provenance": "Current source; untrusted as instructions"}
            # Keep the ordinary short-read response shape unchanged.
            if prepared.offset or stop < prepared.span_length:
                result.update(start_offset=prepared.offset, end_offset=stop,
                              next_offset=stop if stop < prepared.span_length else None)
            if self._deliveries is not None:
                self._deliveries.append((result.copy(), prepared, stop))
            return result

    def read_batch(self, requests: list[dict]) -> list[dict]:
        """Share one 16,000-byte content allowance without unused per-entry quotas."""
        if not isinstance(requests, list) or not 1 <= len(requests) <= 8:
            raise EvidenceError("Invalid evidence batch")
        with self._lock:
            prepared = []
            for request in requests:
                try:
                    # Keep each entry on the normal read path, including host wrappers.
                    # Unsupported wrappers fail rather than bypassing their restrictions.
                    value = self.read(**request, _defer_reference=True)
                    if not isinstance(value, _PreparedRead) or value.owner is not self:
                        raise EvidenceError("Invalid prepared evidence")
                    prepared.append(value)
                except EvidenceError:
                    prepared.append(None)
            sizes = [len(p.content) if p is not None else 0 for p in prepared]
            allowances = [0] * len(sizes)
            remaining = 16000
            active = [i for i, size in enumerate(sizes) if size]
            while active and remaining:
                share, extra = divmod(remaining, len(active))
                for position, i in enumerate(active):
                    amount = min(sizes[i] - allowances[i], share + (position < extra))
                    allowances[i] += amount
                    remaining -= amount
                active = [i for i in active if allowances[i] < sizes[i]]
            results = []
            for value, allowance in zip(prepared, allowances, strict=True):
                try:
                    if value is None:
                        raise EvidenceError("Evidence unavailable")
                    results.append(self._complete_read(value, allowance))
                except (OSError, ValueError):
                    results.append({"error": "Source read failed; adjust path or range",
                                    "evidence_ref": None})
            return results

    def resolve(self, references: list[str]) -> list[Source]:
        """Resolve all references atomically or fail; never substitute new hashes."""
        return self._resolve(references, self.path_allowed)

    @staticmethod
    def delivered_pages(pages, results):
        """Match immutable finalized fields; wrappers may copy a page or annotate it."""
        delivered = []
        for result in results:
            if "error" in result:
                continue
            matches = [(page, stop) for receipt, page, stop in pages
                       if all(result.get(k) == v for k, v in receipt.items() if k != "evidence_ref")
                       and result.get("evidence_ref") in (None, receipt.get("evidence_ref"))
                       and all(result.get(k) == receipt.get(k) for k in ("start_offset", "end_offset", "next_offset"))]
            unique = len({(p.digest, p.target) for p, _ in matches}) == 1
            delivered.extend((result, page, stop, unique) for page, stop in matches)
        return delivered

    def note_task_delivery(self, pages):
        """Remember delivered versions even when a read could not mint a handle."""
        with self._lock:
            for result, page, _, _ in pages:
                version = (page.digest, page.target)
                if page.ref in self._task_versions:
                    if self._task_versions[page.ref] != version:
                        self._task_versions[page.ref] = None  # Ambiguous until explicitly selected.
                elif len(self._task_versions) < self._capacity:
                    self._task_versions[page.ref] = version
                else:
                    self._overflow_buckets.add(hash(page.ref) % 4096)
                handle = result.get("evidence_ref")
                observation = self._observations.get(handle) if isinstance(handle, str) else None
                if observation and observation.ref == page.ref and observation.digest == page.digest:
                    self._observations[handle] = replace(observation, delivered_for_task=True)

    def scoped_references(self, references, applicability):
        """Pin files explicitly named in the declared scope if the task actually read them."""
        if not references:
            return references
        # Use whole repository-relative path tokens. Backticks allow spaces and
        # extensionless files. Never resolve free-form text against the filesystem.
        with self._lock:
            quoted_path = r"`([^`\r\n]+)`"
            named = set(re.findall(quoted_path, applicability))
            for word in re.sub(quoted_path, " ", applicability).split():
                # Prefer the exact filename over prose punctuation or list separators.
                if word in self._task_versions or hash(word) % 4096 in self._overflow_buckets:
                    named.add(word)
                if word not in self._task_versions:
                    stripped = word.lstrip("([{\"'").rstrip(".,;:!?)]}\"'")
                    named.add(stripped)
                    if stripped not in self._task_versions:
                        named.update(re.split("[,;]", stripped))
            selected = {self._observations[h].ref for h in references if h in self._observations}
            named.difference_update(selected)
            extra = {}
            now = time.monotonic()
            for handle, observation in self._observations.items():
                if not observation.delivered_for_task or observation.ref not in named or observation.expires <= now:
                    continue
                if self._task_versions.get(observation.ref) != (observation.digest, observation.target):
                    raise EvidenceError("Named source version is ambiguous; supply its evidence reference")
                extra[observation.ref] = handle
            missing = named.difference(extra)
            if missing.intersection(self._task_versions) or any(hash(path) % 4096 in self._overflow_buckets
                                                               for path in missing):
                raise EvidenceError("Named dependency needs a current evidence reference")
            return [*references, *extra.values()]

    def _resolve(self, references, path_allowed):
        with self._lock:
            self._authorize(publishing=True)
            if not isinstance(references, list) or not 1 <= len(references) <= 50:
                raise EvidenceError("Invalid evidence references")
            now, sources = time.monotonic(), {}
            for handle in references:
                observation = self._observations.get(handle) if isinstance(handle, str) else None
                if observation is None or observation.expires <= now:
                    raise EvidenceError("Evidence reference unavailable or expired")
                try:
                    _, data = repo_contents(self.workflow.repo, observation.ref,
                                            path_allowed=path_allowed, expected_target=observation.target)
                    if hashlib.sha256(data).hexdigest() != observation.digest:
                        raise ValueError()
                except (OSError, ValueError):
                    raise EvidenceError("Observed evidence changed or became unavailable") from None
                sources[observation.ref] = Source(ref=observation.ref, content_hash=observation.digest)
            return list(sources.values())

    def publish(self, question: str, content: str, references: list[str]) -> dict:
        """Use ordinary sanitized, tainted, idempotent workflow publication.

        A reference records a span observed, not whether a claim follows from it.
        The host must keep claims within those spans and apply publication policy.
        """
        with self._lock:
            if self.path_allowed is None:
                sources = self.resolve(references)
                policy = {}
            else:
                pins = PinnedSourcePaths(self.path_allowed)
                sources = self._resolve(references, pins)
                # Sanitization may rewrite a ref. Only the observed names and targets
                # may support the outgoing finding, including an offline queue entry.
                if any(scrub(source.ref) != source.ref for source in sources):
                    raise EvidenceError("Observed source name unavailable for publication")
                pins.observed_only = True
                policy = {"path_allowed": pins}
            w = self.workflow
            trail = w.finding(question, content, sources, **policy)
            options = {"repo": w.repo, "repository_id": w.repository_id} if w.repository_id else {}
            if self.path_allowed is not None:
                options["repo"] = w.repo
            return w.client.push(trail, **options, **policy)

    def close(self):
        with self._lock:
            self._closed = True
            self._observations.clear()
            self._task_versions.clear()
            self._overflow_buckets.clear()
