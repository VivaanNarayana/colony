"""Cited dependency bundles are bounded by evidence size, not a four-file accident."""

import pytest
import test_compact_delivery as controls

from colony import context
from colony.compact import PROFILE, compact_response
from colony.context_budget import count_response, fit_response
from colony.models import ContextBudget
from colony.validation import snapshot


@pytest.mark.parametrize("count", [3, 5, 6, 8])
@pytest.mark.parametrize("reverse", [False, True])
def test_small_complete_bundle_keeps_every_cited_dependency(tmp_path, monkeypatch, count, reverse):
    names = [f"component_{i}.py" for i in range(count)]
    for i, name in enumerate(names):
        (tmp_path / name).write_text(f"VALUE_{i} = {i + 17}\n", encoding="utf-8")
    sources = [snapshot(tmp_path, name) for name in names[:: -1 if reverse else 1]]
    monkeypatch.setattr(context, "rank", lambda q, texts: [0.0] * len(texts))
    result = context.excerpts(tmp_path, sources, "Explain these components",
                              claims="Configuration flows through the components.",
                              adaptive=True, compact_bundle=True)
    assert {x["ref"] for x in result} == set(names)
    assert all(x["complete_file"] and x["line"] == x["selected_line"] == 1 for x in result)
    assert sum(len(x["text"]) for x in result) <= 2800


def test_bundle_does_not_expand_existing_eight_source_work_limit(tmp_path, monkeypatch):
    sources = []
    for i in range(9):
        name = f"part{i}.py"
        (tmp_path / name).write_text(f"VALUE = {i}\n", encoding="utf-8")
        sources.append(snapshot(tmp_path, name))
    monkeypatch.setattr(context, "rank", lambda q, texts: [0.0] * len(texts))
    reads = []
    original = context.source_lines

    def tracked(root, source):
        reads.append(source.ref)
        return original(root, source)

    monkeypatch.setattr(context, "source_lines", tracked)
    result = context.excerpts(tmp_path, sources, "VALUE", claims="Values",
                              adaptive=True, compact_bundle=True)
    assert len(result) <= 2 and "part8.py" not in reads


def test_legacy_explicit_files_keep_original_selection(tmp_path, monkeypatch):
    sources = []
    for i in range(5):
        name = f"part{i}.py"
        (tmp_path / name).write_text(f"VALUE = {i}\n", encoding="utf-8")
        sources.append(snapshot(tmp_path, name))
    monkeypatch.setattr(context, "rank", lambda q, texts: [0.0] * len(texts))
    assert len(context.excerpts(tmp_path, sources, "VALUE", whole_files=True)) <= 2


@pytest.fixture
def transport(tmp_path, monkeypatch):
    return controls.transport.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("count", [5, 6, 8])
def test_more_than_four_files_survive_actual_mcp_with_full_accounting(transport, count):
    client, repo, _, _ = transport
    trail = controls.publish(client, repo, count, content="Configuration components.")
    budget = ContextBudget(max_tokens=6000)
    value = controls.ask_mcp(client, repo, budget)
    assert {r["path"] for r in value["current_repository_reads"]} == {s.ref for s in trail.sources}
    assert all(r["complete_file"] for r in value["current_repository_reads"])
    assert value["evidence_scope"] == "cited_files"
    assert count_response(value, budget) == value["context_budget"]["used"] <= 6000
    too_small = controls.ask_mcp(client, repo, ContextBudget(max_tokens=256))
    assert not too_small["hit"] and too_small["reason"] == "budget_limited"


def test_partial_scope_is_computed_from_delivered_reads_not_supplied_claim():
    digest = "a" * 64
    value = {"response_profile": PROFILE, "hit": True, "match_kind": "evidence",
             "sources": [{"ref": "a.py", "content_hash": digest},
                         {"ref": "b.py", "content_hash": digest}],
             "evidence_scope": "cited_files",
             "current_repository_reads": [{"path": "a.py", "sha256": digest,
                 "start_line": 1, "content": "VALUE = 7", "complete_file": True}]}
    assert compact_response(value)["evidence_scope"] == "partial"
    value["sources"].pop()
    assert compact_response(value)["evidence_scope"] == "cited_files"
    value["current_repository_reads"][0]["complete_file"] = False
    assert compact_response(value)["evidence_scope"] == "partial"


def test_coverage_tracks_actual_post_packing_source_expansion():
    digest = "b" * 64
    value = {"response_profile": PROFILE, "hit": True, "status": "validated",
             "match_kind": "evidence", "sources": [{"ref": "code.py", "content_hash": digest}],
             "current_repository_reads": [{"path": "code.py", "sha256": digest,
                 "start_line": 1, "content": "VALUE = 7\n# " + "x" * 1500,
                 "complete_file": True, "selected_line": 1, "selected_end_line": 1}]}
    small = ContextBudget(max_tokens=900)
    partial = fit_response(value, small)
    assert partial["hit"] and partial["evidence_scope"] == "partial"
    assert partial["current_repository_reads"][0]["content"] == "VALUE = 7"
    assert count_response(partial, small) == partial["context_budget"]["used"] <= 900
    large = ContextBudget(max_tokens=6000)
    complete = fit_response(value, large)
    assert complete["hit"] and complete["evidence_scope"] == "cited_files"
    assert complete["current_repository_reads"][0]["content"] == value["current_repository_reads"][0]["content"]
    assert count_response(complete, large) == complete["context_budget"]["used"] <= 6000
