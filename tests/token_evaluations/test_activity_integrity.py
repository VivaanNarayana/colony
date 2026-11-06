"""Both arms must have an unambiguous, equally counted tool-call inventory."""
import json

import pytest

from scripts.token_evaluation.report import activity


def observed(tmp_path, events):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
    return activity(path)


def event(stage="completed", **changes):
    item = {"id": "call", "type": "mcp_tool_call", "server": "repository",
            "tool": "fixture_read", "status": "completed"}
    item.update(changes)
    return {"type": "item." + stage, "item": item}


@pytest.mark.parametrize("server,tool", [("repository", "fixture_read"),
                                        ("colony", "ask"), ("other", "read")])
def test_duplicate_completion_rejected_for_every_server(tmp_path, server, tool):
    call = event(server=server, tool=tool)
    with pytest.raises(ValueError, match="Duplicate completed"):
        observed(tmp_path, [call, call])


@pytest.mark.parametrize("changed", [
    {"server": "colony", "tool": "ask"},
    {"tool": "fixture_search"},
    {"type": "command_execution"},
])
def test_item_identity_cannot_hide_a_different_tool(tmp_path, changed):
    with pytest.raises(ValueError, match="Item identity changed"):
        observed(tmp_path, [event("started"), event(**changed)])


@pytest.mark.parametrize("identity", [None, "", [], 1])
def test_tool_call_requires_valid_identity(tmp_path, identity):
    with pytest.raises(ValueError, match="Invalid tool call identity"):
        observed(tmp_path, [event(id=identity)])


def test_tool_call_cannot_reuse_an_answer_identity(tmp_path):
    answer = {"type": "item.completed", "item": {
        "id": "call", "type": "agent_message", "text": "answer"}}
    with pytest.raises(ValueError, match="Item identity changed"):
        observed(tmp_path, [answer, event()])


@pytest.mark.parametrize("stage", ["started", "updated"])
def test_tool_lifecycle_cannot_resume_after_completion(tmp_path, stage):
    with pytest.raises(ValueError, match="after completion"):
        observed(tmp_path, [event(), event(stage)])


def test_each_turn_counts_its_own_tool_calls(tmp_path):
    events = [{"type": "turn.started"}, event(), {"type": "turn.completed"},
              {"type": "turn.started"}, event(), {"type": "turn.completed"}]
    assert observed(tmp_path, events)["research_calls"] == 2


@pytest.mark.parametrize("server,tool,counter", [
    ("repository", "fixture_read", "research_calls"),
    ("colony", "ask", "colony_calls"),
])
def test_streamed_failed_and_interrupted_calls_each_count_once(tmp_path, server, tool, counter):
    events = [event("started", server=server, tool=tool),
              event("updated", server=server, tool=tool),
              event(server=server, tool=tool, status="failed"),
              event("started", id="interrupted", server=server, tool=tool)]
    assert observed(tmp_path, events)[counter] == 2
