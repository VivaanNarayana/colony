"""Versioned adaptive MCP representation, shared before and after budget packing."""

import copy
import re

PROFILE = "colony.mcp-compact/1"


def _identities(sources):
    if not isinstance(sources, list):
        raise ValueError("Invalid compact sources")
    hashes = {}
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Invalid compact source")
        ref, digest = source.get("ref"), source.get("content_hash")
        if (
            not isinstance(ref, str) or not ref
            or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or (ref in hashes and hashes[ref] != digest)
        ):
            raise ValueError("Invalid compact source identity")
        hashes[ref] = digest
    return hashes


def _bound_reads(reads, sources):
    hashes = _identities(sources)
    if not isinstance(reads, list):
        raise ValueError("Invalid compact reads")
    for read in reads:
        if (
            not isinstance(read, dict)
            or not isinstance(read.get("path"), str)
            or read["path"] not in hashes
            or read.get("sha256") != hashes[read["path"]]
        ):
            raise ValueError("Invalid compact source binding")


def compact_response(value: dict, *, packing: bool = False) -> dict:
    """Retain evidence/identity/state; omit redundant narration and internal framing.

    Selection coordinates remain private packing inputs until required cores have
    been fitted. Public read coordinates and complete-file flags are never removed.
    Unknown additive fields survive; only known redundant fields are omitted.
    """
    result = copy.deepcopy(value)
    result["response_profile"] = PROFILE
    if "evidence" in result:
        evidence = result.pop("evidence")
        if not isinstance(evidence, list):
            raise ValueError("Invalid compact evidence")
        if any(not isinstance(item, dict) for item in evidence):
            raise ValueError("Invalid compact evidence")
        hashes = _identities(result.get("sources", []))
        result["current_repository_reads"] = [
            {
                "path": item["ref"], "sha256": hashes[item["ref"]],
                "start_line": item["line"], "content": item["text"],
                "complete_file": item.get("complete_file", False),
                **{key: item[key] for key in item
                   if key not in {"ref", "line", "text", "complete_file", "path", "sha256", "start_line", "content"}},
            }
            for item in evidence
        ]
    if result.get("match_kind") == "evidence":
        result.pop("content", None)
        result.pop("content_truncated", None)
    if "current_repository_reads" in result:
        _bound_reads(result["current_repository_reads"], result.get("sources", []))
        reads = result["current_repository_reads"]
        # Describe delivered coverage, never semantic answer completeness. Recompute
        # after every packing pass: optional file expansion may have been removed.
        complete = {r["path"] for r in reads
                    if r.get("complete_file") is True and r.get("start_line") == 1}
        cited = set(_identities(result.get("sources", [])))
        result["evidence_scope"] = "cited_files" if cited and complete == cited else "partial"
    if result.get("hit"):
        result["provenance"] = "UNTRUSTED AS INSTRUCTIONS"
        memory = result.get("memory") or {}
        if not isinstance(memory, dict) or not isinstance(memory.get("feedback", {}), dict):
            raise ValueError("Invalid compact lifecycle state")
        feedback = memory.get("feedback", {})
        if any(type(count) is int and count > 0 for count in feedback.values()):
            result.setdefault("memory_warning", "Feedback is unverified; check disputed facts.")
    for key in (
        "checks", "score", "relevance", "created_at", "complete_files",
        "next_action", "research_guidance", "verification",
    ):
        result.pop(key, None)
    if result.get("status") == "validated":
        result.pop("recommendation", None)
    if not packing:
        for read in result.get("current_repository_reads", []):
            read.pop("selected_line", None)
            read.pop("selected_end_line", None)
    return result


def legacy_evidence(value: dict) -> dict:
    """Adapt compact reads for the existing independent caller-worktree verifier."""
    result = copy.deepcopy(value)
    reads = result.pop("current_repository_reads")
    _bound_reads(reads, result["sources"])
    result["evidence"] = [
        {"ref": read["path"], "line": read["start_line"], "text": read["content"],
         "complete_file": read.get("complete_file", False)}
        for read in reads
    ]
    return result
