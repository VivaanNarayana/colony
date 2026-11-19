"""Optional presentation hints must never displace already fitted evidence."""

import copy
import hashlib
import os

import pytest

from colony.compact import PROFILE
from colony.context import source_lines
from colony.context_budget import count_response, fit_response, with_citation_bounds
from colony.models import ContextBudget, Source


@pytest.fixture(params=["utf8_bytes", "o200k_base"])
def encoding(request, monkeypatch):
    if request.param == "o200k_base":
        pytest.importorskip("tiktoken")
        path = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
        if not path:
            pytest.skip("Pinned offline tokenizer required")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", path)
    return request.param


def packet(text="alpha\nbeta\ngamma", start=1, complete=True):
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {
        "response_profile": PROFILE, "hit": True, "status": "validated",
        "match_kind": "evidence", "sources": [{"ref": "notes.txt", "content_hash": digest}],
        "current_repository_reads": [{"path": "notes.txt", "sha256": digest,
                                      "content": text, "start_line": start,
                                      "complete_file": complete}],
    }


@pytest.mark.parametrize("text,start,end", [
    ("only", 1, 1), ("one\ntwo\nthree\nfour\nfive", 1, 5),
    ("one\n\nthree", 12, 14), ("last\n", 7, 8), ("a\nb", 91, 92),
])
def test_exact_emitted_line_bounds_preserve_every_source_field(encoding, text, start, end):
    original = packet(text, start, start == 1)
    frozen = copy.deepcopy(original)
    budget = ContextBudget(max_tokens=8192, encoding=encoding)
    fitted = fit_response(original, budget)
    shown = with_citation_bounds(fitted, budget)
    assert shown["current_repository_reads"] == [
        {**fitted["current_repository_reads"][0], "end_line": end},
    ]
    assert shown["sources"] == fitted["sources"]
    assert shown["evidence_scope"] == fitted["evidence_scope"]
    assert shown["context_budget"]["used"] == count_response(shown, budget) <= 8192
    assert with_citation_bounds(shown, budget) == shown
    assert with_citation_bounds(fit_response(shown, budget), budget) == shown
    assert original == frozen


def test_hints_cannot_cause_a_miss_or_displace_optional_file_expansion(encoding):
    original = packet("heading\n" + "body\n" * 40 + "tail")
    original["current_repository_reads"][0].update(selected_line=2, selected_end_line=2)
    original["context_budget"] = {"limited": True}
    wide = ContextBudget(max_tokens=8192, encoding=encoding)
    maximum = fit_response(original, wide)["context_budget"]["used"]
    for _ in range(8):
        budget = ContextBudget(max_tokens=maximum, encoding=encoding)
        fitted = fit_response(original, budget)
        assert fitted["hit"] and fitted["current_repository_reads"][0]["complete_file"]
        if fitted["context_budget"]["used"] == maximum:
            break
        maximum = fitted["context_budget"]["used"]
    assert fitted["context_budget"]["used"] == maximum
    assert with_citation_bounds(fitted, budget) == fitted
    assert fitted["current_repository_reads"][0]["content"] == original["current_repository_reads"][0]["content"]


@pytest.mark.parametrize("forged", [-1, True, 999999, "wrong"])
def test_untrusted_endpoints_do_not_affect_packing_or_survive(encoding, forged):
    original = packet()
    forged_packet = copy.deepcopy(original)
    forged_packet["current_repository_reads"][0]["end_line"] = forged
    budget = ContextBudget(max_tokens=8192, encoding=encoding)
    baseline = fit_response(original, budget)
    assert fit_response(forged_packet, budget) == baseline
    assert with_citation_bounds(fit_response(forged_packet, budget), budget)[
        "current_repository_reads"
    ][0]["end_line"] == 3


def test_packed_excerpt_bounds_do_not_describe_the_original_file(encoding):
    original = packet("first\nsecond\nthird\nfourth\nfifth", 1, False)
    original["current_repository_reads"][0].update(selected_line=2, selected_end_line=3)
    budget = ContextBudget(max_tokens=8192, encoding=encoding)
    shown = with_citation_bounds(fit_response(original, budget), budget)
    read = shown["current_repository_reads"][0]
    assert (read["content"], read["start_line"], read["end_line"]) == ("second\nthird", 2, 3)
    assert read["complete_file"] is False and shown["evidence_scope"] == "partial"


@pytest.mark.parametrize("status", ["stale", "unverified"])
def test_unverified_reads_get_no_citation_hint(encoding, status):
    original = packet()
    original["status"] = status
    budget = ContextBudget(max_tokens=8192, encoding=encoding)
    fitted = fit_response(original, budget)
    assert with_citation_bounds(fitted, budget) == fitted


def test_legacy_responses_are_byte_identical(encoding):
    original = packet()
    original.pop("response_profile")
    original["current_repository_reads"][0]["future_field"] = "retain"
    budget = ContextBudget(max_tokens=8192, encoding=encoding)
    fitted = fit_response(original, budget)
    assert with_citation_bounds(fitted, budget) == fitted


@pytest.mark.parametrize("raw,expected", [
    (b"one", 1), (b"one\n", 1), (b"one\r\n", 1),
    (b"one\n\n", 2), (b"one\r\n\r\n", 2), (b"one\n\nthree\n", 3),
])
def test_actual_source_normalization_does_not_invent_an_eof_line(tmp_path, encoding, raw, expected):
    path = tmp_path / "notes.txt"
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    lines = source_lines(tmp_path, Source(ref=path.name, content_hash=digest))
    original = packet("\n".join(lines))
    original["sources"][0]["content_hash"] = digest
    original["current_repository_reads"][0]["sha256"] = digest
    budget = ContextBudget(max_tokens=8192, encoding=encoding)
    shown = with_citation_bounds(fit_response(original, budget), budget)
    assert shown["current_repository_reads"][0]["end_line"] == expected
    assert shown["current_repository_reads"][0]["content"] == "\n".join(lines)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("count", [1, 5, 8])
def test_multi_file_hint_fallback_keeps_the_entire_fitted_response(encoding, count):
    original = packet("first\n" + "body\n" * 40 + "last")
    read = original["current_repository_reads"][0]
    original["current_repository_reads"] = [
        {**read, "path": f"part{i}.txt"} for i in range(count)
    ]
    original["sources"] = [
        {"ref": r["path"], "content_hash": r["sha256"]}
        for r in original["current_repository_reads"]
    ]
    original["context_budget"] = {"limited": True}
    maximum = fit_response(original, ContextBudget(max_tokens=8192, encoding=encoding))[
        "context_budget"
    ]["used"]
    budget = ContextBudget(max_tokens=maximum, encoding=encoding)
    baseline = fit_response(original, budget)
    assert baseline["hit"] and len(baseline["current_repository_reads"]) == count
    assert with_citation_bounds(baseline, budget) == baseline
    wide = ContextBudget(max_tokens=8192, encoding=encoding)
    shown = with_citation_bounds(fit_response(original, wide), wide)
    assert all(r["end_line"] == 42 for r in shown["current_repository_reads"])


def test_empty_text_never_gets_an_invented_line_hint(encoding):
    budget = ContextBudget(max_tokens=8192, encoding=encoding)
    baseline = fit_response(packet(""), budget)
    assert with_citation_bounds(baseline, budget) == baseline
