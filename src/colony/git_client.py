"""Validate Git-aware receipts against the caller's files, without local inference."""

import hashlib
import re
from pathlib import Path
from typing import Literal, NamedTuple

from colony.git_context import capture, compare
from colony.models import ContextBudget, GitContext, Source
from colony.security import PATTERNS, scrub
from colony.validation import PinnedSourcePaths, repo_contents, repo_file, validate

_MAX_LOOKUP_HINTS = 8
_MAX_EXCERPT_CHARS = 2400
_MAX_FOCUSED_READS = 2


def _source_lines(root: Path, source: Source, *, path_allowed=None) -> list[str]:
    # Keep the caller path independent of the server's native retrieval stack.
    # Scrub the whole file before excerpting, preserving original line numbers.
    if source.kind != "file":
        return []
    try:
        _, data = repo_contents(root, source.ref, path_allowed=path_allowed)
        if hashlib.sha256(data).hexdigest() != source.content_hash:
            raise ValueError("Current source changed")
        text = data.decode("utf-8")
    except (OSError, ValueError, UnicodeError):
        if path_allowed is not None:
            raise ValueError("Current source unavailable") from None
        return []
    for pattern in PATTERNS:
        text = re.sub(pattern, lambda m: "[REDACTED]" + "\n" * m[0].count("\n"), text)
    return text.splitlines()


def checked_sources(repo: Path, sources: list[Source], *, path_allowed=None) -> None:
    if validate(repo, sources, path_allowed=path_allowed)[0] != "validated":
        raise ValueError("Git-aware publication requires matching current local file evidence")


def compact_receipt(context: GitContext, reason: str, origin: GitContext | None = None):
    result = {"compatibility": reason, "state": context.state}
    if context.state == "git":
        result.update(
            branch=scrub(context.branch) if context.branch else None,
            commit=context.commit,
            dirty=context.dirty,
        )
    if origin is not None and reason not in {"matched", "not_applicable"}:
        result.update(
            origin_branch=scrub(origin.branch) if origin.branch else None,
            origin_commit=origin.commit,
        )
    return result


class _LookupHint(NamedTuple):
    kind: Literal["whole", "excerpt", "prefix"]
    position: int
    slot: tuple[str, int, int | None]
    line: int
    length: int


class _UnbudgetedHints:
    """Keep verification slots bounded and retain the wire order of their alternatives."""

    def __init__(self, evidence, reads, sources):
        from colony.compact import bound_lookup_spans

        self.marker_positions = {}
        self.prefixes = {}
        for position, span in enumerate(bound_lookup_spans(evidence, reads, sources)):
            if span.get("complete_file"):
                ref, length = span["ref"], len(span["text"])
                self.marker_positions.setdefault(ref, position)
                prefixes = self.prefixes.setdefault(ref, {})
                # A wider prefix may reach the first nonblank local character.
                # Keep each bounded width at its own first citation position.
                if 0 < length <= _MAX_EXCERPT_CHARS:
                    prefixes.setdefault(length, position)

        self.slots = set()
        self.excerpts = {}
        for position, span in enumerate(bound_lookup_spans(evidence, reads, sources)):
            ref, line, length = span["ref"], span["line"], len(span["text"])
            if not span.get("complete_file") and not 0 < length <= _MAX_EXCERPT_CHARS:
                continue
            key = self.slot_key(ref, line, length)
            if len(self.slots) < _MAX_LOOKUP_HINTS:
                self.slots.add(key)
            if not span.get("complete_file"):
                bounds = self.excerpts.setdefault(ref, {})
                if key in self.slots or (ref in self.prefixes and len(bounds) < _MAX_LOOKUP_HINTS):
                    bounds.setdefault((line, length), position)

    def slot_key(self, ref, line, length):
        # A marker and its line-one alternatives can produce at most one read.
        return (ref, line, None if line == 1 and ref in self.prefixes else length)

    def candidates(self, whole_files):
        candidates = []
        for ref, bounds in self.excerpts.items():
            for (line, length), position in bounds.items():
                key = self.slot_key(ref, line, length)
                if key not in self.slots:
                    # A late explicit excerpt may use this file's admitted marker slot.
                    key = (ref, 1, None)
                if key in self.slots:
                    candidates.append(_LookupHint("excerpt", position, key, line, length))
        for ref, prefixes in self.prefixes.items():
            key = (ref, 1, None)
            if key in self.slots:
                if whole_files:
                    candidates.append(_LookupHint("whole", self.marker_positions[ref], key, 1, 0))
                candidates.extend(_LookupHint("prefix", pos, key, 1, n) for n, pos in prefixes.items())
        # At the marker itself, try the whole file before using its prefix.
        # Focused research exhausts explicit excerpts before using spare prefix slots.
        return sorted(
            candidates, key=lambda hint: (not whole_files and hint.kind == "prefix", hint.position)
        )


def _overlaps(occupied, ref, start, end):
    return any(r == ref and start <= last and end >= first for r, first, last in occupied)


def _verified_hint_text(repo, source, starts, whole_file_limit, explicit_bounds, options):
    # Read a source once and retain only text that could be delivered.
    lines = _source_lines(repo, source, **options)
    file_text = "\n".join(lines)
    tails, offset = {}, 0
    for number, line in enumerate(lines, 1):
        if number in starts:
            tails[number] = file_text[offset : offset + _MAX_EXCERPT_CHARS]
            if len(tails) == len(starts):
                break
        offset += len(line) + 1
    whole = file_text if whole_file_limit is not None and len(file_text) <= whole_file_limit else None
    excerpts = (tails[line][:length] for line, length in explicit_bounds if line in tails)
    shortest = min((len(text) for text in excerpts if text.strip()), default=None)
    return whole, tails, shortest


def _fitting_read(hint, verified, budget):
    whole, tails, shortest = verified
    if hint.line not in tails:
        return None
    excerpt = tails[hint.line][:hint.length]
    if (
        whole is not None and len(whole) <= budget
        and (hint.kind == "whole" or (hint.kind == "excerpt" and excerpt.strip()))
    ):
        return 1, whole, True
    if hint.kind == "whole" or len(excerpt) > budget:
        return None
    if hint.kind == "prefix" and shortest is not None and shortest <= budget:
        return None
    return hint.line, excerpt, False


def _unbudgeted_reads(repo, by_ref, hints, options, whole_files):
    candidates = hints.candidates(whole_files)
    starts = {}
    for hint in candidates:
        starts.setdefault(hint.slot[0], set()).add(hint.line)

    verified, used, occupied, selected = {}, set(), [], []
    complete_refs = set()
    budget, limit = (24000, 4) if whole_files else (4800, _MAX_FOCUSED_READS)
    # Reconsider citation order at the current budget for each of the two/four
    # delivery slots. A blank may fill a spare slot after all usable reads.
    for _ in range(limit):
        first_blank = None
        for hint in candidates:
            ref = hint.slot[0]
            if hint.slot in used or ref in complete_refs:
                continue
            if whole_files and hint.kind == "prefix" and any(r == ref for r, _, _ in occupied):
                continue
            if ref not in verified:
                marked = whole_files and ref in hints.marker_positions
                verified[ref] = _verified_hint_text(
                    repo, by_ref[ref], starts[ref], min(16000, budget) if marked else None,
                    hints.excerpts.get(ref, ()) if marked else (), options,
                )
            read = _fitting_read(hint, verified[ref], budget)
            if read is None:
                continue
            start, text, complete = read
            end = start + text.count("\n")
            if _overlaps(occupied, ref, start, end):
                continue
            if complete or text.strip():
                break
            if first_blank is None:
                first_blank = hint, start, end, text
        else:
            if first_blank is None:
                break
            hint, start, end, text = first_blank
            ref, complete = hint.slot[0], False
        # Focused lookups display the first explicit excerpt at its earlier marker.
        # This affects presentation only, after admission at the excerpt's own rank.
        position = hint.position
        if not whole_files and hint.kind == "excerpt" and not any(r == ref for r, _, _ in occupied):
            position = min(position, hints.marker_positions.get(ref, position))
        read = {"ref": ref, "line": start, "text": text, "complete_file": complete}
        selected.append(((not whole_files and hint.kind == "prefix", position), read))
        occupied.append((ref, start, end))
        used.add(hint.slot)
        if complete:
            complete_refs.add(ref)
        budget -= len(text)
    return [read for _, read in sorted(selected, key=lambda entry: entry[0])]


def _local_reads(
    repo, sources, remote_evidence, whole_files, *, adaptive=False,
    compact_bundle=False, path_allowed=None,
):
    by_ref = {source.ref: source for source in sources}
    options = {"path_allowed": path_allowed} if path_allowed is not None else {}
    if isinstance(remote_evidence, _UnbudgetedHints):
        return _unbudgeted_reads(repo, by_ref, remote_evidence, options, whole_files)
    hints = remote_evidence if isinstance(remote_evidence, list) else []
    selected, occupied, budget = [], [], 24000 if whole_files else 4800
    # Compact adaptive responses can require all eight bounded source cores.
    # Legacy focused/whole-file requests retain their two/four-read limits.
    limit = 8 if adaptive and compact_bundle else (4 if whole_files else _MAX_FOCUSED_READS)
    for item in hints[:_MAX_LOOKUP_HINTS]:
        if not isinstance(item, dict):
            continue
        ref, line = item.get("ref"), item.get("line")
        if not isinstance(ref, str) or ref not in by_ref:
            continue
        if type(line) is not int or line < 1:
            continue
        lines = _source_lines(repo, by_ref[ref], **options)
        if not lines or line > len(lines):
            continue
        text = "\n".join(lines)
        file_limit = 16000 if adaptive else min(16000, budget)
        complete = whole_files and item.get("complete_file") is True and len(text) <= file_limit
        start = 1 if complete else line
        if not complete:
            hint = item.get("text")
            if not isinstance(hint, str) or not hint or (not adaptive and len(hint) > _MAX_EXCERPT_CHARS):
                continue
            # A short Markdown section may exceed 32 lines. Preserve its selected
            # span, using only the bounded hint length and verified local text.
            if adaptive:
                # Budgeted source units are indivisible. A remote character count
                # must never turn a verified local command into a partial command.
                # Excerpts use canonical '\n' separators. splitlines() would
                # discard a represented trailing empty source line.
                last_line = line + len(hint.split("\n")) - 1
                if last_line > len(lines):
                    continue
                text = "\n".join(lines[line - 1 : last_line])
            else:
                text = "\n".join(lines[line - 1 :])[:len(hint)]
        end = start + text.count("\n")
        if (not adaptive and len(text) > budget) or _overlaps(occupied, ref, start, end):
            continue
        read = {"ref": ref, "line": start, "text": text, "complete_file": complete}
        if adaptive:
            first, last = item.get("selected_line"), item.get("selected_end_line")
            if first is not None or last is not None:
                if not (type(first) is int and type(last) is int and start <= first <= last <= end):
                    continue
                read.update(selected_line=first, selected_end_line=last)
        selected.append(read)
        occupied.append((ref, start, end))
        budget -= len(text)
        if len(selected) >= limit:
            break
    return selected


def verify_lookup(
    result,
    repo: Path,
    context: GitContext,
    *,
    whole_files=False,
    context_budget: ContextBudget | None = None,
    path_allowed=None,
):
    """A server's checkout or receipt alone cannot certify the caller's working tree."""
    if not isinstance(result, dict):
        return {"hit": False, "reason": "git_invalid_receipt"}
    if not result.get("hit"):
        reason = result.get("reason")
        if reason not in {
            "no_pull_scope",
            "no_relevant_trail",
            "below_threshold",
            "no_active_trails",
            "git_context_mismatch",
        } and not (context_budget is not None and reason == "budget_limited"):
            reason = "git_no_verified_hit"
        return {"hit": False, "reason": reason, "git": compact_receipt(context, "unavailable")}
    # Client.ask owns the final delivery check when it supplies its operation's
    # pins. Direct Python callers get the same protection at this return boundary.
    check_on_return = path_allowed is not None and not isinstance(path_allowed, PinnedSourcePaths)
    if check_on_return:
        path_allowed = PinnedSourcePaths(path_allowed)
    try:
        receipt = result["git"]
        if GitContext.model_validate(receipt["request"]) != context:
            raise ValueError()
        origin = GitContext.model_validate(receipt["origin"]) if receipt.get("origin") else None
        reason = compare(origin, context)
        if reason == "repository_mismatch" or result.get("status") != "validated":
            raise ValueError()
        from colony.compact import PROFILE, legacy_evidence

        profile = result.get("response_profile")
        if profile is not None and (profile != PROFILE or context_budget is None):
            raise ValueError()
        compact = profile == PROFILE
        if compact:
            result = legacy_evidence(result)
        raw_sources = result["sources"]
        if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= 50:
            raise ValueError()
        sources = [Source.model_validate(source) for source in raw_sources]
        checked_sources(repo, sources, path_allowed=path_allowed)
        hints = result.get("evidence")
        if context_budget is None:
            hints = _UnbudgetedHints(hints, result.get("current_repository_reads"), raw_sources)
        evidence = _local_reads(
            repo,
            sources,
            hints,
            whole_files or compact,
            **({"compact_bundle": True} if compact else {}),
            **({"adaptive": True} if context_budget is not None else {}),
            **({"path_allowed": path_allowed} if path_allowed is not None else {}),
        )
        if context_budget is not None and (
            not isinstance(hints, list) or len(evidence) != len(hints)
        ):
            # Every advertised adaptive core is required; a partial saved-answer
            # hit must not survive one failed local span or source check.
            raise ValueError()
        if not evidence or capture(repo, context.repository_id) != context:
            raise ValueError()
    except (KeyError, TypeError, ValueError, OSError):
        return {"hit": False, "reason": "git_local_evidence_not_verified"}
    answer = dict(result)
    if context_budget is None:
        # Only locally rebuilt spans may accompany the caller-worktree verification receipt.
        answer.pop("current_repository_reads", None)
    answer["git"] = compact_receipt(context, reason, origin)
    answer["evidence"] = evidence
    answer["complete_files"] = [item["ref"] for item in evidence if item["complete_file"]]
    if context_budget is None and "evidence_scope" in answer:
        cited = {source.ref for source in sources}
        answer["evidence_scope"] = "cited_files" if set(answer["complete_files"]) == cited else "partial"
    answer["provenance"] = "Caller worktree file hashes verified. Git origin is producer-reported."
    if reason not in {"matched", "not_applicable"} or result.get("match_kind") == "evidence":
        answer["match_kind"] = "evidence"
        answer["content"] = "\n\n".join(
            f"{item['ref']}:{item['line']}\n{item['text']}" for item in evidence
        )
        answer["recommendation"] = "Use only current local file evidence; saved answer withheld."
    if context_budget is not None:
        from colony.context_budget import fit_response

        answer = fit_response(answer, context_budget)
    if check_on_return and answer.get("hit"):
        try:
            for ref in path_allowed.targets:
                repo_file(repo, ref, path_allowed=path_allowed)
        except (ValueError, OSError):
            return {"hit": False, "reason": "git_local_evidence_not_verified"}
    return answer
