"""Read saved native logs into a separate diagnostic report; never collect or grade."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from colony.product_value import MAX_BYTES, _native_usage_bytes
from colony.report_io import bounded_bytes

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")
RESPONSE_EVENTS = {"response.created", "response.completed"}


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def response_usage(response):
    usage = response.get("usage")
    if usage is None:
        return None
    if not isinstance(usage, dict):
        raise ValueError("Malformed response usage")
    details = usage.get("input_tokens_details")
    if details is None:
        details = {}
    if not isinstance(details, dict):
        raise ValueError("Malformed response cache details")
    values = {
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": usage.get("cached_input_tokens", details.get("cached_tokens")),
        "output_tokens": usage.get("output_tokens"),
    }
    if any(v is not None and (type(v) is not int or v < 0) for v in values.values()):
        raise ValueError("Invalid response token count")
    if (
        values["input_tokens"] is not None
        and values["cached_input_tokens"] is not None
        and values["cached_input_tokens"] > values["input_tokens"]
    ):
        raise ValueError("Response cache exceeds input")
    return values


def outcome(item):
    result = item.get("result")
    result = result if isinstance(result, dict) else {}
    error = bool(
        item.get("error")
        or result.get("isError")
        or result.get("is_error")
        or item.get("status") == "failed"
    )
    structured = result.get("structured_content") or result.get("structuredContent")
    if isinstance(structured, dict):
        return structured, error
    blocks = result.get("content")
    if not isinstance(blocks, list):
        return {}, error
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                value = json.loads(block.get("text", ""))
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict):
                return value, error
    return {}, error


def excerpt(value, lookup=False):
    if not isinstance(value, dict):
        return None
    path, sha = value.get("path"), value.get("sha256")
    start = value.get("start_line" if lookup else "start")
    text = value.get("content")
    if (
        not isinstance(path, str)
        or not isinstance(sha, str)
        or not re.fullmatch("[a-f0-9]{64}", sha)
        or type(start) is not int
        or start < 1
        or not isinstance(text, str)
        or not text
    ):
        return None
    lines = text.split("\n")
    end = start + len(lines) - 1
    declared_end = value.get("end_line" if lookup else "end")
    if declared_end is not None and (type(declared_end) is not int or declared_end != end):
        return None
    return path, sha, start, end, lines


def consistent_coverage(previous, observed):
    """Retain lookup spans consistent with this read; never add research coverage."""
    _, sha, start, end, lines = observed
    retained = []
    for old in previous:
        _, old_sha, old_start, old_end, old_lines = old
        if old_sha != sha:
            continue
        first, last = max(start, old_start), min(end, old_end)
        if first <= last and (
            lines[first - start : last - start + 1]
            != old_lines[first - old_start : last - old_start + 1]
        ):
            continue
        retained.append(old)
    return retained


def analyze_bytes(source):
    if len(source) > MAX_BYTES:
        raise ValueError("Native log exceeds size limit")
    events = [json.loads(line) for line in source.decode("utf-8").splitlines()]
    if any(not isinstance(e, dict) for e in events):
        raise ValueError("Native event must be an object")
    # Additional response receipts are observations, never extra billable turn totals.
    turn_bytes = "\n".join(
        json.dumps(e, ensure_ascii=False) for e in events if e.get("type") not in RESPONSE_EVENTS
    ).encode("utf-8")
    authority = _native_usage_bytes(turn_bytes)
    responses, started, duplicates, tool_groups = {}, set(), 0, {}
    counts = dict.fromkeys(
        (
            "validated_lookup_hits",
            "reuse_cue_present",
            "lookup_excerpt_count",
            "research_reads",
            "repeated_observed_excerpts",
            "publication_attempts",
            "publication_completed",
            "publication_incomplete",
            "publication_errors",
            "publication_stored",
            "publication_outcome_unknown",
            "publication_declared_sources",
        ),
        0,
    )
    covered, tools_seen, turn_index = {}, {}, 0
    observed_calls = {}
    for event in events:
        kind = event.get("type")
        if kind in RESPONSE_EVENTS:
            response = event.get("response")
            if (
                not isinstance(response, dict)
                or not isinstance(response.get("id"), str)
                or not response["id"]
            ):
                raise ValueError("Response receipt requires an ID")
            identity = digest(response["id"])
            if kind == "response.created":
                started.add(identity)
                continue
            usage = response_usage(response)
            if identity in responses:
                if responses[identity] != usage:
                    raise ValueError("Conflicting duplicate response receipt")
                duplicates += 1
            responses[identity] = usage
            continue
        if kind == "turn.started":
            covered = {}
            turn_index += 1
        if kind not in {"item.started", "item.updated", "item.completed"}:
            continue
        if not isinstance(event.get("item"), dict):
            raise ValueError("Malformed tool observation")
        if event["item"].get("type") != "mcp_tool_call":
            continue
        item = event["item"]
        identity = item.get("id")
        if not isinstance(identity, str) or not identity:
            raise ValueError("Tool observation requires an ID")
        identity = (turn_index, identity)
        server, tool = item.get("server"), item.get("tool")
        signature = (server, tool)
        if identity in observed_calls and observed_calls[identity] != signature:
            raise ValueError("Tool observation identity changed")
        if identity not in observed_calls:
            counts["publication_attempts"] += signature == ("colony", "push")
            counts["research_reads"] += signature == ("repository", "fixture_read")
        observed_calls[identity] = signature
        if kind != "item.completed":
            if identity in tools_seen:
                raise ValueError("Tool event after completion")
            continue
        event_binding, item_binding = event.get("response_id"), item.get("response_id")
        if event_binding is not None and item_binding is not None and event_binding != item_binding:
            raise ValueError("Conflicting tool response bindings")
        response_id = event_binding if event_binding is not None else item_binding
        if response_id is not None and (not isinstance(response_id, str) or not response_id):
            raise ValueError("Invalid tool response binding")
        fingerprint = digest(json.dumps([item, response_id], sort_keys=True, ensure_ascii=False))
        if identity in tools_seen:
            if tools_seen[identity] != fingerprint:
                raise ValueError("Conflicting duplicate tool observation")
            continue
        tools_seen[identity] = fingerprint
        if isinstance(response_id, str) and response_id:
            group = digest(response_id)
            tool_groups[group] = tool_groups.get(group, 0) + 1
        value, error = outcome(item)
        if server == "colony" and tool == "ask":
            # Keep only the latest lookup's explicit current excerpts; no cross-turn inference.
            covered = {}
            reads = value.get("current_repository_reads")
            if (
                not error
                and value.get("hit") is True
                and value.get("status") == "validated"
                and isinstance(reads, list)
            ):
                counts["validated_lookup_hits"] += 1
                counts["reuse_cue_present"] += isinstance(value.get("next_action"), str) and bool(
                    value["next_action"]
                )
                for read in reads:
                    record = excerpt(read, lookup=True)
                    if record:
                        covered.setdefault(record[0], []).append(record)
                        counts["lookup_excerpt_count"] += 1
        elif server == "repository" and tool == "fixture_read":
            record = None if error else excerpt(value)
            if record:
                path, sha, start, end, lines = record
                retained = consistent_coverage(covered.get(path, []), record)
                repeated = any(
                    sha == old_sha
                    and old_start <= start <= end <= old_end
                    and lines == old_lines[start - old_start : end - old_start + 1]
                    for _, old_sha, old_start, old_end, old_lines in retained
                )
                counts["repeated_observed_excerpts"] += repeated
                covered[path] = retained
        elif server == "colony" and tool == "push":
            counts["publication_completed"] += 1
            counts["publication_errors"] += error
            counts["publication_stored"] += not error and value.get("stored") is True
            counts["publication_outcome_unknown"] += (
                not error and type(value.get("stored")) is not bool
            )
            args = item.get("arguments")
            args = args if isinstance(args, dict) else {}
            sources = args.get("sources")
            counts["publication_declared_sources"] += (
                len(sources) if isinstance(sources, list) else 0
            )
    counts["publication_incomplete"] = (
        counts["publication_attempts"] - counts["publication_completed"]
    )
    complete_records = bool(responses) and all(
        u is not None and all(u[k] is not None for k in FIELDS) for u in responses.values()
    )
    summed = (
        {k: sum(u[k] for u in responses.values()) for k in FIELDS} if complete_records else None
    )
    reconciles = (
        all(summed[k] == authority["known_usage"][k] for k in FIELDS)
        if summed is not None and authority["usage_complete"]
        else None
    )
    return {
        "contract": "colony.token-observability/1",
        "events_sha256": hashlib.sha256(source).hexdigest(),
        "turn_accounting": authority,
        "response_usage": {
            "records": [{"response_id_sha256": k, "usage": u} for k, u in responses.items()],
            "available": bool(responses),
            "complete_records": complete_records,
            "all_started_responses_completed": started <= responses.keys() if started else None,
            "sum": summed,
            "reconciles_with_turn_totals": reconciles,
            "duplicate_receipts_ignored": duplicates,
            "added_to_turn_totals": False,
        },
        "explicit_tool_response_groups": tool_groups or None,
        "tool_calls_without_response_binding": len(tools_seen) - sum(tool_groups.values()),
        "observations": counts,
        "semantic_source_coverage": "not_assessed",
        "limits": [
            "Observed repeated excerpts may be intentional verification, not proven waste.",
            "Missing per-response usage or grouping remains unknown; no allocation is inferred.",
            "Publication references and cue presence do not establish claim support or obedience.",
            "This additive diagnostic report does not modify grades, protocols or acceptance.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze_bytes(bounded_bytes(args.events, MAX_BYTES))
    # Exclusive creation protects historical outputs and the input itself.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)


if __name__ == "__main__":
    main()
