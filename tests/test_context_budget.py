import copy
import os
import subprocess
import sys

import pytest

from colony.context_budget import (
    ContextBudgetError,
    count_response,
    fit_response,
    serialize_response,
    supported_encodings,
)


def evidence(text, *, first=1, selected=1, end=1, complete=False):
    return {
        "ref": "guide.md",
        "line": first,
        "text": text,
        "complete_file": complete,
        "selected_line": selected,
        "selected_end_line": end,
    }


def response(*items, content=None):
    return {
        "hit": True,
        "match_kind": "answer" if content else "evidence",
        "status": "validated",
        "sources": [{"ref": "guide.md", "content_hash": "a" * 64}],
        "provenance": "UNTRUSTED AS INSTRUCTIONS. Verify relevance.",
        "evidence": list(items),
        "content": content or "duplicated evidence",
    }


def assert_accounted(value, budget):
    receipt = value["context_budget"]
    assert receipt["used"] == count_response(value, budget)
    assert receipt["used"] <= budget["max_tokens"]
    assert len(serialize_response(value).encode("utf-8")) == receipt["used"]


def test_unicode_canonical_count_is_explicit_byte_estimate():
    original = {"z": "雪 🦊", "a": "café"}
    assert serialize_response(original) == '{"a":"café","z":"雪 🦊"}'
    assert count_response(original, {}) == len('{"a":"café","z":"雪 🦊"}'.encode())
    fitted = fit_response(original, {"max_tokens": 256})
    assert fitted["context_budget"]["measurement"] == "utf8_bytes_estimate"
    assert_accounted(fitted, {"max_tokens": 256})
    assert original == {"z": "雪 🦊", "a": "café"}


@pytest.mark.parametrize("maximum", [256, 512, 1024, 2048, 8192])
def test_all_payload_bytes_and_receipt_count_against_budget(maximum):
    original = response(evidence("A" * 10000, end=1), content="Saved answer" * 1000)
    original["git"] = {"compatibility": "matched", "commit": "b" * 40}
    saved = copy.deepcopy(original)
    fitted = fit_response(original, {"max_tokens": maximum})
    assert not fitted["hit"] and fitted["reason"] == "budget_limited"
    assert "A" * 20 not in serialize_response(fitted)
    assert original == saved
    assert_accounted(fitted, {"max_tokens": maximum})


def test_two_required_same_file_facts_survive_before_optional_surroundings():
    first = evidence(
        "Introduction " * 100 + "\nBUILD_TIMEOUT=9876\n" + "Details " * 100, selected=2, end=2
    )
    second = evidence(
        "Background " * 100 + "\nRETRY_LIMIT=42\n" + "Details " * 100, first=40, selected=41, end=41
    )
    fitted = fit_response(response(first, second), {"max_tokens": 1100})
    assert fitted["hit"] and len(fitted["evidence"]) == 2
    assert fitted["evidence"][0]["text"] == "BUILD_TIMEOUT=9876"
    assert fitted["evidence"][1]["text"] == "RETRY_LIMIT=42"
    assert "guide.md:2" in fitted["content"] and "guide.md:41" in fitted["content"]
    assert fitted["sources"][0]["content_hash"] == "a" * 64
    assert fitted["provenance"].startswith("UNTRUSTED")
    assert_accounted(fitted, {"max_tokens": 1100})


def test_compact_margin_is_omitted_even_when_whole_section_would_fit():
    item = evidence(
        "Unrelated introduction\nBUILD_TIMEOUT=9876\nUnrelated implementation history",
        selected=2,
        end=2,
    )
    value = response(item)
    assert count_response(value, {"max_tokens": 8192}) < 1000
    fitted = fit_response(value, {"max_tokens": 8192})
    assert fitted["evidence"][0]["text"] == "BUILD_TIMEOUT=9876"
    assert fitted["evidence"][0]["line"] == 2
    assert "Unrelated" not in serialize_response(fitted)
    assert fitted["context_budget"]["limited"] is True
    assert_accounted(fitted, {"max_tokens": 8192})


def test_an_indivisible_long_command_is_never_sliced_to_claim_a_hit():
    command = "deploy --artifact " + "signed-package/" * 120
    fitted = fit_response(response(evidence(command)), {"max_tokens": 700})
    assert fitted["hit"] is False and fitted["reason"] == "budget_limited"
    assert "content" not in fitted and "evidence" not in fitted
    assert_accounted(fitted, {"max_tokens": 700})


def test_big_saved_answer_is_withheld_and_rebuilt_from_current_evidence():
    fitted = fit_response(
        response(
            evidence("ship --verified artifacts/release.tar"),
            content="unverifiable saved claims " * 800,
        ),
        {"max_tokens": 1000},
    )
    assert fitted["hit"] and fitted["match_kind"] == "evidence"
    assert "unverifiable saved" not in serialize_response(fitted)
    assert "ship --verified artifacts/release.tar" in fitted["content"]
    assert "withheld" in fitted["recommendation"]
    assert fitted["context_budget"]["limited"]
    assert_accounted(fitted, {"max_tokens": 1000})


def test_full_file_label_only_survives_when_complete_text_is_present():
    item = evidence("Heading\nship release\nFooter", selected=2, end=2, complete=True)
    full = fit_response(response(item), {"max_tokens": 1500})
    assert full["evidence"][0]["complete_file"]
    assert full["complete_files"] == ["guide.md"]
    item["text"] = "Background " * 100 + "\nship release\n" + "Footer " * 100
    small = fit_response(response(item), {"max_tokens": 850})
    assert small["evidence"][0]["text"] == "ship release"
    assert not small["evidence"][0]["complete_file"]
    assert small["complete_files"] == []
    assert "complete redacted file" not in small["content"]


def test_mcp_reads_pack_cores_preserve_hashes_and_safety():
    value = {
        "hit": True,
        "match_kind": "evidence",
        "status": "validated",
        "memory_warning": "User feedback is unverified.",
        "git": {"compatibility": "branch_mismatch"},
        "current_repository_reads": [
            {
                "path": "guide.md",
                "sha256": "c" * 64,
                "start_line": 10,
                "content": "Intro " * 150
                + "\n```sh\nship --signed release.tar\n```\n"
                + "Outro " * 150,
                "selected_line": 11,
                "selected_end_line": 13,
                "complete_file": False,
            }
        ],
    }
    fitted = fit_response(value, {"max_tokens": 900})
    read = fitted["current_repository_reads"][0]
    assert read["content"] == "```sh\nship --signed release.tar\n```"
    assert read["start_line"] == 11 and read["sha256"] == "c" * 64
    assert fitted["memory_warning"] == value["memory_warning"]
    assert fitted["git"] == value["git"]
    assert "content" not in fitted
    assert_accounted(fitted, {"max_tokens": 900})


def test_metadata_overflow_and_missing_selection_cannot_create_partial_answer():
    value = response(evidence("Required fact"))
    value["git"] = {"branch": "a" * 8000}
    assert fit_response(value, {"max_tokens": 500})["reason"] == "budget_limited"
    value = response(evidence("One\nTwo", selected=99, end=100))
    assert fit_response(value, {"max_tokens": 1000})["reason"] == "budget_limited"
    value = {"hit": True, "match_kind": "answer", "content": "claim " * 1000}
    assert fit_response(value, {"max_tokens": 500})["reason"] == "budget_limited"


def test_prior_limited_receipt_is_not_lost_during_client_or_mcp_refit():
    value = fit_response(response(evidence("Fact")), {"max_tokens": 1000})
    value["context_budget"]["limited"] = True
    again = fit_response(value, {"max_tokens": 2000})
    assert again["context_budget"]["limited"] is True
    assert_accounted(again, {"max_tokens": 2000})


@pytest.mark.parametrize(
    "configured", [None, "https://example.invalid/encoding", "//server/share/data", "relative-file"]
)
def test_unavailable_encoding_never_downloads_or_falls_back(monkeypatch, configured):
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("Network access attempted")

    monkeypatch.setattr(socket, "socket", blocked)
    if configured is None:
        monkeypatch.delenv("COLONY_CONTEXT_TOKENIZER", raising=False)
    else:
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", configured)
    assert supported_encodings() == ["utf8_bytes"]
    with pytest.raises(
        ContextBudgetError, match="^Local context tokenizer is unavailable or invalid$"
    ):
        fit_response({}, {"encoding": "o200k_base"})


def test_tampered_local_encoding_fails_closed_without_echoing_path(tmp_path, monkeypatch):
    path = tmp_path / "synthetic-private-path.tiktoken"
    path.write_bytes(b"not the official encoding")
    monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", str(path))
    with pytest.raises(ContextBudgetError) as failure:
        count_response({}, {"encoding": "o200k_base"})
    assert "synthetic" not in str(failure.value)


def test_cold_counter_import_has_no_retrieval_or_network_dependency():
    code = """
import socket, sys
def blocked(*a, **k): raise AssertionError('network attempted')
socket.socket = blocked
from colony.context_budget import fit_response
result = fit_response({'hit':False}, {'max_tokens':256})
assert result['context_budget']['used'] <= 256
assert not any(n in sys.modules for n in ('colony.retrieval','fastembed','onnxruntime','tiktoken'))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr


def test_adaptive_excerpts_keep_entire_fence_and_independent_same_file_facts(tmp_path, monkeypatch):
    import colony.context as context
    from colony.validation import snapshot

    lines = [
        "# Release",
        "",
        "```sh",
        "ship --signed release.tar",
        "",
        "verify release.tar",
        "```",
        "",
        "# Timing",
        "",
        "TIMEOUT=9876",
    ]
    (tmp_path / "guide.md").write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.setattr(context, "rank", lambda *a: pytest.fail("preferred sections reranked"))
    items = context.excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "release and timeout",
        preferred=[("guide.md", 3), ("guide.md", 11)],
        adaptive=True,
    )
    assert len(items) == 2
    assert items[0]["selected_line"] == 1 and items[0]["selected_end_line"] == 7
    packed = fit_response(response(*items), {"max_tokens": 1200})
    assert "```sh\nship --signed release.tar\n\nverify release.tar\n```" in packed["content"]
    assert "TIMEOUT=9876" in packed["content"]


def test_adaptive_long_paragraph_is_not_cut_at_legacy_window(tmp_path):
    from colony.context import excerpts
    from colony.validation import snapshot

    text = "# Configuration\n\n" + "\n".join(f"setting_{i}={i}" for i in range(40))
    (tmp_path / "guide.md").write_text(text)
    items = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "settings",
        preferred=[("guide.md", 19)],
        adaptive=True,
    )
    assert items[0]["selected_line"] == 1 and items[0]["selected_end_line"] == 42
    assert "setting_39=39" in items[0]["text"]


def test_legacy_excerpt_bytes_stay_unchanged_when_adaptive_is_omitted(tmp_path):
    from colony.context import excerpts
    from colony.validation import snapshot

    (tmp_path / "guide.md").write_text("# Release\n\nship release.tar\n")
    sources = [snapshot(tmp_path, "guide.md")]
    options = {"preferred": [("guide.md", 3)]}
    expected = [
        {
            "ref": "guide.md",
            "line": 1,
            "text": "# Release\n\nship release.tar",
            "complete_file": False,
        }
    ]
    assert excerpts(tmp_path, sources, "release", **options) == expected
    assert excerpts(tmp_path, sources, "release", adaptive=False, **options) == expected


def test_whole_file_expansion_cannot_hide_two_independent_cores(tmp_path):
    from colony.context import excerpts
    from colony.validation import snapshot

    (tmp_path / "guide.md").write_text("# Release\n\nship release.tar\n\nTIMEOUT=30\n")
    items = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "release timeout",
        preferred=[("guide.md", 3), ("guide.md", 5)],
        adaptive=True,
        whole_files=True,
    )
    assert [(e["line"], e["text"]) for e in items] == [
        (1, "# Release\n\nship release.tar\n\nTIMEOUT=30")
    ]
    assert items[0]["complete_file"]
    assert (items[0]["selected_line"], items[0]["selected_end_line"]) == (1, 5)


def test_fence_like_content_with_suffix_cannot_end_a_selected_code_block(tmp_path):
    from colony.context import excerpts
    from colony.validation import snapshot

    (tmp_path / "guide.md").write_text(
        "# Script\n\n```sh\nship start\n```literal-code\nship finish\n```\n"
    )
    items = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "script",
        preferred=[("guide.md", 3)],
        adaptive=True,
    )
    assert items[0]["selected_end_line"] == 7
    assert "ship finish\n```" in items[0]["text"]


@pytest.mark.parametrize(
    "opening,prefix,blank,closing",
    [
        ("    ```sh", "    ", "", "    ```"),
        ("\t```sh", "\t", "", "\t```"),
        ("- ```sh", "  ", "", "  ```"),
        ("1. ```sh", "   ", "", "   ```"),
        ("> ```sh", "> ", ">", "> ```"),
        ("- > ```sh", "  > ", "  >", "  > ```"),
    ],
)
def test_container_fences_keep_every_command_and_original_closing(
    tmp_path, opening, prefix, blank, closing
):
    from colony.context import excerpts
    from colony.validation import snapshot

    text = "\n".join(
        [
            "# Release",
            "",
            opening,
            prefix + "deploy --signed release.tar",
            blank,
            prefix + "verify release.tar",
            closing,
        ]
    )
    (tmp_path / "guide.md").write_text(text)
    items = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "release",
        preferred=[("guide.md", 3)],
        adaptive=True,
    )
    result = fit_response(response(*items), {"max_tokens": 2048})
    assert result["hit"]
    assert items[0]["selected_line"] == 1 and items[0]["selected_end_line"] == 7
    assert result["evidence"][0]["text"] == text
    assert "verify release.tar" in result["content"] and result["content"].endswith(closing)


def test_answer_mcp_complete_file_list_tracks_actual_remaining_read():
    value = {
        "hit": True,
        "match_kind": "answer",
        "status": "validated",
        "content": "Saved answer",
        "complete_files": ["guide.md"],
        "current_repository_reads": [
            {
                "path": "guide.md",
                "sha256": "d" * 64,
                "start_line": 1,
                "content": "intro " * 300 + "\nFACT=42",
                "selected_line": 2,
                "selected_end_line": 2,
                "complete_file": True,
            }
        ],
    }
    result = fit_response(value, {"max_tokens": 700})
    assert result["hit"] and result["content"] == "Saved answer"
    assert result["complete_files"] == []
    assert not result["current_repository_reads"][0]["complete_file"]


def test_required_nested_headings_qualify_value_without_later_sibling(tmp_path):
    from colony.context import excerpts
    from colony.validation import snapshot

    text = "# Deployment\n\n## Production\n\n### Replicas\n\nCOUNT=9\n\n## Development\n\nCOUNT=1"
    (tmp_path / "guide.md").write_text(text)
    items = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "production count",
        preferred=[("guide.md", 7)],
        adaptive=True,
    )
    fitted = fit_response(response(*items), {"max_tokens": 2048})
    assert fitted["hit"]
    assert fitted["evidence"][0]["text"] == "\n".join(text.splitlines()[:7])
    assert fitted["evidence"][0]["selected_line"] == 1
    assert "Development" not in fitted["content"] and "COUNT=1" not in fitted["content"]


def test_overlapping_ancestor_envelopes_merge_both_selected_facts_not_third(tmp_path):
    from colony.context import excerpts
    from colony.validation import snapshot

    text = "# Deployment\n\n## Production\n\nCOUNT=9\n\n## Development\n\nCOUNT=1\n\n# Other\n\nUNSELECTED=77"
    (tmp_path / "guide.md").write_text(text)
    items = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "compare deployment counts",
        preferred=[("guide.md", 5), ("guide.md", 9), ("guide.md", 13)],
        adaptive=True,
    )
    assert len(items) == 1
    fitted = fit_response(response(*items), {"max_tokens": 2048})
    assert fitted["hit"] and "Production\n\nCOUNT=9" in fitted["content"]
    assert "Development\n\nCOUNT=1" in fitted["content"]
    assert "UNSELECTED" not in fitted["content"]
    assert (items[0]["selected_line"], items[0]["selected_end_line"]) == (1, 9)


def test_sibling_top_level_heading_scopes_remain_distinct(tmp_path):
    from colony.context import excerpts
    from colony.validation import snapshot

    (tmp_path / "guide.md").write_text("# Production\n\nCOUNT=9\n\n# Development\n\nCOUNT=1")
    items = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "deployment counts",
        preferred=[("guide.md", 3), ("guide.md", 7)],
        adaptive=True,
    )
    fitted = fit_response(response(*items), {"max_tokens": 2048})
    assert [e["text"] for e in fitted["evidence"]] == [
        "# Production\n\nCOUNT=9",
        "# Development\n\nCOUNT=1",
    ]
    assert [e["line"] for e in fitted["evidence"]] == [1, 5]


def test_setext_ancestor_headings_are_required_source(tmp_path):
    from colony.context import excerpts
    from colony.validation import snapshot

    text = "Deployment\n==========\n\nProduction\n----------\n\nCOUNT=9"
    (tmp_path / "guide.md").write_text(text)
    items = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "production count",
        preferred=[("guide.md", 7)],
        adaptive=True,
    )
    assert fit_response(response(*items), {"max_tokens": 2048})["evidence"][0]["text"] == text


def test_required_heading_envelope_overflow_withholds_bare_value(tmp_path):
    from colony.context import excerpts
    from colony.validation import snapshot

    text = "# Deployment\n\n" + "Applicability information. " * 100 + "\n\n## Production\n\nCOUNT=9"
    (tmp_path / "guide.md").write_text(text)
    items = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "production count",
        preferred=[("guide.md", 7)],
        adaptive=True,
    )
    fitted = fit_response(response(*items), {"max_tokens": 900})
    assert not fitted["hit"] and fitted["reason"] == "budget_limited"
    assert "COUNT=9" not in serialize_response(fitted)


@pytest.mark.parametrize(
    "heading,body,blank,preferred",
    [
        ("- ## ", "  ", "", 3),
        ("1. ## ", "   ", "", 3),
        ("> ## ", "> ", ">", 1),
        ("- > ## ", "  > ", "  >", 1),
        ("- - ## ", "    ", "", 3),
    ],
)
def test_container_heading_labels_remain_with_both_profile_values(
    tmp_path, heading, body, blank, preferred
):
    from colony.context import excerpts
    from colony.validation import snapshot

    text = "\n".join(
        [
            heading + "Production profile",
            blank,
            body + "TIMEOUT=30",
            blank,
            heading + "Development profile",
            blank,
            body + "TIMEOUT=5",
        ]
    )
    (tmp_path / "guide.md").write_text(text)
    items = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "Compare profile timeouts",
        preferred=[("guide.md", preferred), ("guide.md", preferred + 4)],
        adaptive=True,
    )
    fitted = fit_response(response(*items), {"max_tokens": 2048})
    assert fitted["hit"]
    assert len(fitted["evidence"]) == 1 and fitted["evidence"][0]["text"] == text
    assert fitted["evidence"][0]["line"] == 1
    assert not fitted["evidence"][0]["complete_file"]
    small = fit_response(response(*items), {"max_tokens": 256})
    assert not small["hit"] and small["reason"] == "budget_limited"


@pytest.mark.parametrize(
    "prefix,body,blank", [("- ", "  ", ""), ("> ", "> ", ">"), ("- > ", "  > ", "  >")]
)
def test_container_setext_heading_is_retained_with_value(tmp_path, prefix, body, blank):
    from colony.context import excerpts
    from colony.validation import snapshot

    text = "\n".join(
        [prefix + "Production profile", body + "------------------", blank, body + "TIMEOUT=30"]
    )
    (tmp_path / "guide.md").write_text(text)
    preferred = 4 if not blank else 1
    items = excerpts(
        tmp_path,
        [snapshot(tmp_path, "guide.md")],
        "Production timeout",
        preferred=[("guide.md", preferred)],
        adaptive=True,
    )
    fitted = fit_response(response(*items), {"max_tokens": 2048})
    assert fitted["hit"] and fitted["evidence"][0]["text"] == text


@pytest.mark.parametrize(
    "name,shebang",
    [
        ("deploy.sh", ""),
        ("deploy", "#!/usr/bin/env -S bash\n\n"),
        ("deploy.payload", "#!/bin/bash\n\n"),
    ],
)
def test_blank_heredoc_process_substitution_and_trailing_flags_are_indivisible(
    tmp_path, name, shebang
):
    from colony.context import excerpts
    from colony.validation import snapshot

    text = (
        shebang
        + 'deploy --manifest=<(cat <<\'JSON\'\n\n{"mode": "production"}\nJSON\n) --production\n\nverify --remote release'
    )
    (tmp_path / name).write_text(text)
    line = 3 if shebang else 1
    items = excerpts(
        tmp_path,
        [snapshot(tmp_path, name)],
        "deploy production",
        preferred=[(name, line)],
        adaptive=True,
    )
    fitted = fit_response(response(*items), {"max_tokens": 2048})
    assert fitted["hit"] and fitted["evidence"][0]["text"] == text
    assert ") --production" in fitted["content"] and "verify --remote release" in fitted["content"]
    assert not fitted["evidence"][0]["complete_file"]
    assert (items[0]["selected_line"], items[0]["selected_end_line"]) == (1, len(text.splitlines()))


def test_large_required_script_overflow_never_returns_partial_command(tmp_path):
    from colony.context import excerpts
    from colony.validation import snapshot

    text = (
        "deploy --manifest=<(cat <<'JSON'\n\n"
        + '{"description":"'
        + "signed artifact " * 100
        + '"}\nJSON\n) --production'
    )
    (tmp_path / "deploy.sh").write_text(text)
    items = excerpts(
        tmp_path,
        [snapshot(tmp_path, "deploy.sh")],
        "deploy production",
        preferred=[("deploy.sh", 1)],
        adaptive=True,
    )
    fitted = fit_response(response(*items), {"max_tokens": 900})
    assert not fitted["hit"] and fitted["reason"] == "budget_limited"
    assert "deploy --manifest" not in serialize_response(fitted)


def test_verified_optional_o200k_fixture_counts_without_loader_or_network(monkeypatch):
    configured = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
    if not configured:
        pytest.skip("Explicit verified local tokenizer fixture not configured")
    import socket

    import tiktoken

    monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", configured)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network attempted"))
    monkeypatch.setattr(
        tiktoken, "get_encoding", lambda *a, **k: pytest.fail("downloading loader used")
    )
    budget = {"max_tokens": 256, "encoding": "o200k_base"}
    assert "o200k_base" in supported_encodings()
    assert count_response({"text": "hello world"}, budget) == 6
    value = fit_response({"hit": False, "reason": "not found 雪"}, budget)
    assert value["context_budget"]["measurement"] == "encoding_tokens"
    assert value["context_budget"]["used"] == count_response(value, budget)


def _indented_markdown_result(tmp_path, text, line, maximum=2048):
    from colony.context import excerpts, source_chunks
    from colony.validation import snapshot

    (tmp_path / "indented.md").write_text(text, encoding="utf-8")
    sources = [snapshot(tmp_path, "indented.md")]
    assert ("indented.md", line) in {
        (ref, start) for ref, start, _ in source_chunks(tmp_path, sources)
    }
    items = excerpts(
        tmp_path, sources, "complete command", preferred=[("indented.md", line)], adaptive=True
    )
    return items, fit_response(response(*items), {"max_tokens": maximum})


@pytest.mark.parametrize("indent", ["    ", "\t"])
@pytest.mark.parametrize("line", [3, 5])
def test_indented_markdown_retains_complete_block_from_beginning_or_middle(tmp_path, indent, line):
    command = [
        indent + "deploy --manifest=<(cat <<'JSON'",
        "",
        indent + '{"verify_signature":true}',
        indent + "JSON",
        indent + ") --production",
    ]
    text = "Introductory prose.\n\n" + "\n".join(command) + "\n\nUnrelated closing prose."
    items, result = _indented_markdown_result(tmp_path, text, line)
    assert result["hit"]
    assert items[0]["selected_line"] == 3 and items[0]["selected_end_line"] == 7
    assert result["evidence"][0]["text"] == "\n".join(command)
    assert "Introductory" not in result["content"] and "Unrelated" not in result["content"]
    assert not result["evidence"][0]["complete_file"]


def test_indented_markdown_keeps_active_heading_but_excludes_next_section(tmp_path):
    text = "Intro.\n\n# Release\n\n    deploy first\n\n    verify last\n\n# Other\n\nUnrelated."
    items, result = _indented_markdown_result(tmp_path, text, 5)
    assert result["hit"]
    assert (items[0]["selected_line"], items[0]["selected_end_line"]) == (3, 7)
    assert result["evidence"][0]["text"] == "# Release\n\n    deploy first\n\n    verify last"
    assert "Intro." not in result["content"] and "Other" not in result["content"]


def test_indented_fence_like_text_is_literal_and_does_not_swallow_later_heading(tmp_path):
    text = "    deploy first\n\n    ```literal\n    verify last\n\n# Actual scope\n\nVALUE=30"
    items, result = _indented_markdown_result(tmp_path, text, 3)
    assert result["evidence"][0]["text"] == "\n".join(text.splitlines()[:4])
    assert "Actual scope" not in result["content"]
    items, result = _indented_markdown_result(tmp_path, text, 8)
    assert result["evidence"][0]["text"] == "# Actual scope\n\nVALUE=30"
    assert items[0]["selected_line"] == 6


def test_indented_code_inside_genuine_fence_keeps_the_enclosing_fence(tmp_path):
    text = "```sh\n    deploy first\n\nverify last\n```\n\nUnrelated prose."
    items, result = _indented_markdown_result(tmp_path, text, 4)
    assert result["evidence"][0]["text"] == "\n".join(text.splitlines()[:5])
    assert (items[0]["selected_line"], items[0]["selected_end_line"]) == (1, 5)


def test_indented_paragraph_continuation_keeps_bounded_context_not_whole_document(tmp_path):
    text = "Paragraph context.\n    first continuation\n\n    final continuation\n\nUnrelated paragraph."
    items, result = _indented_markdown_result(tmp_path, text, 4)
    assert result["evidence"][0]["text"] == "\n".join(text.splitlines()[:4])
    assert "Unrelated" not in result["content"]
    assert items[0]["selected_line"] == 1


def test_two_space_prose_is_not_a_whole_program(tmp_path):
    text = "Paragraph context.\n  ordinary continuation\n\nSeparate paragraph."
    _, result = _indented_markdown_result(tmp_path, text, 1)
    assert result["evidence"][0]["text"] == "Paragraph context.\n  ordinary continuation"
    assert "Separate" not in result["content"]


def test_large_indented_markdown_block_withholds_instead_of_partial_command(tmp_path):
    text = "    deploy first\n\n    " + "required configuration " * 100 + "\n    verify last"
    _, result = _indented_markdown_result(tmp_path, text, 1, maximum=900)
    assert not result["hit"] and result["reason"] == "budget_limited"
    assert "deploy first" not in serialize_response(result)


def test_indented_markdown_legacy_excerpt_still_retains_original_bytes(tmp_path):
    from colony.context import excerpts
    from colony.validation import snapshot

    text = "Intro.\n\n    deploy first\n\n    verify last\n\nClosing prose."
    (tmp_path / "indented.md").write_text(text)
    sources = [snapshot(tmp_path, "indented.md")]
    options = {"preferred": [("indented.md", 3)]}
    expected = [{"ref": "indented.md", "line": 1, "text": text, "complete_file": False}]
    assert excerpts(tmp_path, sources, "complete command", **options) == expected
    assert excerpts(tmp_path, sources, "complete command", adaptive=False, **options) == expected


def test_indented_context_does_not_cross_a_preceding_genuine_fence(tmp_path):
    text = "```sh\nold command\n```\nParagraph context.\n    deploy first\n\n    verify last"
    items, result = _indented_markdown_result(tmp_path, text, 7)
    assert (
        result["evidence"][0]["text"] == "Paragraph context.\n    deploy first\n\n    verify last"
    )
    assert items[0]["selected_line"] == 4
    assert "old command" not in result["content"]


def test_genuine_markdown_fence_ignores_four_space_literal_closing_marker(tmp_path):
    text = "```text\n    ```\n    literal data\n\nlast required line\n```\n\nUnrelated prose."
    items, result = _indented_markdown_result(tmp_path, text, 1)
    assert result["evidence"][0]["text"] == "\n".join(text.splitlines()[:6])
    assert (items[0]["selected_line"], items[0]["selected_end_line"]) == (1, 6)


@pytest.mark.parametrize(
    "opening,literal,body,blank,last,closing",
    [
        ("- ```text", "      ```", "      literal data", "", "  last required line", "  ```"),
        ("1. ```text", "       ```", "       literal data", "", "   last required line", "   ```"),
        ("> ```text", ">     ```", ">     literal data", ">", "> last required line", "> ```"),
        (
            "- > ```text",
            "  >     ```",
            "  >     literal data",
            "  >",
            "  > last required line",
            "  > ```",
        ),
    ],
)
def test_container_fence_relative_indented_marker_is_literal(
    tmp_path, opening, literal, body, blank, last, closing
):
    text = "\n".join([opening, literal, body, blank, last, closing, "", "Unrelated prose."])
    items, result = _indented_markdown_result(tmp_path, text, 1)
    assert result["evidence"][0]["text"] == "\n".join(text.splitlines()[:6])
    assert items[0]["selected_end_line"] == 6
    assert "Unrelated prose" not in result["content"]


def _boundary_result(tmp_path, text, line, maximum=4096):
    from colony.context import excerpts, source_chunks
    from colony.validation import snapshot

    (tmp_path / "boundaries.md").write_text(text, encoding="utf-8")
    sources = [snapshot(tmp_path, "boundaries.md")]
    candidate = next(block for _, start, block in source_chunks(tmp_path, sources) if start == line)
    items = excerpts(
        tmp_path,
        sources,
        "Required deployment facts",
        preferred=[("boundaries.md", line)],
        adaptive=True,
    )
    return candidate, items, fit_response(response(*items), {"max_tokens": maximum})


@pytest.mark.parametrize("indent", ["    ", "\t"])
def test_indented_ordinary_paragraph_continuation_keeps_fact_after_selected_window_start(
    tmp_path, indent
):
    lines = [f"Deployment procedure explanation {i}." for i in range(8)]
    lines += [
        indent + "Production deployment timeout is",
        "TIMEOUT=9876; never use staging values.",
    ]
    lines += [f"Further deployment detail {i}." for i in range(10)]
    text = "\n".join(lines) + "\n\nUnrelated next paragraph."
    candidate, items, result = _boundary_result(tmp_path, text, 9)
    assert "TIMEOUT=9876" in candidate
    assert result["hit"] and result["evidence"][0]["text"] == "\n".join(lines)
    assert "TIMEOUT=9876" in result["content"] and "Unrelated" not in result["content"]
    assert (items[0]["selected_line"], items[0]["selected_end_line"]) == (1, 20)


@pytest.mark.parametrize("caption_lines,selected", [(1, 1), (16, 17)])
def test_attached_caption_and_complete_interrupting_fence_stay_together(
    tmp_path, caption_lines, selected
):
    caption = ["Do not use this production command:"] * caption_lines
    code = [
        "```sh",
        "deploy --manifest=<(cat <<'JSON'",
        "",
        '{"verified":true}',
        "JSON",
        ") --production",
        "```",
    ]
    required = "\n".join(caption + code)
    _, items, result = _boundary_result(tmp_path, required + "\n\nUnrelated prose.", selected)
    assert result["hit"] and result["evidence"][0]["text"] == required
    assert result["evidence"][0]["text"].startswith("Do not use")
    assert ") --production\n```" in result["content"]
    assert items[0]["selected_end_line"] == caption_lines + len(code)
    assert "Unrelated" not in result["content"]


def test_paragraph_envelope_expands_across_two_adjacent_fences(tmp_path):
    lines = [
        "Compare commands:",
        "```sh",
        "deploy first",
        "```",
        "```sh",
        "verify second",
        "",
        "REQUIRED_FACT=42",
        "```",
    ]
    text = "\n".join(lines) + "\n\nUnrelated prose."
    _, _, result = _boundary_result(tmp_path, text, 1)
    assert result["hit"] and result["evidence"][0]["text"] == "\n".join(lines)
    assert "REQUIRED_FACT=42" in result["content"] and "Unrelated" not in result["content"]


def test_selected_window_at_fence_closer_keeps_immediately_following_paragraph_fact(tmp_path):
    lines = ["```text", "Initial required command", ""]
    lines += [f"literal code {i}" for i in range(8)]
    lines += ["```", "REQUIRED_FACT=42"]
    lines += [f"Further procedure detail {i}." for i in range(10)]
    text = "\n".join(lines) + "\n\nUnrelated next paragraph."
    candidate, _, result = _boundary_result(tmp_path, text, 12)
    assert "REQUIRED_FACT=42" in candidate
    assert result["hit"] and result["evidence"][0]["text"] == "\n".join(lines)
    assert "REQUIRED_FACT=42" in result["content"] and "Unrelated" not in result["content"]


def test_interrupted_paragraph_overflow_withholds_instead_of_returning_command_prefix(tmp_path):
    text = (
        "Deploy with this manifest:\n```sh\ndeploy --manifest=<(cat <<'JSON'\n\n"
        + '{"required":"'
        + "signed artifact " * 150
        + '"}\nJSON\n) --production\n```'
    )
    _, _, result = _boundary_result(tmp_path, text, 1, maximum=900)
    assert not result["hit"] and result["reason"] == "budget_limited"
    assert "deploy --manifest" not in serialize_response(result)


def test_paragraph_after_completed_fence_does_not_absorb_prior_unrelated_code(tmp_path):
    lines = ["```sh", "old unrelated command", "```"]
    paragraph = [f"Current procedure detail {i}." for i in range(20)]
    _, items, result = _boundary_result(tmp_path, "\n".join(lines + paragraph), 9)
    assert result["hit"] and result["evidence"][0]["text"] == "\n".join(paragraph)
    assert items[0]["selected_line"] == 4 and "old unrelated" not in result["content"]


def test_heading_without_blank_before_indented_code_keeps_all_commands(tmp_path):
    text = "# Release\n    deploy first\n\n    verify last"
    _, _, result = _boundary_result(tmp_path, text, 1)
    assert result["hit"] and result["evidence"][0]["text"] == text


def test_real_heading_after_indented_paragraph_continuation_is_not_hidden_as_code(tmp_path):
    lines = [f"Ordinary explanation {i}." for i in range(8)]
    lines += ["    paragraph continuation", "# Production", "", "TIMEOUT=9876"]
    _, items, result = _boundary_result(tmp_path, "\n".join(lines), 12)
    assert result["hit"] and result["evidence"][0]["text"] == "# Production\n\nTIMEOUT=9876"
    assert items[0]["selected_line"] == 10


@pytest.mark.parametrize(
    "caption,indent",
    [
        ("- Production deployment:", "    "),
        ("1. Production deployment:", "    "),
        ("- Deployment:\n  - Production:", "      "),
    ],
)
def test_list_relative_fence_interrupt_requires_complete_original_file(tmp_path, caption, indent):
    text = "\n".join(
        [
            caption,
            indent + "```sh",
            indent + "deploy --manifest=<(cat <<'JSON'",
            "",
            indent + '{"verify_signature":true}',
            indent + "JSON",
            indent + ") --production",
            indent + "```",
            "",
            "Original terminal note.",
        ]
    )
    candidate, items, result = _boundary_result(tmp_path, text, 1, maximum=2048)
    assert "deploy --manifest" in candidate
    assert result["hit"] and result["evidence"][0]["text"] == text
    assert (items[0]["selected_line"], items[0]["selected_end_line"]) == (1, len(text.splitlines()))
    assert not result["evidence"][0]["complete_file"]
    assert ") --production" in result["content"] and "Original terminal note." in result["content"]
    assert_accounted(result, {"max_tokens": 2048})


def test_required_ambiguous_container_file_overflow_never_returns_command_prefix(tmp_path):
    text = (
        "- Production deployment:\n    ```sh\n    deploy first\n\n    verify last\n    ```"
        "\n\nOriginal required context. " + "original context " * 150
    )
    _, _, result = _boundary_result(tmp_path, text, 1, maximum=900)
    assert not result["hit"] and result["reason"] == "budget_limited"
    assert "deploy first" not in serialize_response(result)
    assert_accounted(result, {"max_tokens": 900})


def test_indented_fence_like_prose_without_container_does_not_require_unrelated_file_tail(tmp_path):
    paragraph = "Plain explanation.\n    ```literal example\nTIMEOUT=42"
    _, _, result = _boundary_result(tmp_path, paragraph + "\n\nUnrelated tail.", 1)
    assert result["hit"] and result["evidence"][0]["text"] == paragraph
    assert "TIMEOUT=42" in result["content"] and "Unrelated tail" not in result["content"]


@pytest.mark.parametrize(
    "caption,opening_indent,body_indent",
    [
        ("- Production:", "    ", "  "),
        ("1. Production:", "     ", "   "),
        ("- Deployment:\n  - Production:", "      ", "    "),
    ],
)
@pytest.mark.parametrize("select_body", [False, True])
def test_blank_separated_list_fence_keeps_less_indented_body_and_opening(
    tmp_path, caption, opening_indent, body_indent, select_body
):
    text = "\n".join(
        [
            caption,
            "",
            opening_indent + "```sh",
            body_indent + "deploy --manifest=<(cat <<'JSON'",
            "",
            body_indent + '{"verified":true}',
            body_indent + "JSON",
            body_indent + ") --production",
            body_indent + "```",
            "",
            "Original terminal note.",
        ]
    )
    line = len(caption.splitlines()) + (5 if select_body else 2)
    candidate, items, result = _boundary_result(tmp_path, text, line, maximum=2048)
    assert ('"verified"' if select_body else "deploy --manifest") in candidate
    assert result["hit"] and result["evidence"][0]["text"] == text
    assert (items[0]["selected_line"], items[0]["selected_end_line"]) == (1, len(text.splitlines()))
    assert not result["evidence"][0]["complete_file"]
    assert_accounted(result, {"max_tokens": 2048})


@pytest.mark.parametrize("separator", ["***", "___", "- - -", "* \t* *", "   ___\t", "---"])
def test_thematic_break_ends_paragraph_before_complete_indented_command(tmp_path, separator):
    required = (
        "Deploy using this manifest:\n"
        + separator
        + "\n    deploy --manifest=<(cat <<'JSON'\n\n    {\"verified\":true}\n"
        "    JSON\n    ) --production"
    )
    _, items, result = _boundary_result(tmp_path, required + "\n\nUnrelated tail.", 1, maximum=2048)
    assert result["hit"] and result["evidence"][0]["text"] == required
    assert items[0]["selected_end_line"] == 7 and "Unrelated tail" not in result["content"]
    assert_accounted(result, {"max_tokens": 2048})


@pytest.mark.parametrize("separator", ["***", "___", "- - -"])
def test_nested_container_thematic_break_requires_complete_source(tmp_path, separator):
    text = (
        "- Deployment:\n  - Production:\n    "
        + separator
        + "\n        deploy --manifest=<(cat <<'JSON'\n\n        {\"verified\":true}\n"
        "        JSON\n        ) --production\n\nOriginal terminal note."
    )
    _, _, result = _boundary_result(tmp_path, text, 1, maximum=2048)
    assert result["hit"] and result["evidence"][0]["text"] == text
    assert not result["evidence"][0]["complete_file"]


@pytest.mark.parametrize("separator", ["***", "___"])
def test_quoted_thematic_boundary_keeps_following_command_complete(tmp_path, separator):
    text = (
        "> Deployment:\n> "
        + separator
        + "\n    deploy --manifest=<(cat <<'JSON'\n\n    {\"verified\":true}\n"
        "    JSON\n    ) --production\n\nOriginal terminal note."
    )
    _, _, result = _boundary_result(tmp_path, text, 1, maximum=2048)
    assert result["hit"] and result["evidence"][0]["text"] == text


def test_indented_thematic_marker_in_prose_remains_a_continuation(tmp_path):
    paragraph = "Plain explanation.\n    ***\nTIMEOUT=42"
    _, _, result = _boundary_result(tmp_path, paragraph + "\n\nUnrelated tail.", 1)
    assert result["hit"] and result["evidence"][0]["text"] == paragraph


def test_indented_quote_thematic_text_without_container_stays_literal(tmp_path):
    code = "    > ***\n\n    literal continuation"
    _, _, result = _boundary_result(tmp_path, code + "\n\nUnrelated tail.", 1)
    assert result["hit"] and result["evidence"][0]["text"] == code
