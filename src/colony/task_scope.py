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
        self.listed = (0 < len(self.paths) <= 8 and set(listed) == self.paths
                       and all(_public_path(p) for p in self.paths))
        if not self.listed:
            self.covered.clear()
            return {"status": "unverified_scope", "guidance": "Qualify uninspected files."}
        versions = self.versions()
        missing = [p for p in listed if p not in self.covered or not self.covered[p].complete]
        if not missing:
            return {"status": "complete", "listed_files": len(listed)}
        units = self.remaining() if self.remaining is not None else None
        if (all(versions[p] is not None for p in missing)
                and sum(versions[p][-1] for p in missing) <= 16000
                and (units is None or len(missing) <= units)):
            return {"next_tool": "read", "arguments": {"batch": [{"path": p} for p in missing]}}
        return {"status": "partial", "guidance": "Prioritize evidence; qualify unread or partial files."}

    def versions(self):
        self.evidence._authorize()
        versions = {p: self.version(p) for p in self.paths}
        for p in list(self.covered):
            if self.covered[p].version != versions[p]:
                del self.covered[p]
        return versions

    def record(self, before, results):
        after = self.versions()
        for result in results:
            path = result.get("path")
            if path not in after or not after[path] or before[path] != after[path]:
                continue
            obs = self.evidence._observations.get(result.get("evidence_ref"))
            if obs is None or obs.ref != path:
                continue
            prior = self.covered.get(path)
            if prior and prior.digest != obs.digest:
                del self.covered[path]
                prior = None
            if not obs.whole_range or obs.redacted:
                continue
            stop = prior.stop if prior else 0
            if obs.start_offset <= stop:
                stop = max(stop, obs.end_offset)
                self.covered[path] = _Coverage(after[path], obs.digest, stop, stop == obs.span_length)
        missing = sorted(p for p in self.paths if p not in self.covered or not self.covered[p].complete)
        result = {"status": "partial" if missing else "complete", "listed_files": len(self.paths)}
        if missing:
            # Only repeat names still authorized for disclosure.
            result["unverified_count"] = len(missing)
            result["unverified_paths"] = [p for p in missing if after[p] is not None]
        return result
