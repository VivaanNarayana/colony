"""Plan explicit source reads for a small, host-enumerated containment question."""

import re
from dataclasses import dataclass

from colony.task_reads import _public_path
from colony.validation import repo_file

_REPO = r"(?:repo(?:sitory)?|codebase|project)"
_VERB = r"(?:contain|include|document|mention|record|implement|define|specify|provide)"
_CONTAINMENT = re.compile(
    rf"(?:\b(?:whether|if)|(?:^|[?!.:])\s*does)\s+(?:(?:this|the|our)\s+)?{_REPO}\s+{_VERB}(?:s|es)?\b"
    rf"|(?:^|[?!.\n])\s*(?:is|are)\s+[^?!.\n]{{1,140}}\s+in\s+(?:this|the|our)\s+{_REPO}\b",
    re.IGNORECASE,
)
SCOPE_ROUTE = (
    "For the repository containment question, list the authorized files once with {}. "
    "Follow its read plan before a literal search or memory lookup unless positive evidence "
    "already answers the question. Judge meaning from delivered content. Qualify any gaps."
)


def containment_question(question):
    return _CONTAINMENT.search(question) is not None


@dataclass
class _Coverage:
    version: tuple
    digest: str
    stop: int
    at_eof: bool
    redacted_bytes_left: int | None

    @property
    def complete(self):
        return self.at_eof and self.redacted_bytes_left is None

    def remaining_bytes_bound(self, file_size):
        if self.redacted_bytes_left is not None:
            return self.redacted_bytes_left
        return file_size - self.stop


class SourceScope:
    """A fixed host inventory. Only ordinary, charged, delivered reads supply coverage."""

    def __init__(self, access, paths, remaining):
        self.access = access
        self.evidence = access.evidence
        self.paths = frozenset(paths)
        self.remaining = remaining
        self.listed = False
        self.covered = {}

    def version(self, path):
        if self.evidence.path_allowed is None and self.evidence.read != self.access.read:
            return None  # An opaque host read wrapper cannot authorize metadata probes.
        try:
            target = repo_file(self.evidence.workflow.repo, path,
                               path_allowed=self.evidence.path_allowed)
            info = target.stat()
            return (str(target), info.st_dev, info.st_ino, info.st_ctime_ns,
                    info.st_mtime_ns, info.st_size)
        except (OSError, ValueError, RuntimeError):
            return None

    def visible_paths(self):
        self.evidence._authorize()
        return sorted(p for p in self.paths if _public_path(p) and self.version(p) is not None)

    def plan(self, listed):
        self.evidence._authorize()
        # An embedded inventory may repeat a path. Plan one read per distinct file.
        listed = list(dict.fromkeys(listed))
        self.listed = (bool(self.paths) and set(listed) == self.paths
                       and all(_public_path(p) for p in self.paths))
        if not self.listed:
            self.covered.clear()
            return {"status": "unverified_scope", "guidance": "Qualify uninspected files."}
        versions = self.versions(())
        partial = {"status": "partial", "guidance": "Prioritize evidence; qualify unread or partial files."}
        pending = [p for p in listed if p not in self.covered or not self.covered[p].at_eof]
        if not pending:
            if all(self.covered[p].complete for p in listed):
                return {"status": "complete", "listed_files": len(listed)}
            return partial
        if self.remaining is None:
            fits_quota = self.access.admit is None and self.evidence.read == self.access.read
        else:
            try:
                units = self.remaining()
            except Exception:
                units = None
            fits_quota = type(units) is int and units >= len(pending)
        if not fits_quota or len(pending) > 8:
            return partial
        batch = []
        remaining_bytes = 0
        for path in pending:
            version = versions[path] if path in versions else self.version(path)
            if version is None:
                return partial
            coverage = self.covered.get(path)
            file_size = version[-1]
            remaining_bytes += coverage.remaining_bytes_bound(file_size) if coverage else file_size
            if remaining_bytes > 16000:
                return partial
            entry = {"path": path}
            if coverage and coverage.stop:
                entry["offset"] = coverage.stop
            batch.append(entry)
        return {"next_tool": "read", "arguments": {"batch": batch}}

    def versions(self, requested):
        self.evidence._authorize()
        paths = self.paths.intersection(requested) | self.covered.keys()
        versions = {p: self.version(p) for p in paths}
        for p in list(self.covered):
            if self.covered[p].version != versions[p]:
                del self.covered[p]
        return versions

    def record(self, before, pages, results):
        after = self.versions(before)
        delivered = {id(result) for result in results}
        for result, content, page, end_offset in pages:
            path = page.ref
            if (id(result) not in delivered or result.get("content") != content or result.get("path") != path
                    or not after.get(path) or before[path] != after[path] or page.target != after[path][0]):
                continue
            prior = self.covered.get(path)
            if prior and prior.digest != page.digest:
                del self.covered[path]
                prior = None
            if not page.whole_range:
                continue
            stop = prior.stop if prior else 0
            if page.offset <= stop <= end_offset:
                redacted_bytes_left = page.redacted_bytes_from_offset
                if redacted_bytes_left is not None:
                    redacted_bytes_left -= len(content.encode("utf-8"))
                self.covered[path] = _Coverage(after[path], page.digest, end_offset,
                                               end_offset == page.span_length, redacted_bytes_left)
        missing = sorted(p for p in self.paths if p not in self.covered or not self.covered[p].complete)
        result = {"status": "partial" if missing else "complete", "listed_files": len(self.paths)}
        if missing:
            # Only repeat names still authorized for disclosure.
            result["unverified_count"] = len(missing)
            result["unverified_paths"] = [p for p in missing[:8] if self.version(p) is not None]
        return result
