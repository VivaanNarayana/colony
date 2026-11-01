"""Validate Git-aware receipts against the caller's files, without local inference."""

from pathlib import Path

from colony.context import source_lines
from colony.git_context import capture, compare
from colony.models import GitContext, Source
from colony.security import scrub
from colony.validation import validate


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


def _local_reads(repo, sources, remote_evidence, whole_files):
    by_ref = {source.ref: source for source in sources}
    hints = remote_evidence if isinstance(remote_evidence, list) else []
    selected, used, budget = [], set(), 24000 if whole_files else 4800
    for item in hints[:8]:
        if not isinstance(item, dict):
            continue
        ref, line = item.get("ref"), item.get("line")
        if not isinstance(ref, str) or ref not in by_ref or ref in used:
            continue
        if type(line) is not int or line < 1:
            continue
        # source_lines verifies confinement and the actual bytes, and scrubs locally.
        lines = source_lines(repo, by_ref[ref])
        if not lines or line > len(lines):
            continue
        text = "\n".join(lines)
        complete = whole_files and item.get("complete_file") is True and len(text) <= 16000
        start = 1 if complete else line
        if not complete:
            text = "\n".join(lines[line - 1 : line + 31])[:2400]
        if len(text) > budget:
            continue
        selected.append({"ref": ref, "line": start, "text": text, "complete_file": complete})
        used.add(ref)
        budget -= len(text)
        if len(selected) >= (4 if whole_files else 2):
            break
    return selected


def verify_lookup(result, repo: Path, context: GitContext, *, whole_files=False):
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
        }:
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
        evidence = _local_reads(repo, sources, result.get("evidence"), whole_files)
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
    return answer
