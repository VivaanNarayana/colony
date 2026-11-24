"""Plan explicit source reads for a small, host-enumerated containment question."""

import re
from dataclasses import dataclass

from colony.evidence_refs import BATCH_CONTENT_BYTES
from colony.task_reads import _public_path
from colony.validation import repo_file

_REPO = r"(?:repo(?:sitory)?|codebase|project)"
_VERB = r"(?:contain|include|document|mention|record|implement|define|specify|provide)"
_ANSWER_FORMAT = (
    r"(?:(?:please\s++)?(?:answer|reply|respond|cite)\b[^()?!.\r\n]*+"
    r"|yes\s*+(?:or|/)\s*+no|true\s*+(?:or|/)\s*+false|briefly)"
)
_ANSWER_SUFFIX = rf"(?:\s*+(?:\(\s*+{_ANSWER_FORMAT}\s*+\)|[,;]\s*+{_ANSWER_FORMAT}))?"
# A subordinate clause can describe the code's behavior. Route an explicit request
# to determine existence, such as "state whether", "check if", or "I want to know if".
_CHECK = r"(?:check|confirm|determine|verify|state|report|see|find\s+out|tell\s+(?:me|us))"
# Allow a short preceding imperative in the same sentence, including a dotted filename.
_SEQUENCE = (
    r"(?:(?:read|inspect|review|summarize|trace|list)\s+(?:[^?!.;:,\n]|\.(?=\w)){1,200}?"
    r"\s+(?:and(?:\s+then)?|then)\s+)?"
)
_EXISTENCE_REQUEST = (
    r"(?:^|[?!.:;,])\s*(?:(?:and\s+then|and|then)\s+)?"
    rf"(?:(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?{_SEQUENCE}{_CHECK}"
    r"|i\s+(?:want|need)\s+to\s+know|do\s+you\s+know)\s+(?:whether|if)"
)
_CONTAINMENT = re.compile(
    rf"(?:{_EXISTENCE_REQUEST}|(?:^|[?!.:])\s*does)\s+(?:(?:this|the|our)\s+)?{_REPO}\s+"
    rf"(?P<verb>{_VERB})(?:s|es)?\b"
    rf"|(?:^|[?!.\n])\s*(?:is|are)\s+[^?!.\n]{{1,140}}\s+in\s+(?:this|the|our)\s+{_REPO}\b"
    rf"(?={_ANSWER_SUFFIX}\s*+(?:[?!.]|$))",
    re.IGNORECASE,
)
# A temporal qualifier on the repository's action asks about order. A dot inside
# a filename is not a sentence boundary.
_ORDERED_ACTION = re.compile(
    r"(?:[^?.!;]|\.(?=\w))*?\b(?:before|after)\s+\w",
    re.IGNORECASE,
)
_REPORTED_CLAUSE = re.compile(r"\s+(?:how|when|why|whether|what|that)\b", re.IGNORECASE)
SCOPE_ROUTE = (
    "For the repository containment question, list the authorized files once with {}. "
    "Follow its read plan unless positive evidence already answers the question. "
    "When listed_scope is complete, answer from those files. A memory lookup cannot certify absence."
)


def containment_question(question):
    for match in _CONTAINMENT.finditer(question):
        if match["verb"] is None:  # "Is X in this repository?" already requires a terminal suffix.
            return True
        # A request for documentation can describe the order it should document.
        if (match["verb"].lower() in {"document", "mention", "record", "specify"}
                and _REPORTED_CLAUSE.match(question, match.end())):
            return True
        if not _ORDERED_ACTION.match(question, match.end()):
            return True
    return False


@dataclass
class _Coverage:
    version: tuple
    digest: str
    stop: int
    at_eof: bool
    redacted: bool
    bytes_left: int

    @property
    def complete(self):
        return self.at_eof and not self.redacted


class SourceScope:
    """A fixed host inventory. Only ordinary, charged, delivered reads supply coverage."""

    def __init__(self, access, paths, remaining):
        self.access = access
        self.evidence = access.evidence
        self.paths = frozenset(paths)
        self.remaining = remaining
        self.listed = False
        self.covered = {}

    def remaining_units(self):
        try:
            units = self.remaining() if self.remaining is not None else None
        except Exception:
            return None
        return units if type(units) is int else None

    def routing(self, list_tool):
        units = self.remaining_units()
        if units is not None and units <= 0:
            return "No source units remain. Use available evidence and qualify uninspected files."
        if units is None or units == 1:
            return (f"Read a file named in the question directly. For unknown paths, list with {list_tool}. "
                    "Follow the read plan when available. Qualify any uninspected scope.")
        return SCOPE_ROUTE.format(list_tool)

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

    def unverified_plan(self):
        self.listed = False
        self.covered.clear()
        return {"status": "unverified_scope", "guidance": "Qualify uninspected files."}

    def plan(self, listed):
        self.evidence._authorize()
        # An embedded inventory may repeat a path. Plan one read per distinct file.
        listed = list(dict.fromkeys(listed))
        self.listed = (bool(self.paths) and set(listed) == self.paths
                       and all(_public_path(p) for p in self.paths))
        if not self.listed:
            return self.unverified_plan()
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
            units = self.remaining_units()
            fits_quota = units is not None and units >= len(pending)
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
            remaining_bytes += coverage.bytes_left if coverage else file_size
            if remaining_bytes > BATCH_CONTENT_BYTES:
                return partial
            entry = {"path": path}
            if coverage and coverage.stop:
                entry["offset"] = coverage.stop
            batch.append(entry)
        return {"next_tool": "read", "arguments": {"batch": batch}}

    def versions(self, requested):
        self.evidence._authorize()
        paths = self.paths.intersection(requested)
        if self.listed:
            paths |= self.covered.keys()
        versions = {p: self.version(p) for p in paths}
        for p, version in versions.items():
            if p in self.covered and self.covered[p].version != version:
                del self.covered[p]
        return versions

    def record(self, before, pages):
        after = self.versions(before)
        for result, page, end_offset, unambiguous in pages:
            path = page.ref
            if not unambiguous or not after.get(path) or before[path] != after[path] or page.target != after[path][0]:
                continue
            prior = self.covered.get(path)
            if prior and prior.digest != page.digest:
                del self.covered[path]
                prior = None
            if not page.whole_range or page.planning_bytes_from_offset is None:
                continue
            stop = prior.stop if prior else 0
            if page.offset <= stop <= end_offset:
                bytes_left = min(BATCH_CONTENT_BYTES + 1,
                                 page.planning_bytes_from_offset - len(result["content"].encode("utf-8")))
                self.covered[path] = _Coverage(after[path], page.digest, end_offset,
                                               end_offset == page.span_length, page.redacted, bytes_left)

    def receipt(self):
        missing = sorted(p for p in self.paths if p not in self.covered or not self.covered[p].complete)
        result = {"status": "partial" if missing else "complete", "listed_files": len(self.paths)}
        if missing:
            # Only repeat names still authorized for disclosure.
            result["unverified_count"] = len(missing)
            result["unverified_paths"] = [p for p in missing[:8] if self.version(p) is not None]
        return result
