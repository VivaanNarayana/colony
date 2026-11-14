"""Adaptive transport reduces overhead without weakening source or permission boundaries."""

import asyncio
import copy
import hashlib
import json
import os
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from test_integration import running_team as running_team
from test_workflow_policy_envelopes import (
    assert_publication_catalog_growth_bounded,
)

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
    adaptive = mcp.create_mcp(tmp_path, context_budget=ContextBudget(max_tokens=2048), **options)
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
        context_budget=ContextBudget(max_tokens=2048),
    )
    assert {t.name for t in asyncio.run(server.list_tools())} == {"ask"}
    assert ADAPTIVE_READ_ONLY_WORKFLOW_GUIDANCE in server.instructions
    assert "On writable connections" not in server.instructions
    assert "After substantive new research, publish" not in server.instructions
    for tool in {"push", *mcp.MEMORY_TOOLS}:
        with pytest.raises(ToolError):
            asyncio.run(server.call_tool(tool, {}))


def test_adaptive_policy_applies_without_workflow_and_legacy_policy_stays(response, tmp_path):
    adaptive = mcp.create_mcp(tmp_path, context_budget=ContextBudget(max_tokens=2048))
    assert ADAPTIVE_WORKFLOW_GUIDANCE in adaptive.instructions
    legacy = mcp.create_mcp(tmp_path, workflow_namespace="team", workflow_repo=tmp_path)
    assert WORKFLOW_GUIDANCE in legacy.instructions
    assert ADAPTIVE_WORKFLOW_GUIDANCE not in legacy.instructions


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("git_aware", [False, True])
def test_adaptive_discovery_keeps_complete_policy_once(response, tmp_path, read_only, git_aware):
    server = mcp.create_mcp(
        tmp_path,
        workflow_namespace="team",
        workflow_repo=tmp_path,
        workflow_read_only=read_only,
        workflow_repository_id="project" if git_aware else None,
        context_budget=ContextBudget(max_tokens=2048),
    )
    policy = ADAPTIVE_READ_ONLY_WORKFLOW_GUIDANCE if read_only else ADAPTIVE_WORKFLOW_GUIDANCE
    # The shared policy remains self-contained even with smaller tool descriptions.
    assert server.instructions == policy + (
        " Git checks use this caller's current checkout." if git_aware else ""
    )


@pytest.mark.parametrize("workflow", [False, True])
@pytest.mark.parametrize("measurement", ["utf8_bytes", "o200k_base"])
def test_adaptive_discovery_preserves_publication_schema_and_annotations(
    response, tmp_path, workflow, measurement, monkeypatch,
):
    if measurement == "o200k_base":
        pytest.importorskip("tiktoken")
        if not os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER"):
            pytest.skip("Set COLONY_TEST_CONTEXT_TOKENIZER for pinned o200k cost checks")
    options = {"workflow_namespace": "team", "workflow_repo": tmp_path} if workflow else {}
    legacy = {
        tool.name: tool for tool in asyncio.run(mcp.create_mcp(tmp_path, **options).list_tools())
    }
    adaptive = {
        tool.name: tool
        for tool in asyncio.run(
            mcp.create_mcp(tmp_path, context_budget=ContextBudget(max_tokens=2048), **options).list_tools()
        )
    }
    assert adaptive["push"].inputSchema == legacy["push"].inputSchema
    assert adaptive["push"].outputSchema == legacy["push"].outputSchema
    assert all(adaptive[name].annotations == legacy[name].annotations for name in adaptive)
    assert adaptive["ask"].outputSchema == legacy["ask"].outputSchema
    assert set(adaptive["ask"].inputSchema["properties"]) == {
        *legacy["ask"].inputSchema["properties"], "max_context_tokens"
    }
    ending = ("Namespace bound; taint true; secrets scrubbed." if workflow
              else "Client scrubs secrets; mark external content tainted.")
    description = adaptive["push"].description
    for cue in (
        "Push one new reusable finding from the requested task",
        "applicability and observed file ref/content_hash.",
        "Skip isolated values unless asked to remember; no task expansion to justify publication or unchanged reuse.",
        "Never invent hashes or research solely to publish.",
        "Answer detail/research remain unrestricted; publication failure must not block the answer.",
        ending,
    ):
        assert cue in description
    catalog = [t.model_dump(by_alias=True, exclude_none=True) for t in adaptive.values()]
    evaluated_catalog = copy.deepcopy(catalog)
    next(t for t in evaluated_catalog if t["name"] == "push")["description"] = (
        "Publish findings with actual file ref/content_hash under the server's "
        "publication policy. " + ending
    )
    assert_publication_catalog_growth_bounded(
        json.dumps(catalog, separators=(',', ':')).encode(),
        json.dumps(evaluated_catalog, separators=(',', ':')).encode(),
        measurement, monkeypatch,
    )



def test_adaptive_exact_identity_dedup_preserves_answer_source_and_safety(response, tmp_path):
    before = copy.deepcopy(response)
    server = mcp.create_mcp(tmp_path, context_budget=ContextBudget(max_tokens=2048))
    text = ask(server)
    packet = json.loads(text)
    assert packet["sources"] == [{"ref": "runbook.md", "content_hash": "b" * 64}]
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
    assert packet["context_budget"]["used"] == count_response(packet, ContextBudget(max_tokens=2048)) <= 2048
    assert response == before
    legacy = json.loads(ask(mcp.create_mcp(tmp_path)))
    assert legacy["sources"] == response["sources"]


def test_real_adaptive_mcp_retains_publication_source_binding(running_team):
    """A consumer must still bind a real retrieved finding to its published file hashes."""
    cli, homes, repo, directory = running_team
    question = "What is the build timeout in settings.py?"
    trail_id = "a" * 32
    trail = directory / "source-binding.json"
    trail.write_text(json.dumps({
        "id": trail_id, "question": question,
        "content": "The build timeout is TIMEOUT = 30 in settings.py.",
    }), encoding="utf-8")
    published = json.loads(cli(
        "--home", homes["a"], "push", trail, "--ns", "team", "--repo", repo,
        "--source", "settings.py",
    ).stdout)
    assert published["stored"] and published["id"] == trail_id
    published_identity = {
        "settings.py": hashlib.sha256((repo / "settings.py").read_bytes()).hexdigest()
    }
    budget = ContextBudget(max_tokens=4096)
    server = mcp.create_mcp(
        homes["b"], workflow_namespace="team", workflow_repo=repo,
        workflow_read_only=True, context_budget=budget,
    )
    text = asyncio.run(server.call_tool("ask", {"question": question}))[0].text
    packet = json.loads(text)
    assert packet["hit"] and packet["status"] == "validated" and packet["id"] == trail_id
    returned_identity = {s["ref"]: s["content_hash"] for s in packet["sources"]}
    assert returned_identity == published_identity
    assert all(set(s) == {"ref", "content_hash"} for s in packet["sources"])
    assert {r["path"]: r["sha256"] for r in packet["current_repository_reads"]} == returned_identity
    assert all("TIMEOUT = 30" in r["content"] for r in packet["current_repository_reads"])
    assert packet["context_budget"]["used"] == count_response(packet, budget) <= 4096


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
    packet = json.loads(ask(mcp.create_mcp(tmp_path, context_budget=ContextBudget(max_tokens=2048))))
    assert packet["sources"] == response["sources"]


@pytest.mark.parametrize("missing_read", [True, False], ids=["missing", "conflicting-hash"])
def test_adaptive_retains_entire_source_list_without_complete_identity_coverage(
    response, tmp_path, missing_read
):
    extra = copy.deepcopy(response["sources"][0])
    extra.update(ref="other.md" if missing_read else "runbook.md", content_hash="c" * 64)
    response["sources"].append(extra)
    packet = json.loads(ask(mcp.create_mcp(tmp_path, context_budget=ContextBudget(max_tokens=2048))))
    assert packet["sources"] == response["sources"]


def test_no_read_or_nonvalidated_response_keeps_sources(response, tmp_path):
    response["evidence"] = []
    packet = json.loads(ask(mcp.create_mcp(tmp_path, context_budget=ContextBudget(max_tokens=2048))))
    assert packet["sources"] == response["sources"]
    response["status"] = "stale"
    packet = json.loads(ask(mcp.create_mcp(tmp_path, context_budget=ContextBudget(max_tokens=2048))))
    assert packet["sources"] == response["sources"]


@pytest.mark.parametrize("include_null_hash", [False, True], ids=["missing-hash", "null-hash"])
def test_dedup_unknown_ref_without_hash_cannot_match_absent_identity(
    response, tmp_path, include_null_hash
):
    packet = json.loads(ask(mcp.create_mcp(tmp_path, context_budget=ContextBudget(max_tokens=2048))))
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
