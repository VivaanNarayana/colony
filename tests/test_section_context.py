"""Section context is additional evidence under the original read budgets."""

from colony.context import excerpts
from colony.validation import snapshot


def document(introduction, fact):
    return (
        "## Retention\n\n"
        + introduction
        + "\n\n"
        + "\n\n".join(f"Diagnostic detail {i}." for i in range(40))
        + "\n\n"
        + fact
        + "\n"
    )


def test_section_context_keeps_definitions_qualifications_redaction_and_line_identity(tmp_path):
    introduction = (
        "Archives normally expire after 17 days.\n\n"
        "Legal holds suspend expiry.\n\napi_key = 'private-material'"
    )
    fact = "Diagnostic receipts preserve the current archive policy identifier."
    text = document(introduction, fact)
    path = tmp_path / "policy.md"
    path.write_text(text)
    proof = snapshot(tmp_path, "policy.md")
    line = text.splitlines().index(fact) + 1
    result = excerpts(
        tmp_path, [proof], "Archive policy?", preferred=[("policy.md", line)], section_context=True
    )
    assert len(result) == 2
    assert result[0]["line"] == 1
    assert "17 days" in result[0]["text"] and "Legal holds" in result[0]["text"]
    assert "[REDACTED]" in result[0]["text"] and "private-material" not in str(result)
    assert fact in result[1]["text"]
    for item in result:
        assert item["ref"] == "policy.md" and item["complete_file"] is False
        assert len(item["text"]) <= 2400
    assert sum(len(item["text"]) for item in result) <= 2800
    path.write_text("The policy changed.\n")
    assert (
        excerpts(
            tmp_path,
            [proof],
            "Archive policy?",
            preferred=[("policy.md", line)],
            section_context=True,
        )
        == []
    )


def test_section_context_never_evicts_a_second_ranked_source(tmp_path):
    sources, preferred = [], []
    for name in ["alpha", "beta"]:
        fact = f"The requested {name} fact is documented here."
        text = document(f"Introduction for {name}.", fact)
        ref = f"{name}.md"
        (tmp_path / ref).write_text(text)
        sources.append(snapshot(tmp_path, ref))
        preferred.append((ref, text.splitlines().index(fact) + 1))
    result = excerpts(
        tmp_path, sources, "Requested facts?", preferred=preferred, section_context=True
    )
    assert len(result) == 2
    assert [item["ref"] for item in result] == ["alpha.md", "beta.md"]
    assert all("requested" in item["text"] for item in result)
    assert sum(len(item["text"]) for item in result) <= 2800


def test_oversized_section_context_never_clips_selected_fact(tmp_path):
    fact = "Selected fact: " + "B" * 1300
    text = document("A" * 1600, fact)
    (tmp_path / "policy.md").write_text(text)
    result = excerpts(
        tmp_path,
        [snapshot(tmp_path, "policy.md")],
        "Requested fact?",
        preferred=[("policy.md", text.splitlines().index(fact) + 1)],
        section_context=True,
    )
    assert len(result) == 1 and fact in result[0]["text"]
    assert sum(len(item["text"]) for item in result) <= 2800
