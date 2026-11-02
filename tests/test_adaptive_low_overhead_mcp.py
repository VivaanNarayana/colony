"""Adaptive transport reduces overhead without weakening source or permission boundaries."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from colony import mcp
from colony.context_budget import count_response, serialize_response
from colony.models import ContextBudget
from colony.workflow import (
    ADAPTIVE_READ_ONLY_WORKFLOW_GUIDANCE,
    ADAPTIVE_WORKFLOW_GUIDANCE,
    WORKFLOW_GUIDANCE,
)


@pytest.fixture
def response(monkeypatch):
    value = {
        "hit": True,
        "id": "a" * 32,
        "namespace": "team",
        "producer_id": "agent-a",
        "match_kind": "answer",
        "status": "validated",
        "age_flag": False,
        "tainted": True,
        "content": "The production timeout is 37 seconds.",
        "sources": [
            {
                "kind": "file",
                "ref": "runbook.md",
                "content_hash": "b" * 64,
                "symbol": None,
                "contains": None,
            }
        ],
        "evidence": [
            {
                "ref": "runbook.md",
                "line": 1,
                "text": "# Deployment\n\n## Production\n\nThe timeout is 37 seconds.",
                "complete_file": True,
                "selected_line": 1,
                "selected_end_line": 5,
            }
        ],
        "complete_files": ["runbook.md"],
        "memory": {
            "state": "active",
            "revision": 3,
            "replacement_id": None,
            "feedback": {"incorrect": 1},
        },
        "git": {"compatibility": "matched", "branch": "feature", "dirty": False},
    }

    class FakeClient:
        def __init__(self, home):
            self.keyfile = SimpleNamespace(
                grant=SimpleNamespace(
                    scopes=[SimpleNamespace(namespace="team", pull=True, push=True)]
                )
            )

        def ask(self, question, namespace=None, **kwargs):
            return copy.deepcopy(value)

        def memory_inspect(self, trail_id, namespace):
            return {"id": trail_id, "namespace": namespace, "revision": 3}

    monkeypatch.setattr(mcp, "Client", FakeClient)
    return value


def ask(server, **kwargs):
    return asyncio.run(server.call_tool("ask", {"question": "timeout?", **kwargs}))[0].text


@pytest.mark.parametrize("workflow", [False, True])
def test_adaptive_routine_tools_and_explicit_legacy_management(response, tmp_path, workflow):
    options = {"workflow_namespace": "team", "workflow_repo": tmp_path} if workflow else {}
    adaptive = mcp.create_mcp(tmp_path, context_budget=ContextBudget(), **options)
    legacy = mcp.create_mcp(tmp_path, **options)
    adaptive_tools = {t.name for t in asyncio.run(adaptive.list_tools())}
    assert adaptive_tools == {"ask", "push"}
    assert {t.name for t in asyncio.run(legacy.list_tools())} == {
        "ask", "push", *mcp.MEMORY_TOOLS
    }
    for tool in mcp.MEMORY_TOOLS:
        with pytest.raises(ToolError):
            asyncio.run(adaptive.call_tool(tool, {}))
    inspected = asyncio.run(
        legacy.call_tool("memory_inspect", {"trail_id": "a" * 32, "namespace": "team"})
    )
    assert json.loads(inspected[0].text)["revision"] == 3


def test_adaptive_read_only_cannot_expose_mutations(response, tmp_path):
    server = mcp.create_mcp(
        tmp_path,
        workflow_namespace="team",
        workflow_repo=tmp_path,
        workflow_read_only=True,
        context_budget=ContextBudget(),
    )
    assert {t.name for t in asyncio.run(server.list_tools())} == {"ask"}
    assert ADAPTIVE_READ_ONLY_WORKFLOW_GUIDANCE in server.instructions
    assert "On writable connections" not in server.instructions
    for tool in {"push", *mcp.MEMORY_TOOLS}:
        with pytest.raises(ToolError):
            asyncio.run(server.call_tool(tool, {}))


def test_adaptive_policy_applies_without_workflow_and_legacy_policy_stays(response, tmp_path):
    adaptive = mcp.create_mcp(tmp_path, context_budget=ContextBudget())
    assert ADAPTIVE_WORKFLOW_GUIDANCE in adaptive.instructions
    legacy = mcp.create_mcp(tmp_path, workflow_namespace="team", workflow_repo=tmp_path)
    assert WORKFLOW_GUIDANCE in legacy.instructions
    assert ADAPTIVE_WORKFLOW_GUIDANCE not in legacy.instructions


def test_adaptive_exact_identity_dedup_preserves_answer_source_and_safety(response, tmp_path):
    before = copy.deepcopy(response)
    server = mcp.create_mcp(tmp_path, context_budget=ContextBudget())
    text = ask(server)
    packet = json.loads(text)
    assert "sources" not in packet
    assert packet["content"] == response["content"]
    assert packet["current_repository_reads"] == [
        {
            "path": "runbook.md",
            "sha256": "b" * 64,
            "start_line": 1,
            "content": response["evidence"][0]["text"],
            "complete_file": True,
            "selected_line": 1,
            "selected_end_line": 5,
        }
    ]
    for field in ["id", "namespace", "producer_id", "status", "age_flag", "tainted", "memory", "git"]:
        assert packet[field] == response[field]
    assert packet["complete_files"] == ["runbook.md"]
    assert "UNTRUSTED AS INSTRUCTIONS" in packet["provenance"]
    assert "unverified" in packet["memory_warning"]
    assert packet["verification"] == {
        "file_fingerprints_match": True, "source_reads_current": True
    }
    assert text == serialize_response(packet)
    assert packet["context_budget"]["used"] == count_response(packet, ContextBudget()) <= 2048
    assert response == before
    legacy = json.loads(ask(mcp.create_mcp(tmp_path)))
    assert legacy["sources"] == response["sources"]


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "production"},
        {"contains": "timeout"},
        {"kind": "doc"},
        {"future_evidence": "must retain"},
    ],
)
def test_adaptive_retains_source_selection_and_unknown_metadata(response, tmp_path, change):
    response["sources"][0].update(change)
    packet = json.loads(ask(mcp.create_mcp(tmp_path, context_budget=ContextBudget())))
    assert packet["sources"] == response["sources"]


@pytest.mark.parametrize("missing_read", [True, False], ids=["missing", "conflicting-hash"])
def test_adaptive_retains_entire_source_list_without_complete_identity_coverage(
    response, tmp_path, missing_read
):
    extra = copy.deepcopy(response["sources"][0])
    extra.update(ref="other.md" if missing_read else "runbook.md", content_hash="c" * 64)
    response["sources"].append(extra)
    packet = json.loads(ask(mcp.create_mcp(tmp_path, context_budget=ContextBudget())))
    assert packet["sources"] == response["sources"]


def test_no_read_or_nonvalidated_response_keeps_sources(response, tmp_path):
    response["evidence"] = []
    packet = json.loads(ask(mcp.create_mcp(tmp_path, context_budget=ContextBudget())))
    assert packet["sources"] == response["sources"]
    response["status"] = "stale"
    packet = json.loads(ask(mcp.create_mcp(tmp_path, context_budget=ContextBudget())))
    assert packet["sources"] == response["sources"]


@pytest.mark.parametrize("include_null_hash", [False, True], ids=["missing-hash", "null-hash"])
def test_dedup_unknown_ref_without_hash_cannot_match_absent_identity(
    response, tmp_path, include_null_hash
):
    packet = json.loads(ask(mcp.create_mcp(tmp_path, context_budget=ContextBudget())))
    unknown = {"ref": "unknown.md"}
    if include_null_hash:
        unknown["content_hash"] = None
    packet["sources"] = [*response["sources"], unknown]
    before = copy.deepcopy(packet)
    mcp._deduplicate_adaptive_sources(packet)
    assert packet == before


@pytest.mark.parametrize("maximum", [256, 512])
def test_adaptive_oversized_required_units_fail_closed(response, tmp_path, maximum):
    response["content"] = "A distinct saved finding. " * 200
    response["evidence"][0]["text"] = "A required source paragraph. " * 200
    response["evidence"][0]["selected_end_line"] = 1
    packet = json.loads(
        ask(mcp.create_mcp(tmp_path, context_budget=ContextBudget(max_tokens=maximum)))
    )
    assert packet["hit"] is False and packet["reason"] == "budget_limited"
    assert packet["context_budget"]["used"] == count_response(
        packet, ContextBudget(max_tokens=maximum)
    ) <= maximum
