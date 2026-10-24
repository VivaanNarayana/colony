"""Excerpt capacity avoids unused rereads without bypassing selected-file checks."""

import colony.context as context
from colony.validation import snapshot


def prepare(tmp_path, monkeypatch, texts):
    sources = []
    for ref, text in texts.items():
        (tmp_path / ref).write_text(text, encoding="utf-8")
        sources.append(snapshot(tmp_path, ref))
    # Candidate discovery reads files separately. Supply its existing descriptors
    # so tracked source_lines calls below count only the final display phase.
    chunks = [(ref, 1, text) for ref, text in texts.items()]
    monkeypatch.setattr(context, "source_chunks", lambda root, proofs: chunks)
    original_lines = context.source_lines
    reads = []

    def tracked_lines(root, source):
        reads.append(source.ref)
        return original_lines(root, source)

    def unexpected_rank(*args, **kwargs):
        raise AssertionError("Preferred passages require no new model inference")

    monkeypatch.setattr(context, "source_lines", tracked_lines)
    monkeypatch.setattr(context, "rank", unexpected_rank)
    return sources, [(ref, 1) for ref in texts], reads


def test_two_preferred_facts_do_not_reread_a_third_file(tmp_path, monkeypatch):
    texts = {
        "alpha.txt": "Alpha retention is seventeen days.\napi_key = 'private-material'",
        "beta.txt": "Beta exports use UTF-8.",
        "gamma.txt": "Gamma workers retry after six seconds.",
    }
    sources, preferred, reads = prepare(tmp_path, monkeypatch, texts)
    result = context.excerpts(tmp_path, sources, "Requested facts?", preferred=preferred)
    assert [item["ref"] for item in result] == ["alpha.txt", "beta.txt"]
    assert reads == ["alpha.txt", "beta.txt"]
    assert all(item["line"] == 1 and item["complete_file"] is False for item in result)
    assert "Alpha retention is seventeen days." in result[0]["text"]
    assert "[REDACTED]" in result[0]["text"]
    assert "private-material" not in str(result)
    assert result[1]["text"] == texts["beta.txt"]
    assert sum(len(item["text"]) for item in result) <= 2800


def test_stale_preferred_file_does_not_consume_a_selected_slot(tmp_path, monkeypatch):
    texts = {
        "alpha.txt": "Alpha retention is seventeen days.",
        "beta.txt": "Beta exports use UTF-8.",
        "gamma.txt": "Gamma workers retry after six seconds.",
    }
    sources, preferred, reads = prepare(tmp_path, monkeypatch, texts)
    (tmp_path / "alpha.txt").write_text("Alpha policy was replaced.\n", encoding="utf-8")
    result = context.excerpts(tmp_path, sources, "Requested facts?", preferred=preferred)
    assert reads == ["alpha.txt", "beta.txt", "gamma.txt"]
    assert [item["ref"] for item in result] == ["beta.txt", "gamma.txt"]
    assert [item["text"] for item in result] == [texts["beta.txt"], texts["gamma.txt"]]
    assert "Alpha" not in str(result)


def test_low_remaining_budget_does_not_trigger_an_unused_file_reread(tmp_path, monkeypatch):
    first = "Alpha retention is seventeen days.\n"
    texts = {
        "alpha.txt": first + "A" * (2400 - len(first)),
        "beta.txt": "Beta exports use UTF-8. " + "B" * 180,
        "gamma.txt": "Gamma workers retry after six seconds.",
    }
    sources, preferred, reads = prepare(tmp_path, monkeypatch, texts)
    result = context.excerpts(tmp_path, sources, "Requested facts?", preferred=preferred)
    assert [item["ref"] for item in result] == ["alpha.txt", "beta.txt"]
    assert reads == ["alpha.txt", "beta.txt"]
    assert result[0]["text"] == texts["alpha.txt"]
    assert result[1]["text"] == texts["beta.txt"]
    remaining = 2800 - sum(len(item["text"]) for item in result)
    assert 0 <= remaining < 300
