"""Unicode and pinned-tokenizer checks for the bounded evidence fallback."""

import os

import pytest
from test_compact_budget_fallback import ask, original_packet
from test_compact_budget_fallback import transport as transport

from colony.context_budget import count_response, serialize_response
from colony.models import ContextBudget, Trail
from colony.validation import snapshot


@pytest.mark.parametrize("encoding,maximum", [("utf8_bytes", 2048), ("o200k_base", 512)])
def test_unicode_fallback_respects_selected_encoding(transport, monkeypatch, encoding, maximum):
    if encoding == "o200k_base":
        pytest.importorskip("tiktoken")
        tokenizer = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
        if not tokenizer:
            pytest.skip("Explicit verified local tokenizer fixture unavailable")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", tokenizer)
    client, repo, database, _ = transport
    current = 'NEEDLE = "café 🐜"'
    texts = {"part0.py": "#", "part1.py": "OTHER_1 = 1", "part2.py": "OTHER_2 = 2",
             "part3.py": current}
    texts["part0.py"] += "é" * (2800 - sum(map(len, texts.values())))
    assert sum(map(len, texts.values())) == 2800
    for name, text in texts.items():
        (repo / name).write_bytes(text.encode("utf-8"))
    trail = Trail(
        id="a" * 32, namespace="team", question="What is NEEDLE?",
        content="SAVED_FINDING_MUST_NOT_SURVIVE_FALLBACK",
        sources=[snapshot(repo, name) for name in texts],
    )
    assert client.push(trail)["stored"]
    budget = ContextBudget(max_tokens=maximum, encoding=encoding)
    before = original_packet(database, trail, budget)
    assert not before["hit"] and before["reason"] == "budget_limited"
    assert before["context_budget"]["used"] == count_response(before, budget) <= maximum
    value = ask(client, trail, budget)
    assert value["hit"] and value["status"] == "validated" and value["id"] == trail.id
    assert value["match_kind"] == "evidence" and "content" not in value
    assert value["context_budget"]["limited"] is True
    assert value["context_budget"]["encoding"] == encoding
    assert value["context_budget"]["measurement"] == (
        "utf8_bytes_estimate" if encoding == "utf8_bytes" else "encoding_tokens")
    assert value["context_budget"]["used"] == count_response(value, budget) <= maximum
    assert len(value["current_repository_reads"]) == 1
    read = value["current_repository_reads"][0]
    assert read["path"] == "part3.py" and read["content"] == current and read["start_line"] == 1
    identities = {source["ref"]: source["content_hash"] for source in value["sources"]}
    assert identities == {source.ref: source.content_hash for source in trail.sources}
    assert read["sha256"] == identities["part3.py"]
    text = serialize_response(value)
    assert "café 🐜" in text and "SAVED_FINDING" not in text
    if encoding == "utf8_bytes":
        assert len(text.encode("utf-8")) == value["context_budget"]["used"]
