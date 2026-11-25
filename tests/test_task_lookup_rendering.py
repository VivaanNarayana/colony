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


@pytest.mark.parametrize("kind", ["answer", "evidence"])
@pytest.mark.parametrize("shape", ["reads-only", "null-evidence", "mixed"])
def test_task_preserves_reads_in_one_citation_list(identity, monkeypatch, kind, shape):
    sources = [source.model_dump() for source in identity[2]]
    first, second = sources
    read = {
        "path": first["ref"],
        "sha256": first["content_hash"],
        "start_line": 3,
        "content": "SAME_TEXT",
        "complete_file": False,
        "end_line": 99,  # A display hint. Derive the actual end from delivered text.
    }
    other = {**read, "path": second["ref"], "sha256": second["content_hash"]}
    raw = {
        "hit": True,
        "status": "validated",
        "match_kind": kind,
        "sources": sources,
        "content": "authored finding" if kind == "answer" else "SAME_TEXT",
        "current_repository_reads": [read, other, read],
        "tainted": True,
    }
    span = {"ref": first["ref"], "line": 3, "text": "SAME_TEXT"}
    earlier = {**span, "line": 1}
    if shape == "mixed":
        raw["evidence"] = [
            earlier,
            {**span, "content": span["text"], "origin_detail": "legacy"},
            earlier,
        ]
    elif shape == "null-evidence":
        raw["evidence"] = None
    result = task_lookup(identity, monkeypatch, raw)
    assert result["hit"] and "current_repository_reads" not in result
    merged = {**span, "end_line": 3, "complete_file": False}
    expected = [earlier, {**merged, "origin_detail": "legacy"}] if shape == "mixed" else [merged]
    assert result["evidence"] == [*expected, {**merged, "ref": second["ref"]}]
    assert result["sources"] == sources
    if kind == "answer":
        assert result["content"] == "authored finding"
    else:
        assert "content" not in result


@pytest.mark.parametrize("invalid", [{"sha256": "b" * 64}, {"text": "different"}])
def test_task_rejects_conflicting_read(identity, monkeypatch, invalid):
    source = identity[2][0].model_dump()
    span = {"ref": source["ref"], "line": 1, "text": "supported"}
    raw = {
        "hit": True,
        "status": "validated",
        "match_kind": "evidence",
        "sources": [source],
        "evidence": [span],
        "current_repository_reads": [
            {
                "path": source["ref"],
                "sha256": source["content_hash"],
                "start_line": 3,
                "content": "unbound",
                **invalid,
            }
        ],
    }
    assert task_lookup(identity, monkeypatch, raw) == {
        "hit": False,
        "reason": "lookup_unavailable",
        "continue_research": True,
    }


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


def test_blank_excerpt_keeps_its_coordinates_alongside_usable_evidence(identity, monkeypatch):
    source = identity[2][0].model_dump()
    blank = {"ref": source["ref"], "line": 7, "text": "", "complete_file": False}
    usable = {**blank, "line": 4, "text": "TIMEOUT = 30"}
    raw = {
        "hit": True,
        "status": "validated",
        "match_kind": "evidence",
        "sources": [source],
        "evidence": [blank, usable],
        "content": "generated summary",
    }
    result = task_lookup(identity, monkeypatch, raw)
    assert result["evidence"] == [blank, usable] and "content" not in result


@pytest.mark.parametrize("legacy_complete", [False, True])
def test_duplicate_preserves_complete_file_coverage(identity, monkeypatch, legacy_complete):
    source = identity[2][0].model_dump()
    span = {"ref": source["ref"], "line": 1, "text": "same bytes", "complete_file": legacy_complete}
    raw = {
        "hit": True,
        "status": "validated",
        "match_kind": "evidence",
        "sources": [source],
        "evidence": [span],
        "current_repository_reads": [
            {
                "path": source["ref"],
                "sha256": source["content_hash"],
                "start_line": 1,
                "content": span["text"],
                "complete_file": not legacy_complete,
            }
        ],
    }
    assert task_lookup(identity, monkeypatch, raw)["evidence"] == [{**span, "complete_file": True}]


def test_git_mismatch_retains_verified_local_excerpts(transport, monkeypatch):
    client, repo, _, _ = transport
    trail = publish(client, repo, git=True, content="Interpretation from the original branch")
    subprocess.run(
        ["git", "-C", str(repo), "switch", "-c", "other"], check=True, capture_output=True
    )
    original = client.ask(trail.question, "team", repo=repo, repository_id="project")
    assert original["match_kind"] == "evidence" and original["content"]
    request = client.request

    def with_server_read(route, payload):
        response = request(route, payload)
        if route == "/ask":
            source = response["sources"][0]
            response["current_repository_reads"] = [
                {
                    "path": source["ref"],
                    "sha256": source["content_hash"],
                    "start_line": 1,
                    "content": "SERVER TEXT OUTSIDE THE CALLER READ",
                }
            ]
        return response

    monkeypatch.setattr(client, "request", with_server_read)
    server = TaskMCP(Workflow(client, "team", repo, repository_id="project"), trail.question)
    result = json.loads(asyncio.run(server.call_tool("research", {}))[0].text)
    assert result["git"] == original["git"] and result["git"]["compatibility"] != "matched"
    assert result["provenance"] == original["provenance"]
    assert result["sources"] == original["sources"]
    assert result["evidence"] == original["evidence"] and result["evidence"]
    assert "content" not in result and "current_repository_reads" not in result
    assert "SERVER TEXT OUTSIDE THE CALLER READ" not in json.dumps(result)


def test_stdio_evidence_answer_and_stale_lookup(running_team):
    _, homes, repo, _ = running_team
    client = Client(homes["a"])
    guide = repo / "guide.md"
    guide.write_text(
        "# Worker\n\nThe local processing worker is a background service that executes queued jobs.\n"
    )
    source = snapshot(repo, "guide.md")
    question = "What is the local processing worker?"
    earlier = Trail(
        id="a" * 32,
        namespace="team",
        question="Audit retention period",
        content="Keep audit records for fourteen days.",
        sources=[source],
    )
    assert client.push(earlier)["stored"]

    async def research(session):
        reply = await session.call_tool("research", {})
        assert not reply.isError and reply.structuredContent is None
        assert len(reply.content) == 1 and reply.content[0].type == "text"
        assert "publish" not in {tool.name for tool in (await session.list_tools()).tools}
        return json.loads(reply.content[0].text)

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
                question,
            ],
        )
        async with (
            stdio_client(params) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            raw = client.ask(question, "team")
            assert raw["match_kind"] == "evidence" and raw["evidence"][0]["text"] in raw["content"]
            evidence = await research(session)
            assert evidence["status"] == "validated" and evidence["match_kind"] == "evidence"
            assert "content" not in evidence and evidence["sources"] == raw["sources"]
            assert evidence["evidence"] == raw["evidence"]

            finding = Trail(
                id="b" * 32,
                namespace="team",
                question=question,
                content="The worker processes queued jobs in the background.",
                sources=[source],
            )
            assert client.push(finding)["stored"]
            answer = await research(session)
            assert answer["match_kind"] == "answer" and answer["content"] == finding.content
            assert answer["status"] == "validated" and answer["evidence"]

            guide.write_text("# Worker\n\nRemoved.\n")
            stale = await research(session)
            assert stale["status"] == stale["content_withheld"] == "stale"
            assert stale["id"] == finding.id and "content" not in stale
            assert client.ask(question, "team")["content"] == finding.content

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))
