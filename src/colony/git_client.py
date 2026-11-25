"""Validate Git-aware receipts against the caller's files, without local inference."""

import hashlib
import re
from itertools import chain
from pathlib import Path
from typing import NamedTuple

from colony.git_context import capture, compare
from colony.models import ContextBudget, GitContext, Source
from colony.security import PATTERNS, scrub
from colony.validation import PinnedSourcePaths, repo_contents, repo_file, validate

_MAX_LOOKUP_HINTS = 8
_MAX_EXCERPT_CHARS = 2400


class _WholeFileFallbacks(NamedTuple):
    explicit: tuple[tuple[int, int], ...]
    reserved_excerpts: tuple[tuple[int, int], ...]


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


def _unbudgeted_hints(evidence, reads, sources, whole_files):
    """Reserve up to eight local reads and associate whole-file requests with their fallback spans."""
    from colony.compact import bound_lookup_spans

    unique = {}
    whole_file_refs = set()
    excerpt_bounds = {}
    for span in bound_lookup_spans(evidence, reads, sources):
        # Validate all bindings, even after the local read slots are full.
        ref = span["ref"]
        line, length = span["line"], len(span["text"])
        key = (ref, line, length)
        if len(unique) < _MAX_LOOKUP_HINTS:
            unique.setdefault(key, span)
        if whole_files:
            if span.get("complete_file"):
                whole_file_refs.add(ref)
            elif 0 < length <= _MAX_EXCERPT_CHARS:
                candidates = excerpt_bounds.setdefault(ref, {})
                # A late explicit citation can also describe an already reserved slot.
                if len(candidates) < _MAX_LOOKUP_HINTS or key in unique:
                    candidates[(line, length)] = None
    fallbacks = {}
    for ref in whole_file_refs:
        explicit = tuple(excerpt_bounds.get(ref, ()))
        reserved = tuple(bound for bound in explicit if (ref, *bound) in unique)
        # If a marker occupies an explicit excerpt's slot, that slot can produce a
        # different fallback. Serve the admitted explicit bounds in citation order.
        marker_shares_slot = any(unique[(ref, *bound)].get("complete_file") for bound in reserved)
        fallbacks[ref] = _WholeFileFallbacks(explicit, reserved if marker_shares_slot else ())
    return list(unique.values()), fallbacks


def _fitting_excerpts(lines, bounds, budget):
    for line, length in bounds:
        if 1 <= line <= len(lines) and 0 < length <= _MAX_EXCERPT_CHARS:
            text = "\n".join(lines[line - 1 :])[:length]
            if len(text) <= budget:
                yield line, text


def _local_reads(
    repo, sources, remote_evidence, whole_files, *, whole_file_fallbacks=None, adaptive=False,
    compact_bundle=False, path_allowed=None,
):
    by_ref = {source.ref: source for source in sources}
    hints = remote_evidence if isinstance(remote_evidence, list) else []
    selected, occupied, budget = [], [], 24000 if whole_files else 4800
    # Compact adaptive responses can require all eight bounded source cores.
    # Legacy focused/whole-file requests retain their two/four-read limits.
    limit = 8 if adaptive and compact_bundle else (4 if whole_files else 2)
    options = {"path_allowed": path_allowed} if path_allowed is not None else {}
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
        fallbacks = whole_file_fallbacks.get(ref) if whole_file_fallbacks else None
        expand = fallbacks is not None or item.get("complete_file") is True
        file_limit = 16000 if adaptive else min(16000, budget)
        complete = whole_files and expand and len(text) <= file_limit
        if complete:
            candidates = ((1, text),)
        else:
            hint = item.get("text")
            # A short Markdown section may exceed 32 lines. Preserve its selected
            # span, using only the bounded hint length and verified local text.
            if adaptive:
                if not isinstance(hint, str) or not hint:
                    continue
                # Budgeted source units are indivisible. A remote character count
                # must never turn a verified local command into a partial command.
                # Excerpts use canonical '\n' separators. splitlines() would
                # discard a represented trailing empty source line.
                last_line = line + len(hint.split("\n")) - 1
                if last_line > len(lines):
                    continue
                candidates = ((line, "\n".join(lines[line - 1 : last_line])),)
            else:
                own = (line, len(hint)) if isinstance(hint, str) else (line, 0)
                bounds = (own,)
                if fallbacks is not None and item.get("complete_file") and fallbacks.explicit:
                    if any(read["ref"] == ref for read in selected):
                        bounds = fallbacks.reserved_excerpts
                    else:
                        bounds = chain(fallbacks.explicit, bounds)
                elif fallbacks is not None and fallbacks.reserved_excerpts:
                    bounds = fallbacks.reserved_excerpts
                candidates = _fitting_excerpts(lines, bounds, budget)
        for start, text in candidates:
            end = start + text.count("\n")
            if not any(r == ref and start <= last and end >= first for r, first, last in occupied):
                break
        else:
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
        whole_file_fallbacks = None
        if context_budget is None:
            hints, whole_file_fallbacks = _unbudgeted_hints(
                hints, result.get("current_repository_reads"), raw_sources, whole_files
            )
        evidence = _local_reads(
            repo,
            sources,
            hints,
            whole_files or compact,
            whole_file_fallbacks=whole_file_fallbacks,
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
