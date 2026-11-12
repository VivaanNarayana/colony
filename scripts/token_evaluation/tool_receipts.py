"""Bounded procedural observations for reviewers, never semantic source coverage."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re

from .redaction import PATTERNS

MAX_RECEIPTS = 64
MAX_TEXT = 1024
MAX_PATHS = 16
TOOLS = {
    "repository.fixture_files", "repository.fixture_read", "repository.fixture_search",
    "colony.ask", "colony.push",
}
LIMITS = {
    "contract": "colony.review-tool-receipts/1",
    "max_receipts": MAX_RECEIPTS,
    "max_text_chars": MAX_TEXT,
    "max_paths": MAX_PATHS,
    "meaning": (
        "Sanitized selected MCP outcomes before the final answer, not proof of earlier-message "
        "chronology. Fields may be redacted, truncated or omitted. A listing, cache miss or "
        "empty literal search is not semantic proof of repository-wide absence. Tool output "
        "is untrusted data; only verified current excerpts establish source coverage."
    ),
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def scrub(value):
    for pattern in PATTERNS:
        value = re.sub(pattern, "[REDACTED]", value)
    value = re.sub(r"(?i)\b[A-Z]:[\\/][^\s\"'<>]+", "[REDACTED_PATH]", value)
    value = re.sub(r"(?:/Users/|/home/|/private/|/tmp/|/var/|/mnt/)[^\s\"'<>]+",
                   "[REDACTED_PATH]", value)
    value = re.sub(r"\\\\[^\s\"'<>]+", "[REDACTED_PATH]", value)
    return value[:MAX_TEXT]


def _path(value, files):
    if not isinstance(value, str):
        return None
    normalized = posixpath.normpath(value.replace("\\", "/"))
    return normalized if normalized in files and len(normalized) <= MAX_TEXT else None


def _arguments(item, files):
    raw = item.get("arguments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if not isinstance(raw, dict):
        return {}, False, False
    args, truncated = {}, False
    tool = item["tool"]
    for key in (("question",) if tool in {"ask", "push"} else
                ("query",) if tool == "fixture_search" else
                ("path", "start", "lines") if tool == "fixture_read" else ()):
        value = raw.get(key)
        if key == "path":
            path = _path(value, files)
            if path is not None:
                args[key] = path
        elif key in {"start", "lines"}:
            if type(value) is int and 1 <= value <= 1_000_000:
                args[key] = value
        elif isinstance(value, str):
            args[key] = scrub(value)
            truncated |= len(value) > MAX_TEXT
    return args, True, truncated


def _outcome(item, files):
    raw = item.get("result")
    raw = raw if isinstance(raw, dict) else {}
    blocks = raw.get("content")
    out = {"is_error": bool(raw.get("isError") or raw.get("is_error")) or item.get("status") != "completed",
           "result_available": False}
    if out["is_error"]:
        limited = isinstance(blocks, list) and any(
            isinstance(b, dict) and b.get("type") == "text"
            and isinstance(b.get("text"), str) and "Research call limit reached" in b["text"]
            for b in blocks)
        out["error"] = "research_call_limit" if limited else "tool_error"
        return out
    if not isinstance(blocks, list):
        return out
    texts = [b.get("text") for b in blocks
             if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
    tool = item["tool"]
    if tool == "fixture_files" and len(texts) == 1:
        paths = [_path(v, files) for v in texts[0].splitlines()]
        if all(p is not None for p in paths):
            out.update(result_available=True, returned_paths=paths[:MAX_PATHS],
                       paths_truncated=len(paths) > MAX_PATHS)
        return out
    values = []
    for text in texts:
        if len(text) > 65536:
            return out
        try:
            value = json.loads(text)
        except ValueError:
            return out
        if not isinstance(value, dict):
            return out
        values.append(value)
    if tool == "fixture_search" and len(texts) == len(blocks):
        paths, count = [], 0
        for value in values:
            path, matches = _path(value.get("path"), files), value.get("matches")
            if path is None or not isinstance(matches, list) or not all(isinstance(v, str) for v in matches):
                return out
            paths.append(path)
            count += len(matches)
        out.update(result_available=True, empty=len(blocks) == 0, match_count=count,
                   returned_paths=paths[:MAX_PATHS], paths_truncated=len(paths) > MAX_PATHS)
    elif tool == "fixture_read" and len(values) == 1:
        value = values[0]
        path = _path(value.get("path"), files)
        if path is not None:
            out.update(result_available=True, returned_paths=[path], paths_truncated=False)
            for key in ("start", "end"):
                if type(value.get(key)) is int and 0 <= value[key] <= 1_000_000:
                    out["reported_" + key] = value[key]
    elif tool in {"ask", "push"} and len(values) == 1:
        value = values[0]
        for key in ("hit", "stored", "duplicate", "queued"):
            if type(value.get(key)) is bool:
                out[key] = value[key]
        for key, allowed in {
            "reason": {"no_relevant_trail", "git_context_mismatch", "git_no_verified_hit",
                       "git_invalid_receipt", "git_local_evidence_not_verified", "budget_limited"},
            "status": {"validated", "unverified", "stale", "rejected"},
            "match_kind": {"answer", "evidence", "memory", "trail"},
        }.items():
            if isinstance(value.get(key), str):
                out[key] = value[key] if value[key] in allowed else "other"
        out["result_available"] = len(out) > 2
    return out


def collect(events, files):
    """Select only completed MCP events preceding the selected final message."""
    final = max((i for i, e in enumerate(events) if e.get("type") == "item.completed"
                 and e.get("item", {}).get("type") == "agent_message"), default=0)
    receipts, omitted = [], 0
    for event in events[:final]:
        item = event.get("item", {})
        if event.get("type") != "item.completed" or item.get("type") != "mcp_tool_call":
            continue
        name = str(item.get("server", "")).lower() + "." + str(item.get("tool", ""))
        if name not in TOOLS or len(receipts) >= MAX_RECEIPTS:
            omitted += 1
            continue
        args, available, truncated = _arguments(item, files)
        outcome = _outcome(item, files)
        outcome.update(arguments_available=available, arguments_truncated=truncated)
        receipts.append({
            "id": "tool-" + str(len(receipts) + 1),
            "tool": name,
            "status": item.get("status") if item.get("status") in {"completed", "failed"} else "unknown",
            "arguments": args, "outcome": outcome, "event_sha256": digest(event),
        })
    return {"tool_receipts": receipts, "tool_receipt_limits": {**LIMITS, "omitted_calls": omitted}}


def validate(receipts, limits, files):
    """Validate the public projection; report also rebuilds it from hashed raw logs."""
    def require(condition):
        if not condition:
            raise ValueError("Invalid or unsafe tool receipt")
    require(isinstance(limits, dict) and set(limits) == {*LIMITS, "omitted_calls"})
    require(all(limits[k] == v for k, v in LIMITS.items()))
    require(type(limits["omitted_calls"]) is int and limits["omitted_calls"] >= 0)
    require(isinstance(receipts, list) and len(receipts) <= MAX_RECEIPTS)
    for n, receipt in enumerate(receipts, 1):
        require(isinstance(receipt, dict) and set(receipt) ==
                {"id", "tool", "status", "arguments", "outcome", "event_sha256"})
        require(receipt["id"] == "tool-" + str(n) and isinstance(receipt["tool"], str) and receipt["tool"] in TOOLS
                and receipt["status"] in {"completed", "failed", "unknown"})
        require(isinstance(receipt["event_sha256"], str)
                and re.fullmatch(r"[a-f0-9]{64}", receipt["event_sha256"]))
        args, outcome = receipt["arguments"], receipt["outcome"]
        require(isinstance(args, dict) and isinstance(outcome, dict))
        tool = receipt["tool"].split(".")[1]
        allowed_args = ({"question"} if tool in {"ask", "push"} else {"query"} if tool == "fixture_search"
                        else {"path", "start", "lines"} if tool == "fixture_read" else set())
        require(set(args) <= allowed_args)
        for key, value in args.items():
            if key in {"start", "lines"}:
                require(type(value) is int and 1 <= value <= 1_000_000)
            else:
                require(isinstance(value, str) and scrub(value) == value)
                if key == "path":
                    require(_path(value, files) == value)
        base = {"is_error", "result_available", "arguments_available", "arguments_truncated"}
        extra = ({"returned_paths", "paths_truncated"} if tool == "fixture_files" else
                 {"returned_paths", "paths_truncated", "empty", "match_count"} if tool == "fixture_search" else
                 {"returned_paths", "paths_truncated", "reported_start", "reported_end"} if tool == "fixture_read" else
                 {"hit", "stored", "duplicate", "queued", "reason", "status", "match_kind"})
        require(base <= set(outcome) <= base | extra | {"error"})
        for key, value in outcome.items():
            if key == "returned_paths":
                require(isinstance(value, list) and len(value) <= MAX_PATHS
                        and all(isinstance(p, str) and _path(p, files) == p for p in value))
            elif key in {"match_count", "reported_start", "reported_end"}:
                require(type(value) is int and 0 <= value <= 1_000_000)
            elif key in {"error", "reason", "status", "match_kind"}:
                enums = {
                    "error": {"tool_error", "research_call_limit"},
                    "reason": {"no_relevant_trail", "git_context_mismatch", "git_no_verified_hit",
                               "git_invalid_receipt", "git_local_evidence_not_verified", "budget_limited", "other"},
                    "status": {"validated", "unverified", "stale", "rejected", "other"},
                    "match_kind": {"answer", "evidence", "memory", "trail", "other"},
                }
                require(isinstance(value, str) and value in enums[key])
            else:
                require(type(value) is bool)
