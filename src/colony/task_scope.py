"""Plan explicit source reads for a small, host-enumerated containment question."""

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from colony.evidence_refs import batch_allowances
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
_SENTENCE_END = r"(?:[?!]|\.(?!\w))"
_CONTAINMENT = re.compile(
    rf"(?:{_EXISTENCE_REQUEST}|(?:^|[?!.:])\s*does)\s+(?:(?:this|the|our)\s+)?{_REPO}\s+"
    rf"(?P<verb>{_VERB})(?:s|es)?\b"
    rf"|(?:^|\n|{_SENTENCE_END})\s*(?:is|are)\s+(?:[^?!.\n]|\.(?=\w)){{1,140}}\s+"
    rf"in\s+(?:this|the|our)\s+{_REPO}\b(?={_ANSWER_SUFFIX}\s*+(?:{_SENTENCE_END}|$))",
    re.IGNORECASE,
)
# A temporal qualifier on the repository's action asks about order. A dot inside
# a filename is not a sentence boundary.
_ORDERED_ACTION = re.compile(
    r"(?P<object>(?:[^?.!;]|\.(?=\w))*?)\b(?:before|after)\s+\w",
    re.IGNORECASE,
)
_RELATIVE_CLAUSE = re.compile(r"\S\s+(?:that|which|who|whose|where)\s+[^,;]*$", re.IGNORECASE)
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
        ordered = _ORDERED_ACTION.match(question, match.end())
        # In "scripts that run after deployment", the relative clause describes
        # the requested scripts. The repository's action has no time qualifier.
        if ordered is None or _RELATIVE_CLAUSE.search(ordered["object"]):
            return True
    return False


@dataclass(frozen=True)
class _Span:
    """Half-open normalized character and byte intervals; -1 marks the first line start."""

    start: int
    stop: int
    byte_start: int
    byte_stop: int


@dataclass
class _Coverage:
    digest: str
    length: int
    byte_length: int
    redacted: bool
    spans: list[_Span] = field(default_factory=list)

    def add(self, span: _Span):
        # Intervals are sorted and disjoint. Merge touching or overlapping deliveries.
        first = bisect_left(self.spans, span.start, key=lambda item: item.stop)
        end = bisect_right(self.spans, span.stop, lo=first, key=lambda item: item.start)
        if first < end:
            left, right = self.spans[first], self.spans[end - 1]
            span = _Span(min(left.start, span.start), max(right.stop, span.stop),
                         min(left.byte_start, span.byte_start), max(right.byte_stop, span.byte_stop))
        self.spans[first:end] = [span]

    @property
    def prefix(self) -> _Span | None:
        return self.spans[0] if self.spans and self.spans[0].start == -1 else None

    @property
    def at_eof(self):
        prefix = self.prefix
        return prefix is not None and prefix.stop == self.length

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
        versions = self.versions(listed)
        partial = {"status": "partial", "guidance": "Prioritize evidence; qualify unread or partial files."}
        pending = {}
        for path in listed:
            version = versions[path]
            if version is None:
                return partial
            if version not in self.covered or not self.covered[version].at_eof:
                pending.setdefault(version, path)
        if not pending:
            if all(self.covered[v].complete for v in versions.values()):
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
        sizes, needed = [], []
        for version, path in pending.items():
            coverage = self.covered.get(version)
            prefix = coverage.prefix if coverage else None
            offset_bytes = prefix.byte_stop if prefix else 0
            length = coverage.byte_length if coverage else version[-1]
            # The read stages the suffix to EOF, even if a shorter delivery can
            # meet an observed tail. Interior islands still require bridging.
            tail = coverage.spans[-1] if coverage else None
            gap_end = tail.byte_start if tail and tail.stop == coverage.length else length
            sizes.append(length - offset_bytes)
            needed.append(gap_end - offset_bytes)
            entry = {"path": path}
            if prefix and prefix.stop:
                entry["offset"] = prefix.stop
            batch.append(entry)
        if any(required > share for required, share in zip(needed, batch_allowances(sizes), strict=True)):
            return partial
        return {"next_tool": "read", "arguments": {"batch": batch}}

    def versions(self, requested):
        self.evidence._authorize()
        paths = set(requested)
        if not paths.issubset(self.paths):
            # Redundant dots and separators nominate the same candidate without a scan.
            # Parent segments and links can lead elsewhere; match the delivered target
            # against host-declared names when the spelling has no direct candidate.
            paths = {p if p in self.paths else PurePosixPath(p).as_posix() for p in paths}
            if not paths.issubset(self.paths):
                paths = self.paths
        versions = {p: self.version(p) for p in paths}
        if paths == self.paths:
            current = set(versions.values())
            self.covered = {v: evidence for v, evidence in self.covered.items() if v in current}
        return versions

    def record(self, before, pages):
        # Recheck the read candidates. A receipt also covers the rest of the inventory.
        after = self.versions(self.paths if self.listed else before)
        stable = {v[0]: v for path, v in before.items() if v and v == after[path]}
        for result, page, end_offset, unambiguous in pages:
            position = page.position
            if not unambiguous or position is None or page.target not in stable:
                continue
            # A page beginning at its declared line start also establishes the
            # preceding separator (or BOF). This distinguishes omitted blank lines.
            start = position.offset - int(page.offset == 0)
            byte_start = max(0, position.byte_offset - int(page.offset == 0))
            span = _Span(start, position.offset + (end_offset - page.offset), byte_start,
                         position.byte_offset + len(result["content"].encode("utf-8")))
            version = stable[page.target]
            prior = self.covered.get(version)
            if prior is None or prior.digest != page.digest:
                prior = _Coverage(page.digest, position.length, position.byte_length, page.redacted)
                self.covered[version] = prior
            prior.add(span)
        if self.listed:
            return self.receipt(after)
        # Drop obsolete revisions of sampled files without probing other paths.
        # Only unresolved cache pressure (e.g. many link retargets) needs the
        # inventory to identify which earlier targets can still supply coverage.
        if len(self.covered) > 2 * len(self.paths):
            current = {v[0]: v for v in after.values() if v}
            self.covered = {v: entry for v, entry in self.covered.items() if current.get(v[0], v) == v}
            if len(self.covered) > 2 * len(self.paths):
                self.versions(self.paths)

    def receipt(self, versions):
        missing = sorted(p for p, v in versions.items() if v not in self.covered or not self.covered[v].complete)
        result = {"status": "partial" if missing else "complete", "listed_files": len(self.paths)}
        if missing:
            # Only repeat names still authorized for disclosure.
            result["unverified_count"] = len(missing)
            result["unverified_paths"] = [p for p in missing[:8] if versions[p] is not None]
        return result
