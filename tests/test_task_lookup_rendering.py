import asyncio
import copy
import json
import os
import subprocess
import sys
from unittest.mock import Mock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_compact_delivery import publish
from test_compact_delivery import transport as transport
from test_integration import running_team as running_team
from test_workflow import identity as identity

from colony.client import Client
from colony.models import Trail
from colony.task_mcp import TaskMCP
from colony.validation import snapshot
from colony.workflow import Workflow
from scripts.token_evaluation.task_evidence import lookup_value


def task_lookup(identity, monkeypatch, raw):
    client, repo, _ = identity
    original = copy.deepcopy(raw)
    ask = Mock(return_value=raw)
    monkeypatch.setattr(client, "ask", ask)
    server = TaskMCP(Workflow(client, "team", repo), "Explain the configuration")
    blocks = asyncio.run(server.call_tool("research", {}))
    ask.assert_called_once_with("Explain the configuration", "team")
    assert len(blocks) == 1 and blocks[0].type == "text"
    assert "publish" not in {tool.name for tool in asyncio.run(server.list_tools())}
    assert raw == original
    return json.loads(blocks[0].text)


def test_evidence_only_retains_each_citation_once(identity, monkeypatch):
    _, _, sources = identity
    evidence = [
        {"ref": "settings.py", "line": 2, "text": "café [REDACTED]"},
        {"ref": "settings.py", "line": 7, "text": "SECOND_SPAN", "complete_file": False},
        {"ref": "README.md", "line": 1, "text": "SECOND_SPAN", "complete_file": True},
    ]
    raw = {
        "hit": True,
        "status": "validated",
        "match_kind": "evidence",
        "sources": [s.model_dump() for s in sources],
        "evidence": evidence,
        "content": "\n\n".join(e["text"] for e in evidence),
        "content_truncated": False,
        "complete_files": ["README.md"],
        "provenance": "Current configured repository text",
        "memory": {"state": "active"},
        "tainted": True,
    }
    result = task_lookup(identity, monkeypatch, raw)
    assert result == {k: v for k, v in raw.items() if k not in {"content", "content_truncated"}}
    assert (
        lookup_value(result)["current_repository_reads"]
        == lookup_value(raw)["current_repository_reads"]
    )


@pytest.mark.parametrize("status", ["stale", "unverified"])
@pytest.mark.parametrize("has_content", [True, False])
def test_nonvalidated_hit_withholds_only_stored_prose(identity, monkeypatch, status, has_content):
    raw = {
        "hit": True,
        "status": status,
        "match_kind": "answer",
        "sources": [],
        "evidence": [],
        "recommendation": "research",
        "checks": [],
        "tainted": True,
        "content_truncated": True,
    }
    if has_content:
        raw["content"] = "X" * 100_000
    expected = {k: v for k, v in raw.items() if k not in {"content", "content_truncated"}}
    if has_content:
        expected["content_withheld"] = status
    assert task_lookup(identity, monkeypatch, raw) == expected


def test_validated_answer_keeps_distinct_long_prose(identity, monkeypatch):
    raw = {
        "hit": True,
        "status": "validated",
        "match_kind": "answer",
        "content": "A" * 100_000,
        "evidence": [{"ref": "settings.py", "line": 1, "text": "TIMEOUT = 30"}],
        "sources": [identity[2][0].model_dump()],
    }
    assert task_lookup(identity, monkeypatch, raw) == raw


@pytest.mark.parametrize(
    ("evidence", "sources"),
    [
        (None, []),
        ([], []),
        (
            [{"ref": "other.py", "line": 1, "text": "marker"}],
            [{"ref": "x", "content_hash": "a" * 64}],
        ),
        ([{"ref": "x", "line": 1, "text": "marker"}], [{"ref": "x", "content_hash": "invalid"}]),
        (
            [{"ref": "x", "line": 1, "text": "marker"}],
            [{"ref": "x", "content_hash": h * 64} for h in "ab"],
        ),
        ([{"ref": "x", "line": True, "text": "marker"}], [{"ref": "x", "content_hash": "a" * 64}]),
        ([{"ref": "x", "line": 1}], [{"ref": "x", "content_hash": "a" * 64}]),
        ([{"ref": "x", "line": 1, "text": ""}], [{"ref": "x", "content_hash": "a" * 64}]),
        (
            [{"ref": "x", "line": 3, "text": "marker", "complete_file": True}],
            [{"ref": "x", "content_hash": "a" * 64}],
        ),
    ],
)
def test_unusable_evidence_returns_recoverable_miss(identity, monkeypatch, evidence, sources):
    raw = {
        "hit": True,
        "status": "validated",
        "match_kind": "evidence",
        "sources": sources,
        "content": "prose alone is not a citation",
    }
    if evidence is not None:
        raw["evidence"] = evidence
    assert task_lookup(identity, monkeypatch, raw) == {
        "hit": False,
        "reason": "lookup_unavailable",
        "continue_research": True,
    }


def test_complete_empty_file_keeps_its_citation(identity, monkeypatch):
    raw = {
        "hit": True,
        "status": "validated",
        "match_kind": "evidence",
        "sources": [{"ref": "empty.txt", "content_hash": "a" * 64}],
        "evidence": [{"ref": "empty.txt", "line": 1, "text": "", "complete_file": True}],
        "content": "empty.txt:1 (complete redacted file)\n",
    }
    result = task_lookup(identity, monkeypatch, raw)
    assert result["evidence"] == raw["evidence"] and result["sources"] == raw["sources"]
    assert "content" not in result


def test_git_mismatch_retains_verified_local_excerpts(transport):
    client, repo, _, _ = transport
    trail = publish(client, repo, git=True, content="Interpretation from the original branch")
    subprocess.run(
        ["git", "-C", str(repo), "switch", "-c", "other"], check=True, capture_output=True
    )
    original = client.ask(trail.question, "team", repo=repo, repository_id="project")
    assert original["match_kind"] == "evidence" and original["content"]
    server = TaskMCP(Workflow(client, "team", repo, repository_id="project"), trail.question)
    result = json.loads(asyncio.run(server.call_tool("research", {}))[0].text)
    assert result["git"] == original["git"] and result["git"]["compatibility"] != "matched"
    assert result["provenance"] == original["provenance"]
    assert result["sources"] == original["sources"]
    assert result["evidence"] == original["evidence"] and result["evidence"]
    assert "content" not in result


def test_stdio_validated_then_stale_lookup(running_team):
    _, homes, repo, _ = running_team
    client = Client(homes["a"])
    trail = Trail(
        id="a" * 32,
        namespace="team",
        question="How is the timeout configured?",
        content="The configured timeout is thirty.",
        sources=[snapshot(repo, "settings.py")],
    )
    assert client.push(trail)["stored"]

    async def exercise():
        params = StdioServerParameters(
            command=sys.executable,
            env=dict(os.environ),
            args=[
                "-m",
                "colony.task_mcp",
                "--home",
                str(homes["b"]),
                "--repo",
                str(repo),
                "--ns",
                "team",
                "--question",
                trail.question,
            ],
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            for status in ("validated", "stale"):
                if status == "stale":
                    (repo / "settings.py").write_text("TIMEOUT = 60\n")
                raw = client.ask(trail.question, "team")
                assert raw["status"] == status and raw["content"] == trail.content
                reply = await session.call_tool("research", {})
                assert not reply.isError and reply.structuredContent is None
                assert len(reply.content) == 1 and reply.content[0].type == "text"
                value = json.loads(reply.content[0].text)
                assert value["status"] == status and value["id"] == raw["id"]
                if status == "validated":
                    assert (
                        value["content"] == trail.content and value["evidence"] == raw["evidence"]
                    )
                else:
                    assert value["content_withheld"] == "stale" and "content" not in value
                    assert trail.content not in reply.content[0].text
                assert "publish" not in {tool.name for tool in (await session.list_tools()).tools}

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))
