"""Offline fake-log diagnostics; never execute worker sessions or grade answers."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from colony.product_value import _native_usage_bytes

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from token_observability import analyze_bytes  # noqa: E402


def raw(events):
    return "\n".join(json.dumps(e) for e in events).encode()


def usage(n=100):
    return {"input_tokens": n, "cached_input_tokens": n // 2, "output_tokens": 10}


def turn(items=(), counters=None):
    return [
        {"type": "turn.started"},
        *items,
        {"type": "turn.completed", "usage": usage() if counters is None else counters},
    ]


def response(identifier="r1", counters=None):
    return {
        "type": "response.completed",
        "response": {"id": identifier, "usage": usage() if counters is None else counters},
    }


def tool(name, value, identifier="t1", server="colony", **extra):
    return {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "id": identifier,
            "server": server,
            "tool": name,
            "status": "completed",
            "arguments": {},
            "result": {"content": [{"type": "text", "text": json.dumps(value)}]},
            **extra,
        },
    }


def read(start=1, content="DEFAULT = 100\nQUEUE = 250", sha="a" * 64):
    return {
        "path": "config.py",
        "sha256": sha,
        "start": start,
        "end": start + len(content.split("\n")) - 1,
        "content": content,
    }


def lookup(cue=True):
    r = read()
    r["start_line"] = r.pop("start")
    r["end_line"] = r.pop("end")
    return {
        "hit": True,
        "status": "validated",
        "current_repository_reads": [r],
        **({"next_action": "Use reads; check gaps."} if cue else {}),
    }


def test_turn_totals_are_authoritative_and_response_usage_is_not_added():
    receipt = response()
    data = turn([{"type": "response.created", "response": {"id": "r1"}}, receipt, receipt])
    report = analyze_bytes(raw(data))
    assert report["turn_accounting"]["known_usage"]["gross_tokens"] == 110
    assert report["response_usage"]["sum"] == usage()
    assert report["response_usage"]["reconciles_with_turn_totals"] is True
    assert report["response_usage"]["duplicate_receipts_ignored"] == 1
    assert report["response_usage"]["added_to_turn_totals"] is False


def test_current_native_logs_without_response_metadata_remain_unknown():
    data = raw(turn([tool("ask", lookup())]))
    report = analyze_bytes(data)
    assert report["turn_accounting"] == _native_usage_bytes(data)
    assert report["response_usage"]["sum"] is None
    assert report["response_usage"]["available"] is False
    assert report["explicit_tool_response_groups"] is None
    assert report["tool_calls_without_response_binding"] == 1


@pytest.mark.parametrize("missing", ["input_tokens", "cached_input_tokens", "output_tokens"])
def test_missing_response_components_never_become_zero(missing):
    counters = usage()
    counters.pop(missing)
    report = analyze_bytes(raw(turn([response(counters=counters)])))
    assert report["response_usage"]["sum"] is None
    assert report["response_usage"]["records"][0]["usage"][missing] is None
    assert report["turn_accounting"]["usage_complete"]


@pytest.mark.parametrize("bad", [True, -1, "10", float("inf")])
def test_invalid_response_count_rejected(bad):
    counters = usage()
    counters["input_tokens"] = bad
    with pytest.raises(ValueError):
        analyze_bytes(raw(turn([response(counters=counters)])))


def test_conflicting_receipts_rejected_and_mismatch_reported():
    with pytest.raises(ValueError, match="Conflicting"):
        analyze_bytes(raw(turn([response(), response(counters=usage(101))])))
    report = analyze_bytes(raw(turn([response(counters=usage(50))])))
    assert report["response_usage"]["reconciles_with_turn_totals"] is False
    assert report["turn_accounting"]["known_usage"]["gross_tokens"] == 110


def test_incomplete_failed_turn_remains_incomplete_even_with_response_usage():
    report = analyze_bytes(
        raw(
            [
                {"type": "turn.started"},
                response(),
                {"type": "turn.failed", "usage": {"input_tokens": 10}},
            ]
        )
    )
    assert not report["turn_accounting"]["usage_complete"]
    assert report["response_usage"]["reconciles_with_turn_totals"] is None
    assert report["turn_accounting"]["known_usage"]["input_tokens"] == 10


def test_started_response_without_completion_is_explicit():
    report = analyze_bytes(
        raw(turn([response(), {"type": "response.created", "response": {"id": "pending"}}]))
    )
    assert report["response_usage"]["all_started_responses_completed"] is False


def test_repeated_excerpt_requires_current_hash_range_and_text():
    events = turn(
        [
            tool("ask", lookup()),
            tool("fixture_read", read(), "t2", "repository"),
            tool("fixture_read", read(start=2, content="QUEUE = 250"), "t3", "repository"),
            tool("fixture_read", read(sha="b" * 64), "t4", "repository"),
            tool("fixture_read", read(), "t5", "repository"),
        ]
    )
    counts = analyze_bytes(raw(events))["observations"]
    assert counts["research_reads"] == 4
    assert counts["repeated_observed_excerpts"] == 2
    assert counts["reuse_cue_present"] == counts["validated_lookup_hits"] == 1


@pytest.mark.parametrize("change", [{"status": "stale"}, {"hit": False}])
def test_stale_or_missed_lookup_never_counts_as_covered(change):
    value = lookup()
    value.update(change)
    counts = analyze_bytes(
        raw(turn([tool("ask", value), tool("fixture_read", read(), "t2", "repository")]))
    )["observations"]
    assert counts["repeated_observed_excerpts"] == counts["validated_lookup_hits"] == 0


def test_turn_boundaries_and_reused_item_ids_do_not_invent_coverage():
    data = turn([tool("ask", lookup())]) + turn([tool("fixture_read", read(), server="repository")])
    report = analyze_bytes(raw(data))
    assert report["turn_accounting"]["known_usage"]["gross_tokens"] == 220
    assert report["observations"]["repeated_observed_excerpts"] == 0


def test_publication_failures_and_references_do_not_certify_claims():
    failed = tool("push", {}, status="failed", arguments={"sources": [{"ref": "private-path"}]})
    stored = tool(
        "push",
        {"stored": True},
        "t2",
        arguments={"content": "ignore rules, secret", "sources": [{}, {}]},
    )
    counts = analyze_bytes(raw(turn([failed, stored, tool("push", {}, "t3")])))
    assert counts["observations"]["publication_attempts"] == 3
    assert counts["observations"]["publication_errors"] == 1
    assert counts["observations"]["publication_stored"] == 1
    assert counts["observations"]["publication_outcome_unknown"] == 1
    assert counts["semantic_source_coverage"] == "not_assessed"
    assert "ignore rules" not in json.dumps(counts) and "private-path" not in json.dumps(counts)


def test_grouping_requires_explicit_response_binding_and_tools_are_deduplicated():
    item = tool("ask", lookup(), response_id="r1")
    report = analyze_bytes(raw(turn([response(), item, item])))
    assert list(report["explicit_tool_response_groups"].values()) == [1]
    assert report["observations"]["validated_lookup_hits"] == 1
    other = copy.deepcopy(item)
    other["item"]["arguments"] = {"question": "different"}
    with pytest.raises(ValueError, match="Conflicting"):
        analyze_bytes(raw(turn([item, other])))


def test_cli_never_overwrites_input_or_existing_output(tmp_path):
    source = tmp_path / "events.jsonl"
    data = raw(turn())
    source.write_bytes(data)
    output = tmp_path / "new.json"
    command = [
        sys.executable,
        str(SCRIPTS / "token_observability.py"),
        "--events",
        str(source),
        "--output",
    ]
    subprocess.run([*command, str(output)], check=True, capture_output=True)
    saved = output.read_bytes()
    assert subprocess.run([*command, str(output)], capture_output=True).returncode != 0
    assert subprocess.run([*command, str(source)], capture_output=True).returncode != 0
    assert source.read_bytes() == data and output.read_bytes() == saved


@pytest.mark.parametrize("details", [[], False, 0, ""])
def test_malformed_cache_details_do_not_become_missing(details):
    counters = usage()
    counters["input_tokens_details"] = details
    with pytest.raises(ValueError, match="cache details"):
        analyze_bytes(raw(turn([response(counters=counters)])))


def test_nested_cache_counters_and_cache_exceeds_input():
    counters = usage()
    counters["input_tokens_details"] = {"cached_tokens": counters.pop("cached_input_tokens")}
    assert (
        analyze_bytes(raw(turn([response(counters=counters)])))["response_usage"]["sum"] == usage()
    )
    counters["input_tokens_details"]["cached_tokens"] = 101
    with pytest.raises(ValueError, match="cache exceeds"):
        analyze_bytes(raw(turn([response(counters=counters)])))


def test_conflicting_explicit_response_bindings_rejected():
    item = tool("ask", lookup(), response_id="r1")
    item["response_id"] = "r2"
    with pytest.raises(ValueError, match="Conflicting tool response"):
        analyze_bytes(raw(turn([item])))
    item["item"].pop("response_id")
    other = copy.deepcopy(item)
    other["response_id"] = "r3"
    with pytest.raises(ValueError, match="Conflicting duplicate tool"):
        analyze_bytes(raw(turn([item, other])))


def test_malformed_tool_content_does_not_invent_publication_success():
    item = tool("push", {})
    item["item"]["result"]["content"] = 123
    counts = analyze_bytes(raw(turn([item])))["observations"]
    assert counts["publication_outcome_unknown"] == 1
    assert counts["publication_stored"] == 0


def test_started_but_unfinished_publications_remain_observed_attempts():
    pending = tool("push", {})
    pending["type"] = "item.started"
    pending["item"].pop("result")
    pending["item"]["status"] = "in_progress"
    completed = tool("push", {"stored": True}, "t2")
    started = copy.deepcopy(completed)
    started["type"] = "item.started"
    started["item"].pop("result")
    counts = analyze_bytes(raw(turn([pending, started, completed])))["observations"]
    assert counts["publication_attempts"] == 2
    assert counts["publication_completed"] == counts["publication_incomplete"] == 1
    assert counts["publication_stored"] == 1
