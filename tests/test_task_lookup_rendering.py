import asyncio
import copy
import json
import os
import subprocess
import sys
from itertools import permutations
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


def serve_citations(store, monkeypatch, **fields):
    """Vary only the server's citation fields. Keep Client.ask and its checks in the call path."""
    ask = store.ask

    def with_citations(*args, **kwargs):
        result = ask(*args, **kwargs)
        assert result["hit"] and result["status"] == "validated"
        result.pop("evidence")
        return {**result, "match_kind": "evidence", **fields}

    monkeypatch.setattr(store, "ask", with_citations)


def _wire_read(span, source):
    return {
        "path": source.ref, "sha256": source.content_hash, "start_line": span["line"],
        "content": span["text"], "complete_file": span["complete_file"],
    }


@pytest.mark.parametrize("shape", ["mixed", "reads-only", "wrong-hash"])
def test_git_client_rebuilds_alternate_citations(transport, monkeypatch, shape):
    client, repo, store, responses = transport
    trail = publish(client, repo, count=2, git=True)
    local = [
        {
            "ref": s.ref,
            "line": 2,
            "text": (repo / s.ref).read_text(encoding="utf-8").splitlines()[1],
            "complete_file": False,
        }
        for s in trail.sources
    ]
    reads = [
        {
            "path": s.ref,
            "sha256": s.content_hash,
            "start_line": 2,
            "content": "?" * len(span["text"]),
        }
        for s, span in zip(trail.sources, local, strict=True)
    ]
    # Duplicate hints must not crowd the second file out of the verifier's eight-hint window.
    if shape == "reads-only":
        repeats = [{**reads[0], "content": c * len(local[0]["text"])} for c in "abcdefgh"]
        fields = {"current_repository_reads": [*repeats, reads[1]]}
    else:
        fields = {
            "evidence": [{**local[0], "text": reads[0]["content"]}] * 8,
            "current_repository_reads": reads,
        }
    if shape == "wrong-hash":
        reads[1]["sha256"] = "0" * 64
    serve_citations(store, monkeypatch, **fields)
    task = TaskMCP(Workflow(client, "team", repo, repository_id="project"), trail.question)
    result = json.loads(asyncio.run(task.call_tool("research", {}))[0].text)
    assert len(responses) == 1
    if shape == "wrong-hash":
        assert not result["hit"] and result["reason"] == "git_local_evidence_not_verified"
        assert "content" not in result and "evidence" not in result
    else:
        assert result["hit"] and result["git"]["compatibility"] == "matched"
        assert result["evidence"] == local
        assert "current_repository_reads" not in result and "content" not in result
        assert reads[0]["content"] not in json.dumps(result)


@pytest.mark.parametrize(
    "alternate",
    [{}, {"current_repository_reads": None}, {"current_repository_reads": []}],
    ids=["omitted", "null", "empty"],
)
def test_git_deduplicates_legacy_hints_before_the_limit(transport, monkeypatch, alternate):
    client, repo, store, responses = transport
    trail = publish(client, repo, count=2, git=True)
    spans = [
        {
            "ref": s.ref,
            "line": 2,
            "text": (repo / s.ref).read_text(encoding="utf-8").splitlines()[1],
            "complete_file": False,
        }
        for s in trail.sources
    ]
    serve_citations(store, monkeypatch, evidence=[spans[0]] * 8 + [spans[1]], **alternate)
    task = TaskMCP(Workflow(client, "team", repo, repository_id="project"), trail.question)
    result = json.loads(asyncio.run(task.call_tool("research", {}))[0].text)
    assert len(responses) == 1 and result["hit"]
    assert result["evidence"] == spans
    assert "evidence_scope" not in result


@pytest.mark.parametrize(
    ("count", "whole_files", "advertised", "delivered", "scope"),
    [
        (3, False, "cited_files", 2, "partial"),
        (2, False, "cited_files", 2, "partial"),
        (3, True, "partial", 3, "cited_files"),
        (5, True, "cited_files", 4, "partial"),
    ],
    ids=["omitted-file", "all-files-excerpted", "all-files-complete", "whole-file-limit"],
)
def test_git_scope_reflects_locally_delivered_files(
    transport, monkeypatch, count, whole_files, advertised, delivered, scope
):
    client, repo, store, responses = transport
    trail = publish(client, repo, count=count, git=True)
    reads = [
        {
            "path": s.ref,
            "sha256": s.content_hash,
            "start_line": 1,
            "complete_file": True,
            "content": (repo / s.ref).read_text(encoding="utf-8").rstrip("\n"),
        }
        for s in trail.sources
    ]
    serve_citations(store, monkeypatch, current_repository_reads=reads, evidence_scope=advertised)
    if whole_files:
        result = client.ask(
            trail.question, "team", repo=repo, repository_id="project", include_evidence_files=True
        )
    else:
        task = TaskMCP(Workflow(client, "team", repo, repository_id="project"), trail.question)
        result = json.loads(asyncio.run(task.call_tool("research", {}))[0].text)
    assert len(responses) == 1 and result["hit"]
    refs = [s.ref for s in trail.sources]
    assert [s["ref"] for s in result["sources"]] == refs
    assert [s["ref"] for s in result["evidence"]] == refs[:delivered]
    assert result["complete_files"] == (refs[:delivered] if whole_files else [])
    assert result["evidence_scope"] == scope


@pytest.mark.parametrize(
    "legacy_complete", [True, False], ids=["complete-in-evidence", "complete-in-reads"]
)
@pytest.mark.parametrize(
    ("peer_size", "tail_size", "same_bytes", "fits"),
    [
        (10, 20, False, True),
        (10, 20, True, True),
        (10, 16_000, False, False),
        (15_000, 10_000, False, False),
    ],
    ids=["first-line", "same-bytes", "per-file-limit", "remaining-budget"],
)
def test_git_prefers_verified_whole_file(
    transport, monkeypatch, legacy_complete, peer_size, tail_size, same_bytes, fits
):
    client, repo, store, responses = transport
    first_line = "VALUE = 1"
    peer_text = "#" * peer_size
    full_text = first_line + "\n" + "#" * tail_size
    excerpt = full_text if same_bytes else first_line
    trail = publish(client, repo, git=True, file_texts=[peer_text, full_text])
    peer, target = trail.sources
    # Only the hint lengths come from the server. Every delivered byte must be local.
    peer_span = {"ref": peer.ref, "line": 1, "text": peer_text, "complete_file": True}
    legacy = {
        "ref": target.ref,
        "line": 1,
        "complete_file": legacy_complete,
        "text": "?" * len(full_text if legacy_complete else excerpt),
    }
    alternate = {
        "path": target.ref,
        "sha256": target.content_hash,
        "start_line": 1,
        "complete_file": not legacy_complete,
        "content": "?" * len(excerpt if legacy_complete else full_text),
    }
    serve_citations(
        store,
        monkeypatch,
        evidence=[peer_span, legacy],
        current_repository_reads=[alternate],
        evidence_scope="cited_files",
    )
    result = client.ask(
        trail.question, "team", repo=repo, repository_id="project", include_evidence_files=True
    )

    assert len(responses) == 1 and result["hit"] and result["git"]["compatibility"] == "matched"
    assert result["evidence"] == [
        peer_span,
        {
            "ref": target.ref,
            "line": 1,
            "text": full_text if fits else excerpt,
            "complete_file": fits,
        },
    ]
    assert result["complete_files"] == ([peer.ref, target.ref] if fits else [peer.ref])
    assert result["evidence_scope"] == ("cited_files" if fits else "partial")
    assert "current_repository_reads" not in result


@pytest.mark.parametrize("ordering", ["complete-in-evidence", "complete-in-reads", "both-legacy"])
@pytest.mark.parametrize("excerpt_line", [1, 2])
def test_git_over_budget_complete_hint_preserves_explicit_excerpt(
    transport, monkeypatch, ordering, excerpt_line
):
    client, repo, store, responses = transport
    peer_text = "#" * 15_000
    lines = ["VALUE = 42", "RETRY_LIMIT = 5", "#" * 10_000]
    trail = publish(client, repo, git=True, file_texts=[peer_text, "\n".join(lines)])
    peer, target = trail.sources
    peer_span = {"ref": peer.ref, "line": 1, "text": peer_text, "complete_file": True}
    full = {"ref": target.ref, "line": 1, "text": "V", "complete_file": True}
    excerpt = {
        "ref": target.ref,
        "line": excerpt_line,
        "text": lines[excerpt_line - 1],
        "complete_file": False,
    }
    excerpt_hint = {**excerpt, "text": "?" * len(excerpt["text"])}
    if ordering == "both-legacy":
        fields = {"evidence": [peer_span, full, excerpt_hint]}
    else:
        legacy, read = (
            (full, excerpt_hint) if ordering == "complete-in-evidence" else (excerpt_hint, full)
        )
        fields = {
            "evidence": [peer_span, legacy],
            "current_repository_reads": [
                {
                    "path": target.ref,
                    "sha256": target.content_hash,
                    "start_line": read["line"],
                    "content": read["text"],
                    "complete_file": read["complete_file"],
                }
            ],
        }
    serve_citations(store, monkeypatch, evidence_scope="cited_files", **fields)
    result = client.ask(
        trail.question, "team", repo=repo, repository_id="project", include_evidence_files=True
    )

    assert len(responses) == 1 and result["hit"] and result["git"]["compatibility"] == "matched"
    assert result["evidence"] == [peer_span, excerpt]
    assert result["complete_files"] == [peer.ref] and result["evidence_scope"] == "partial"
    assert "current_repository_reads" not in result


@pytest.mark.parametrize(
    ("legacy_order", "read_order", "fill_window"),
    [
        *[
            pytest.param(order, (), False, id="-".join(order))
            for order in permutations(("whole", "first", "second"))
        ],
        pytest.param(("whole",), ("second", "first"), False, id="whole-in-evidence"),
        pytest.param(("second", "first"), ("whole",), False, id="whole-in-reads"),
        pytest.param(("whole", "second"), ("first",), True, id="explicit-after-window-fills"),
    ],
)
def test_git_preserves_both_excerpts_when_whole_hint_matches_one(
    transport, monkeypatch, legacy_order, read_order, fill_window
):
    client, repo, store, responses = transport
    peer_text = "#" * 15_000
    lines = ["VALUE = 42", "RETRY_LIMIT = 5", "#" * 10_000]
    trail = publish(client, repo, git=True, file_texts=[peer_text, "\n".join(lines)])
    peer, target = trail.sources
    peer_span = {"ref": peer.ref, "line": 1, "text": peer_text, "complete_file": True}
    first = {"ref": target.ref, "line": 1, "text": lines[0], "complete_file": False}
    second = {**first, "line": 2, "text": lines[1]}
    spans = {"first": first, "second": second, "whole": {**first, "complete_file": True}}
    fields = {"evidence": [peer_span, *(spans[name] for name in legacy_order)]}
    if fill_window:
        fields["evidence"].extend({**first, "line": line} for line in range(10, 18))
    if read_order:
        fields["current_repository_reads"] = [
            _wire_read(spans[name], target) for name in read_order
        ]
    serve_citations(store, monkeypatch, evidence_scope="cited_files", **fields)
    result = client.ask(
        trail.question, "team", repo=repo, repository_id="project", include_evidence_files=True
    )

    assert len(responses) == 1 and result["hit"] and result["git"]["compatibility"] == "matched"
    expected = [spans[name] for name in (*legacy_order, *read_order) if name != "whole"]
    assert result["evidence"] == [peer_span, *expected]
    assert result["complete_files"] == [peer.ref] and result["evidence_scope"] == "partial"


@pytest.mark.parametrize("marker_in_reads", [False, True], ids=["marker-first", "excerpt-first"])
def test_focused_git_research_prefers_explicit_excerpt(transport, monkeypatch, marker_in_reads):
    client, repo, store, responses = transport
    fact, peer_fact = "VALUE = 42", "WORKERS = 12"
    trail = publish(client, repo, git=True, file_texts=[fact + "\nFOCUSED_LOOKUP_TAIL", peer_fact])
    target, peer = trail.sources
    explicit = {"ref": target.ref, "line": 1, "text": "?" * len(fact), "complete_file": False}
    marker = {**explicit, "text": "?", "complete_file": True}
    peer_span = {**explicit, "ref": peer.ref, "text": peer_fact}
    legacy, alternate = (explicit, marker) if marker_in_reads else (marker, explicit)
    serve_citations(
        store, monkeypatch, evidence=[legacy, peer_span],
        current_repository_reads=[_wire_read(alternate, target)], evidence_scope="cited_files",
    )
    task = TaskMCP(Workflow(client, "team", repo, repository_id="project"), trail.question)
    blocks = asyncio.run(task.call_tool("research", {}))
    assert len(blocks) == len(responses) == 1
    result = json.loads(blocks[0].text)
    assert result["evidence"] == [{**explicit, "text": fact}, peer_span]
    assert result["git"]["compatibility"] == "matched" and result["complete_files"] == []
    assert result["evidence_scope"] == "partial"
    assert "current_repository_reads" not in result and "content" not in result
    assert blocks[0].text.count(fact) == blocks[0].text.count(peer_fact) == 1
    assert "FOCUSED_LOOKUP_TAIL" not in blocks[0].text


@pytest.mark.parametrize(
    "include_peer", [True, False], ids=["prefer-second-citation", "retain-disjoint-prefix"]
)
def test_focused_git_research_keeps_disjoint_prefix_when_space_remains(
    transport, monkeypatch, include_peer
):
    client, repo, store, responses = transport
    first_line, fact, peer_fact = "VALUE = 42", "RETRY_LIMIT = 5", "WORKERS = 12"
    files = [first_line + "\n" + fact]
    if include_peer:
        files.append(peer_fact)
    trail = publish(client, repo, git=True, file_texts=files)
    target = trail.sources[0]
    marker = {"ref": target.ref, "line": 1, "text": first_line, "complete_file": True}
    explicit = {"ref": target.ref, "line": 2, "text": fact, "complete_file": False}
    later = []
    expected_second = {**marker, "complete_file": False}
    if include_peer:
        peer = trail.sources[1]
        expected_second = {**explicit, "ref": peer.ref, "line": 1, "text": peer_fact}
        later.append(_wire_read(expected_second, peer))
    serve_citations(store, monkeypatch, evidence=[marker, explicit], current_repository_reads=later)
    task = TaskMCP(Workflow(client, "team", repo, repository_id="project"), trail.question)
    result = json.loads(asyncio.run(task.call_tool("research", {}))[0].text)
    assert len(responses) == 1 and result["git"]["compatibility"] == "matched"
    assert result["evidence"] == [explicit, expected_second]
    assert "content" not in result and "current_repository_reads" not in result


@pytest.mark.parametrize(
    ("layout", "target_fits", "explicit"),
    [
        ("legacy", True, False), ("mixed", True, False), ("reads", True, False),
        ("mixed", False, True), ("reads", False, False),
    ],
)
def test_git_groups_whole_markers_with_different_lengths(
    transport, monkeypatch, layout, target_fits, explicit
):
    client, repo, store, responses = transport
    first_line, fact, peer_text = "VALUE = 42", "RETRY_LIMIT = 5", "WORKERS = 12"
    target_text = first_line + "\n" + fact + ("" if target_fits else "\n" + "#" * 16_000)
    trail = publish(client, repo, git=True, file_texts=[target_text, peer_text])
    target, peer = trail.sources
    markers = [
        {"ref": target.ref, "line": 1, "text": "?" * n, "complete_file": True}
        for n in (2500, 8, 7, 6, 5, 4, 3, 2, 1)
    ]
    excerpt = {"ref": target.ref, "line": 2, "text": fact, "complete_file": False}
    peer_span = {"ref": peer.ref, "line": 1, "text": peer_text, "complete_file": True}
    spans = [*markers, *([excerpt] if explicit else []), peer_span]
    split = {"legacy": len(spans), "mixed": 4, "reads": 0}[layout]
    sources = {s.ref: s for s in trail.sources}
    serve_citations(
        store, monkeypatch, evidence=spans[:split],
        current_repository_reads=[_wire_read(s, sources[s["ref"]]) for s in spans[split:]],
        evidence_scope="cited_files",
    )
    result = client.ask(
        trail.question, "team", repo=repo, repository_id="project", include_evidence_files=True
    )
    if target_fits:
        expected = {**markers[0], "text": target_text}
    elif explicit:
        expected = excerpt
    else:
        expected = {**excerpt, "line": 1, "text": first_line[:8]}
    assert len(responses) == 1 and result["git"]["compatibility"] == "matched"
    assert result["evidence"] == [expected, peer_span]
    assert result["complete_files"] == ([target.ref, peer.ref] if target_fits else [peer.ref])
    assert result["evidence_scope"] == ("cited_files" if target_fits else "partial")
    assert "current_repository_reads" not in result


@pytest.mark.parametrize("mixed", [False, True], ids=["legacy", "mixed-fields"])
@pytest.mark.parametrize(
    ("order", "expected"),
    [
        pytest.param(("whole", "workers", "timeout", "prefix"), ("workers", "timeout"), id="defer-prefix"),
        pytest.param(("retry", "whole", "workers", "alpha"), ("retry", "workers"), id="keep-peer-rank"),
        pytest.param(("whole", "workers", "timeout", "alpha"), ("workers", "timeout"), id="late-explicit"),
        pytest.param(("whole", "workers", "prefix"), ("workers", "prefix"), id="spare-prefix"),
        pytest.param(("retry", "whole", "alpha"), ("retry", "alpha"), id="spare-explicit"),
    ],
)
def test_focused_git_research_respects_cross_file_priority(
    transport, monkeypatch, order, expected, mixed
):
    client, repo, store, responses = transport
    alpha, retry, workers, timeout = "ALPHA = 1", "RETRIES = 5", "WORKERS = 12", "TIMEOUT = 30"
    full_text = "\n".join([alpha, retry, "#" * 2500])
    trail = publish(client, repo, git=True, file_texts=[full_text, workers, timeout])
    a, b, c = trail.sources
    first = {"ref": a.ref, "line": 1, "text": alpha, "complete_file": False}
    spans = {
        "alpha": first, "retry": {**first, "line": 2, "text": retry},
        "workers": {**first, "ref": b.ref, "text": workers},
        "timeout": {**first, "ref": c.ref, "text": timeout},
        "whole": {**first, "text": full_text, "complete_file": True},
        "prefix": {**first, "text": "A", "complete_file": True},
    }
    hints = [spans[name] for name in order]
    split = 2 if mixed else len(hints)
    sources = {s.ref: s for s in trail.sources}
    serve_citations(
        store, monkeypatch, evidence=hints[:split], evidence_scope="cited_files",
        current_repository_reads=[_wire_read(h, sources[h["ref"]]) for h in hints[split:]],
    )
    task = TaskMCP(Workflow(client, "team", repo, repository_id="project"), trail.question)
    blocks = asyncio.run(task.call_tool("research", {}))
    assert len(responses) == len(blocks) == 1
    result = json.loads(blocks[0].text)
    assert result["git"]["compatibility"] == "matched" and result["hit"]
    assert result["evidence"] == [{**spans[name], "complete_file": False} for name in expected]
    assert result["evidence_scope"] == "partial" and result["complete_files"] == []
    assert "content" not in result and "current_repository_reads" not in result
    for name in expected:
        if name != "prefix":
            assert blocks[0].text.count(spans[name]["text"]) == 1


@pytest.mark.parametrize("valid_binding", [True, False], ids=["one-slot", "reject-late-hash"])
def test_focused_git_research_uses_one_reserved_marker_slot(transport, monkeypatch, valid_binding):
    client, repo, store, responses = transport
    lines = ["ALPHA = 1", "RETRIES = 5", "TIMEOUT = 30"]
    trail = publish(client, repo, git=True, file_texts=["\n".join(lines), "WORKERS = 12"])
    target, peer = trail.sources
    marker = {"ref": target.ref, "line": 1, "text": "A", "complete_file": True}
    # Fill all eight slots. Only the target's marker can supply a late excerpt.
    unavailable = [
        {"ref": peer.ref, "line": line, "text": "?", "complete_file": False} for line in range(2, 9)
    ]
    late = [
        {**marker, "line": line, "text": lines[line - 1], "complete_file": False} for line in (2, 3)
    ]
    reads = [_wire_read(span, target) for span in late]
    if not valid_binding:
        reads[-1]["sha256"] = "0" * 64
    serve_citations(store, monkeypatch, evidence=[marker, *unavailable], current_repository_reads=reads)
    task = TaskMCP(Workflow(client, "team", repo, repository_id="project"), trail.question)
    result = json.loads(asyncio.run(task.call_tool("research", {}))[0].text)
    assert len(responses) == 1
    if valid_binding:
        assert result["hit"] and result["evidence"] == late[:1]
        assert result["complete_files"] == [] and "content" not in result
    else:
        assert not result["hit"] and result["reason"] == "git_local_evidence_not_verified"
        assert "evidence" not in result


@pytest.mark.parametrize("widths", [(1, 10), (10, 1)], ids=["short-first", "matching-first"])
def test_whole_marker_width_does_not_change_admission_at_limit(transport, monkeypatch, widths):
    client, repo, store, responses = transport
    lines = ["VALUE = 42", *"ABCDEF"]
    target_text, peer_text = "\n".join(lines), "WORKERS = 12"
    trail = publish(client, repo, git=True, file_texts=[target_text, peer_text])
    target, peer = trail.sources
    whole = {"ref": target.ref, "line": 1, "text": target_text, "complete_file": True}
    peer_span = {**whole, "ref": peer.ref, "text": peer_text}
    markers = [{**whole, "text": "?" * width} for width in widths]
    explicit = [
        {**whole, "line": line, "text": text, "complete_file": False}
        for line, text in enumerate(lines, 1)
    ]
    serve_citations(
        store, monkeypatch, evidence=[*markers, *explicit],
        current_repository_reads=[_wire_read(peer_span, peer)], evidence_scope="partial",
    )
    result = client.ask(
        trail.question, "team", repo=repo, repository_id="project", include_evidence_files=True
    )
    assert len(responses) == 1 and result["evidence"] == [whole, peer_span]
    assert result["complete_files"] == [target.ref, peer.ref] and result["evidence_scope"] == "cited_files"


@pytest.mark.parametrize("mixed", [False, True], ids=["legacy", "mixed-fields"])
@pytest.mark.parametrize(
    ("spare", "early", "invalid_excerpt"),
    [(0, False, False), (1, False, False), (0, True, False), (0, True, True)],
    ids=["exact-budget", "room-for-prefix", "early-prefix", "unusable-late-excerpt"],
)
def test_whole_file_prefix_spends_budget_at_its_own_position(
    transport, monkeypatch, mixed, spare, early, invalid_excerpt
):
    client, repo, store, responses = transport
    files = ["A" * 16_100, "B" * 16_000, "C" * (8_000 - spare)]
    trail = publish(client, repo, git=True, file_texts=files)
    whole = [
        {"ref": source.ref, "line": 1, "text": text, "complete_file": True}
        for source, text in zip(trail.sources, files, strict=True)
    ]
    prefix = {**whole[0], "text": "?"}
    hints = [whole[0], prefix, *whole[1:]] if early else [*whole, prefix]
    if invalid_excerpt:
        hints.append({**prefix, "line": 2, "complete_file": False})
    split = 1 if mixed else len(hints)
    sources = {s.ref: s for s in trail.sources}
    serve_citations(
        store, monkeypatch, evidence=hints[:split],
        current_repository_reads=[_wire_read(h, sources[h["ref"]]) for h in hints[split:]],
        evidence_scope="cited_files",
    )
    result = client.ask(
        trail.question, "team", repo=repo, repository_id="project", include_evidence_files=True
    )
    local_prefix = {**prefix, "text": "A", "complete_file": False}
    expected = [local_prefix, whole[1]] if early else whole[1:] + ([local_prefix] if spare else [])
    assert len(responses) == 1 and result["evidence"] == expected
    assert result["complete_files"] == [s["ref"] for s in expected if s["complete_file"]]
    assert result["evidence_scope"] == "partial" and "current_repository_reads" not in result


@pytest.mark.parametrize("remaining", [1, 2], ids=["only-short-prefix-fits", "first-prefix-fits"])
def test_whole_file_prefix_remains_available_when_later_excerpt_no_longer_fits(
    transport, monkeypatch, remaining
):
    client, repo, store, responses = transport
    fact = "RETRIES = 5"
    files = ["AA\n" + fact + "\n" + "#" * 16_000, "B" * 16_000, "C" * (8_000 - remaining)]
    trail = publish(client, repo, git=True, file_texts=files)
    target, b, c = trail.sources
    marker = {"ref": target.ref, "line": 1, "text": "??", "complete_file": True}
    peers = [{**marker, "ref": s.ref, "text": t} for s, t in zip((b, c), files[1:], strict=True)]
    explicit = {**marker, "line": 2, "text": fact, "complete_file": False}
    serve_citations(
        store, monkeypatch, evidence=[marker, *peers],
        current_repository_reads=[_wire_read({**marker, "text": "?"}, target), _wire_read(explicit, target)],
        evidence_scope="cited_files",
    )
    result = client.ask(
        trail.question, "team", repo=repo, repository_id="project", include_evidence_files=True
    )
    fallback = {**marker, "text": "A" * remaining, "complete_file": False}
    assert len(responses) == 1
    assert result["evidence"] == ([fallback, *peers] if remaining == 2 else [*peers, fallback])
    assert result["complete_files"] == [b.ref, c.ref]
    assert result["evidence_scope"] == "partial" and "current_repository_reads" not in result


@pytest.mark.parametrize("mixed", [False, True], ids=["legacy", "mixed-fields"])
@pytest.mark.parametrize("peer_count", [2, 3], ids=["spare-slot", "four-read-limit"])
def test_whole_file_marker_keeps_global_excerpt_order(transport, monkeypatch, mixed, peer_count):
    client, repo, store, responses = transport
    alpha, retry = "ALPHA = 1", "RETRIES = 5"
    peer_texts = ["WORKERS = 12", "TIMEOUT = 30", "BATCH = 4"][:peer_count]
    files = ["\n".join([alpha, retry, "#" * 16_000]), *peer_texts]
    trail = publish(client, repo, git=True, file_texts=files)
    target, *peers = trail.sources
    first = {"ref": target.ref, "line": 1, "text": alpha, "complete_file": False}
    second = {**first, "line": 2, "text": retry}
    marker = {**first, "text": "?", "complete_file": True}
    peer_spans = [
        {**first, "ref": s.ref, "text": text} for s, text in zip(peers, peer_texts, strict=True)
    ]
    legacy = [second, marker, *peer_spans]
    serve_citations(
        store, monkeypatch, evidence=legacy if mixed else [*legacy, first],
        current_repository_reads=[_wire_read(first, target)] if mixed else [],
        evidence_scope="cited_files",
    )
    result = client.ask(
        trail.question, "team", repo=repo, repository_id="project", include_evidence_files=True
    )
    assert len(responses) == 1 and result["evidence"] == [second, *peer_spans, first][:4]
    assert result["complete_files"] == [] and result["evidence_scope"] == "partial"
    assert "current_repository_reads" not in result


@pytest.mark.parametrize("policy", ["allow-both", "deny-second", "unbound-read"])
def test_client_checks_paths_with_null_legacy_evidence(transport, monkeypatch, policy):
    client, repo, store, responses = transport
    trail = publish(client, repo, count=2)
    reads = [
        {
            "path": s.ref,
            "sha256": s.content_hash,
            "start_line": 2,
            "content": (repo / s.ref).read_text(encoding="utf-8").splitlines()[1],
        }
        for s in trail.sources
    ]
    allowed = {s.ref for s in trail.sources}
    if policy == "deny-second":
        allowed.remove(reads[1]["path"])
    elif policy == "unbound-read":
        reads[1]["path"] = "uncited.py"
    serve_citations(store, monkeypatch, evidence=None, current_repository_reads=reads)
    task = TaskMCP(
        Workflow(client, "team", repo), trail.question, read_path_allowed=allowed.__contains__
    )
    result = json.loads(asyncio.run(task.call_tool("research", {}))[0].text)
    assert len(responses) == 1
    if policy == "allow-both":
        assert result["hit"]
        assert result["evidence"] == [
            {"ref": r["path"], "line": 2, "text": r["content"]} for r in reads
        ]
        assert "current_repository_reads" not in result and "content" not in result
    else:
        assert result == {"hit": False, "reason": "lookup_unavailable"}


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
