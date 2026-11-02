"""Validate Git-aware receipts against the caller's files, without local inference."""

import hashlib
import re
from pathlib import Path

from colony.git_context import capture, compare
from colony.models import ContextBudget, GitContext, Source
from colony.security import PATTERNS, scrub
from colony.validation import repo_file, validate


def _source_lines(root: Path, source: Source) -> list[str]:
    # Keep the caller path independent of the server's native retrieval stack.
    # Scrub the whole file before excerpting, preserving original line numbers.
    if source.kind != "file":
        return []
    try:
        data = repo_file(root, source.ref).read_bytes()
        if hashlib.sha256(data).hexdigest() != source.content_hash:
            return []
        text = data.decode("utf-8")
    except (OSError, ValueError, UnicodeError):
        return []
    for pattern in PATTERNS:
        text = re.sub(pattern, lambda m: "[REDACTED]" + "\n" * m[0].count("\n"), text)
    return text.splitlines()


def checked_sources(repo: Path, sources: list[Source]) -> None:
    if validate(repo, sources)[0] != "validated":
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


def _local_reads(repo, sources, remote_evidence, whole_files, *, adaptive=False):
    by_ref = {source.ref: source for source in sources}
    hints = remote_evidence if isinstance(remote_evidence, list) else []
    selected, occupied, budget = [], [], 24000 if whole_files else 4800
    for item in hints[:8]:
        if not isinstance(item, dict):
            continue
        ref, line = item.get("ref"), item.get("line")
        if not isinstance(ref, str) or ref not in by_ref:
            continue
        if type(line) is not int or line < 1:
            continue
        lines = _source_lines(repo, by_ref[ref])
        if not lines or line > len(lines):
            continue
        text = "\n".join(lines)
        complete = whole_files and item.get("complete_file") is True and len(text) <= 16000
        start = 1 if complete else line
        if not complete:
            hint = item.get("text")
            if not isinstance(hint, str) or not hint or (not adaptive and len(hint) > 2400):
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
                text = "\n".join(lines[line - 1 :])[: len(hint)]
        if not adaptive and len(text) > budget:
            continue
        end = start + text.count("\n")
        # Separate sections of one file can supply different requested facts.
        # Refuse overlapping reads, including duplicate complete-file hints.
        if any(r == ref and start <= last and end >= first for r, first, last in occupied):
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
        if len(selected) >= (4 if whole_files else 2):
            break
    return selected


def verify_lookup(
    result,
    repo: Path,
    context: GitContext,
    *,
    whole_files=False,
    context_budget: ContextBudget | None = None,
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
    try:
        receipt = result["git"]
        if GitContext.model_validate(receipt["request"]) != context:
            raise ValueError()
        origin = GitContext.model_validate(receipt["origin"]) if receipt.get("origin") else None
        reason = compare(origin, context)
        if reason == "repository_mismatch" or result.get("status") != "validated":
            raise ValueError()
        raw_sources = result["sources"]
        if not isinstance(raw_sources, list) or not 1 <= len(raw_sources) <= 50:
            raise ValueError()
        sources = [Source.model_validate(source) for source in raw_sources]
        checked_sources(repo, sources)
        hints = result.get("evidence")
        evidence = _local_reads(
            repo,
            sources,
            hints,
            whole_files,
            **({"adaptive": True} if context_budget is not None else {}),
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
    answer["git"] = compact_receipt(context, reason, origin)
    answer["evidence"] = evidence
    answer["complete_files"] = [item["ref"] for item in evidence if item["complete_file"]]
    answer["provenance"] = "Caller worktree file hashes verified. Git origin is producer-reported."
    if reason not in {"matched", "not_applicable"} or result.get("match_kind") == "evidence":
        answer["match_kind"] = "evidence"
        answer["content"] = "\n\n".join(
            f"{item['ref']}:{item['line']}\n{item['text']}" for item in evidence
        )
        answer["recommendation"] = "Use only current local file evidence; saved answer withheld."
    if context_budget is not None:
        from colony.context_budget import fit_response

        return fit_response(answer, context_budget)
    return answer
