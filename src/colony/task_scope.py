"""Plan explicit source reads for a small, host-enumerated containment question."""

import re
from dataclasses import dataclass

from colony.task_reads import _public_path
from colony.validation import repo_file

_REPO = r"(?:repo(?:sitory)?|codebase|project)"
_VERB = r"(?:contain|include|document|mention|record|implement|define|specify|provide)"
_CONTAINMENT = re.compile(
    rf"\b(?:whether|if|does)\s+(?:(?:this|the|our)\s+)?{_REPO}\s+{_VERB}(?:s|es)?\b"
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
    complete: bool


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
        self.listed = (bool(self.paths) and set(listed) == self.paths
                       and all(_public_path(p) for p in self.paths))
        if not self.listed:
            self.covered.clear()
            return {"status": "unverified_scope", "guidance": "Qualify uninspected files."}
        versions = self.versions(())
        missing = [p for p in listed if p not in self.covered or not self.covered[p].complete]
        if not missing:
            return {"status": "complete", "listed_files": len(listed)}
        if self.remaining is None:
            fits_quota = self.access.admit is None and self.evidence.read == self.access.read
        else:
            try:
                units = self.remaining()
            except Exception:
                units = None
            fits_quota = type(units) is int and units >= len(missing)
        partial = {"status": "partial", "guidance": "Prioritize evidence; qualify unread or partial files."}
        if fits_quota and len(missing) <= 8:
            versions.update((p, self.version(p)) for p in missing if p not in versions)
            if any(versions[p] is None for p in missing):
                return partial
            # Offsets count characters. Each observed character consumed at least
            # one source byte, so this conservatively bounds the remaining bytes.
            offsets = {p: self.covered[p].stop if p in self.covered else 0 for p in missing}
            if sum(versions[p][-1] - offsets[p] for p in missing) <= 16000:
                batch = [{"path": p, **({"offset": offsets[p]} if offsets[p] else {})} for p in missing]
                return {"next_tool": "read", "arguments": {"batch": batch}}
        return partial

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
            if not page.whole_range or page.redacted:
                continue
            stop = prior.stop if prior else 0
            if page.offset <= stop:
                stop = max(stop, end_offset)
                self.covered[path] = _Coverage(after[path], page.digest, stop, stop == page.span_length)
        missing = sorted(p for p in self.paths if p not in self.covered or not self.covered[p].complete)
        result = {"status": "partial" if missing else "complete", "listed_files": len(self.paths)}
        if missing:
            # Only repeat names still authorized for disclosure.
            result["unverified_count"] = len(missing)
            result["unverified_paths"] = [p for p in missing[:8] if self.version(p) is not None]
        return result
