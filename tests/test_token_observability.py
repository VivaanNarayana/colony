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


def test_unicode_line_separators_inside_jsonl_strings(tmp_path):
    from token_evaluation.report import extract_answer
    from token_evaluation.usage import native_usage

    answer = "first\u2028second\u2029third\u0085last"
    events = turn([response(answer), {"type": "item.completed",
                  "item": {"type": "agent_message", "text": answer}}])
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"\r\n".join(json.dumps(e, ensure_ascii=False).encode() for e in events) + b"\r\n")
    assert extract_answer(path, "last_agent_message")["text"] == answer
    assert native_usage(path)["known_usage"]["gross_tokens"] == 110
    assert native_usage(path, request_metadata=True)["request_usage"]["reconciles_with_turn_totals"]


def test_deep_optional_payload_is_not_turn_accounting(tmp_path):
    from token_evaluation.report import extract_answer
    from token_evaluation.usage import native_usage

    deep = []
    for _ in range(70):
        deep = [deep]
    path = tmp_path / "events.jsonl"
    path.write_bytes(raw(turn([{**response(), "unused": deep}])))
    assert native_usage(path)["known_usage"]["gross_tokens"] == 110
    observed = native_usage(path, request_metadata=True)
    assert observed["usage_complete"] and observed["request_usage"]["status"] == "unavailable"
    output = tmp_path / "observation.json"
    subprocess.run([sys.executable, str(SCRIPTS / "token_observability.py"),
                    "--events", str(path), "--output", str(output)], check=True, capture_output=True)
    standalone = json.loads(output.read_bytes())
    assert standalone["turn_accounting"]["known_usage"]["gross_tokens"] == 110
    usual = analyze_bytes(raw(turn([response()])))
    assert standalone["response_usage"] == usual["response_usage"]
    final = {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}
    path.write_bytes(raw(turn([{"type": "item.completed", "item": {"type": "mcp_tool_call",
                               "structuredContent": deep}}, final])))
    assert extract_answer(path, "last_agent_message")["text"] == "done"
    duplicate_metadata = '{"type":"response.completed","unused":null,"unused":' + json.dumps(deep) + '}'
    path.write_bytes(raw(turn()).replace(b'\n', b'\n' + duplicate_metadata.encode() + b'\n', 1))
    assert native_usage(path, request_metadata=True)["known_usage"]["gross_tokens"] == 110
    for line in (json.dumps({"type": "item.completed", "unused": deep}),
                 '{"type":"item.completed","type":"response.completed","unused":' + json.dumps(deep) + '}'):
        path.write_bytes(raw(turn()).replace(b'\n', b'\n' + line.encode() + b'\n', 1))
        with pytest.raises(ValueError, match="nesting limit"):
            native_usage(path, request_metadata=True)
        with pytest.raises(ValueError):
            analyze_bytes(path.read_bytes())


@pytest.mark.parametrize("unreadable_first", [True, False])
def test_matched_reader_keeps_independent_receipts_when_one_is_too_deep(tmp_path, unreadable_first):
    from token_evaluation.usage import native_usage

    half = {"input_tokens": 50, "cached_input_tokens": 25, "output_tokens": 5}
    good = response("first", half)
    deep = []
    for _ in range(70):
        deep = [deep]
    other = {**response("second", half), "unused": deep}
    completions = [other, good] if unreadable_first else [good, other]
    events = [{"type": "response.created", "response": {"id": "second"}}, *completions]
    path = tmp_path / "events.jsonl"
    path.write_bytes(raw(turn(events)))
    result = native_usage(path, request_metadata=True)
    assert result["known_usage"]["gross_tokens"] == 110 and result["usage_complete"]
    metadata = result["request_usage"]
    assert metadata["status"] == "observed"
    assert metadata["records"] == analyze_bytes(raw(turn([good])))["response_usage"]["records"]
    assert metadata["complete_records"] is False
    assert metadata["reconciles_with_turn_totals"] is None
    assert metadata["all_started_responses_completed"] is None
    assert analyze_bytes(path.read_bytes())["response_usage"]["reconciles_with_turn_totals"] is True


@pytest.mark.parametrize("other_counts", [usage(60), {"input_tokens": -1}])
def test_matched_reader_quarantines_only_disputed_response_counts(other_counts):
    good = response("good")
    disputed = response("disputed", usage(50))
    events = [{"type": "response.created", "response": {"id": "disputed"}},
              good, disputed, response("disputed", other_counts), disputed]
    report = analyze_bytes(raw(turn(events)), optional_diagnostics=True)
    metadata = report["response_usage"]
    assert metadata["records"] == analyze_bytes(raw(turn([good])))["response_usage"]["records"]
    assert metadata["all_started_responses_completed"] is True
    assert not metadata["complete_records"] and metadata["sum"] is None
    assert metadata["reconciles_with_turn_totals"] is None
    with pytest.raises(ValueError):
        analyze_bytes(raw(turn(events)))


@pytest.mark.parametrize("extra", [b'"unused":0,"unused":1', b'"unused":1e400'])
@pytest.mark.parametrize("bad_first", [False, True])
def test_unreadable_optional_envelope_preserves_other_receipts(tmp_path, extra, bad_first):
    from token_evaluation.usage import native_usage

    good = json.dumps(response("good")).encode()
    bad = b'{"type":"response.completed","response":{"id":"other"},' + extra + b'}'
    first, last = raw(turn()).split(b"\n")
    receipts = [bad, good] if bad_first else [good, bad]
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"\n".join([first, *receipts, last]))
    report = native_usage(path, request_metadata=True)
    assert report["usage_complete"] and report["known_usage"]["gross_tokens"] == 110
    assert native_usage(path)["known_usage"] == report["known_usage"]
    meta = report["request_usage"]
    assert meta["records"] == analyze_bytes(raw(turn([response("good")])))["response_usage"]["records"]
    assert not meta["complete_records"] and meta["reconciles_with_turn_totals"] is None
    with pytest.raises(ValueError):
        analyze_bytes(path.read_bytes())


@pytest.mark.parametrize("bad", [
    b'{"type":"turn.completed","usage":{},"usage":{}}',
    b'{"type":"turn.failed","type":"response.completed"}',
    b'{"type":"response.completed","unused":0,}',
])
def test_optional_receipt_recovery_rejects_invalid_or_ambiguous_native_events(tmp_path, bad):
    from token_evaluation.usage import native_usage

    path = tmp_path / "events.jsonl"
    path.write_bytes(raw(turn()).replace(b"\n", b"\n" + bad + b"\n", 1))
    for metadata in (True, False):
        with pytest.raises(ValueError):
            native_usage(path, request_metadata=metadata)


@pytest.mark.parametrize("bad_first", [True, False])
@pytest.mark.parametrize("damage", [b'"unused":0,"unused":1', b'"unused":1e400',
                                    b'"unused":' + b'[' * 1200 + b'0' + b']' * 1200],
                         ids=["duplicate-key", "out-of-range-number", "deep-payload"])
def test_rejected_duplicate_withholds_only_its_own_response_counts(tmp_path, bad_first, damage):
    from token_evaluation.usage import native_usage

    # The nested unrelated ID must never shadow the immediate response.id.
    other = json.dumps(response("peer")).encode()
    disputed = json.dumps(response("r1", usage(90))).encode()
    bad = (b'{"response":{"metadata":{"id":"peer"},"\\u0069d":"r1","usage":{"input_tokens":60}},'
           + damage + b',"type":"response.completed"}')
    records = [bad, disputed, other] if bad_first else [disputed, other, bad, disputed]
    first, last = raw(turn()).splitlines()
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"\n".join([first, *records, last]))
    data = native_usage(path, request_metadata=True)
    meta = data["request_usage"]
    assert data["usage_complete"] and data["known_usage"]["gross_tokens"] == 110
    assert meta["records"] == analyze_bytes(raw(turn([response("peer")])))["response_usage"]["records"]
    assert not meta["complete_records"] and meta["reconciles_with_turn_totals"] is None


def test_rejected_created_receipt_preserves_completed_counts(tmp_path):
    from token_evaluation.usage import native_usage

    first, good, last = raw(turn([response("r1")])).splitlines()
    bad = b'{"type":"response.created","response":{"id":"r1"},"unused":0,"unused":1}'
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"\n".join([first, good, bad, last]))
    meta = native_usage(path, request_metadata=True)["request_usage"]
    expected = analyze_bytes(raw(turn([response("r1")])))["response_usage"]
    assert meta["records"] == expected["records"]
    assert meta["reconciles_with_turn_totals"] is True
    assert meta["all_started_responses_completed"] is None


@pytest.mark.parametrize("fields", [
    b'"response":{"id":"peer","\\u0069d":"r1"}',
    b'"response":{"id":"r1"},"\\u0072esponse":{"id":"peer"}',
    b'"response":{"metadata":{"id":"peer"}}', b'"response":[{"id":"peer"}]',
    b'"response":{"id":{"id":"peer"}}', b'"other":{"response":{"id":"peer"}}',
])
def test_unreadable_receipt_cannot_withhold_counts_using_an_ambiguous_id(tmp_path, fields):
    from token_evaluation.usage import native_usage

    first, good, last = raw(turn([response("peer")])).splitlines()
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"\n".join([first, good, b'{"type":"response.completed","unused":1e400,' + fields + b'}', last]))
    meta = native_usage(path, request_metadata=True)["request_usage"]
    assert meta["records"] == analyze_bytes(raw(turn([response("peer")])))["response_usage"]["records"]
    assert meta["reconciles_with_turn_totals"] is None


@pytest.mark.parametrize("bad_first", [True, False])
@pytest.mark.parametrize("binding", ["conflicting_fields", "invalid_unicode", "disputed_replay"])
def test_disputed_tool_binding_preserves_independent_groups(tmp_path, binding, bad_first):
    from token_evaluation.usage import native_usage

    good = tool("read", {}, "stable", response_id="r1")
    other = tool("read", {}, "disputed", response_id="r2")
    if binding == "disputed_replay":
        disputed = [other, tool("read", {}, "disputed", response_id="r3"), other]
    elif binding == "invalid_unicode":
        disputed = [tool("read", {}, "disputed", response_id="\ud800")]
    else:
        disputed = [{**other, "response_id": "r3"}]
    # Duplicates count once. The same item ID is independent in a new turn.
    stable = [good, good]
    calls = disputed + stable if bad_first else stable + disputed
    later = tool("read", {}, "disputed", response_id="r1")
    path = tmp_path / "events.jsonl"
    path.write_bytes(raw(turn([response("r1"), *calls]) + turn([later])))
    report = native_usage(path, request_metadata=True)
    meta = report["request_usage"]
    assert meta["tool_groups"] == {meta["records"][0]["response_id_sha256"]: 2}
    assert meta["ungrouped_calls"] is None and report["known_usage"]["gross_tokens"] == 220
    with pytest.raises(ValueError):
        analyze_bytes(path.read_bytes())


def test_optional_envelope_beyond_python_recursion_limit(tmp_path):
    from token_evaluation.report import activity, extract_answer
    from token_evaluation.usage import native_usage

    path = tmp_path / "events.jsonl"
    deep = '{"nested":[' * 2000 + json.dumps('},"type":"turn.failed"') + ']}' * 2000
    record = '{"response":' + deep + ',"type":"response.completed"}'
    path.write_bytes(raw(turn()).replace(b'\n', b'\n' + record.encode() + b'\n', 1))
    assert native_usage(path)["known_usage"]["gross_tokens"] == 110
    assert native_usage(path, request_metadata=True)["request_usage"]["status"] == "unavailable"
    assert extract_answer(path, "last_agent_message")["text"] == ""
    assert activity(path)["research_calls"] == 0
    for invalid in (record.replace(']}', ',]}', 1), record + ' true',
                    record[:-1] + ',"t\\u0079pe":"turn.failed"}'):
        path.write_bytes(raw(turn()).replace(b'\n', b'\n' + invalid.encode() + b'\n', 1))
        with pytest.raises(ValueError, match="nesting limit"):
            native_usage(path)


def test_prospective_reader_preserves_turn_totals_and_explicit_request_metadata(tmp_path):
    from token_evaluation.usage import native_usage

    path = tmp_path / "events.jsonl"
    path.write_bytes(raw(turn()))
    missing = native_usage(path, request_metadata=True)
    assert missing["request_usage"]["status"] == "unavailable"
    response = {"type": "response.completed", "response": {"id": "r1", "usage": usage()}}
    item = {"type": "item.completed", "response_id": "r1",
            "item": {"type": "mcp_tool_call", "id": "t1", "server": "colony", "tool": "read"}}
    path.write_bytes(raw(turn([response, item])))
    observed = native_usage(path, request_metadata=True)
    assert observed["known_usage"] == missing["known_usage"] == native_usage(path)["known_usage"]
    metadata = observed["request_usage"]
    assert metadata["records"][0]["usage"] == usage()
    assert metadata["tool_groups"] == {metadata["records"][0]["response_id_sha256"]: 1}
    assert metadata["reconciles_with_turn_totals"] is True
    del item["item"]["id"]
    path.write_bytes(raw(turn([response, item])))
    incomplete = native_usage(path, request_metadata=True)
    assert native_usage(path)["known_usage"] == incomplete["known_usage"] == missing["known_usage"]
    assert incomplete["request_usage"]["records"] == metadata["records"]
    assert incomplete["request_usage"]["tool_groups"] is None
    item["item"]["id"] = "t1"
    item["response_id"] = "\ud800"
    path.write_bytes(raw(turn([response, item])))
    malformed = native_usage(path, request_metadata=True)
    assert native_usage(path)["known_usage"] == malformed["known_usage"] == missing["known_usage"]
    assert malformed["request_usage"]["records"] == metadata["records"]
    assert malformed["request_usage"]["tool_groups"] is None
    path.write_bytes(raw(turn([response, {"type": "unexpected.event"}])))
    with pytest.raises(ValueError, match="Unsupported native event format"):
        native_usage(path)


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


@pytest.mark.parametrize(
    "start,content",
    [
        (3, "NEW_GAP = 1"),
        (1, "DEFAULT = 100\nQUEUE = 250\nNEW_GAP = 1"),
        (2, "QUEUE = 250\nNEW_GAP = 1"),
    ],
)
def test_same_hash_gap_or_extended_read_preserves_lookup_coverage(start, content):
    events = turn(
        [
            tool("ask", lookup()),
            tool("fixture_read", read(start=start, content=content), "gap", "repository"),
            tool("fixture_read", read(), "repeat", "repository"),
            tool("fixture_read", read(start=start, content=content), "gap-again", "repository"),
        ]
    )
    counts = analyze_bytes(raw(events))["observations"]
    assert counts["research_reads"] == 3
    # New research never becomes lookup coverage; only the original excerpt repeats.
    assert counts["repeated_observed_excerpts"] == 1


def test_gap_before_lookup_excerpt_preserves_coverage():
    value = lookup()
    value["current_repository_reads"][0]["start_line"] = 3
    value["current_repository_reads"][0]["end_line"] = 4
    counts = analyze_bytes(
        raw(
            turn(
                [
                    tool("ask", value),
                    tool("fixture_read", read(start=1, content="OTHER = 1"), "gap", "repository"),
                    tool("fixture_read", read(start=3), "repeat", "repository"),
                ]
            )
        )
    )["observations"]
    assert counts["repeated_observed_excerpts"] == 1


def test_conflicting_overlap_invalidates_only_affected_lookup_span():
    value = lookup()
    value["current_repository_reads"].append(
        {
            "path": "config.py",
            "sha256": "a" * 64,
            "start_line": 5,
            "end_line": 5,
            "content": "SAFE = 1",
        }
    )
    counts = analyze_bytes(
        raw(
            turn(
                [
                    tool("ask", value),
                    tool(
                        "fixture_read",
                        read(start=2, content="QUEUE = 999"),
                        "conflict",
                        "repository",
                    ),
                    tool("fixture_read", read(), "invalidated", "repository"),
                    tool(
                        "fixture_read",
                        read(start=5, content="SAFE = 1"),
                        "unaffected",
                        "repository",
                    ),
                ]
            )
        )
    )["observations"]
    assert counts["repeated_observed_excerpts"] == 1


def test_changed_hash_on_gap_read_invalidates_old_lookup_coverage():
    counts = analyze_bytes(
        raw(
            turn(
                [
                    tool("ask", lookup()),
                    tool(
                        "fixture_read",
                        read(start=3, content="NEW_GAP = 1", sha="b" * 64),
                        "changed",
                        "repository",
                    ),
                    tool("fixture_read", read(), "old-again", "repository"),
                ]
            )
        )
    )["observations"]
    assert counts["repeated_observed_excerpts"] == 0
