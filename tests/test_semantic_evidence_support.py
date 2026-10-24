"""Current evidence includes section definitions rather than isolated metadata."""

import json
import uuid

import pytest

from colony.models import Trail
from colony.validation import snapshot


@pytest.mark.parametrize(
    "heading,declaration,metadata,question,expected,unrelated",
    [
        (
            "Remote adapter",
            "Operations: `locate(query)` and `record(value)`.",
            "The remote adapter exposes tool receipts as quoted JSON metadata. "
            "Tool receipts describe validation and formatting; the adapter preserves metadata.",
            "Which tools does the remote adapter expose?",
            "locate(query)",
            "Which encryption algorithm does the remote adapter use?",
        ),
        (
            "Image renderer",
            "Formats: PNG and WebP.",
            "The image renderer reports supported-format metadata in its validation receipts. "
            "Renderer receipts describe format validation but contain no image data.",
            "Which image formats does the renderer support?",
            "PNG and WebP",
            "How many images can the renderer process concurrently?",
        ),
    ],
)
def test_direct_declaration_survives_topic_metadata_and_lexical_gaps(
    team, monkeypatch, heading, declaration, metadata, question, expected, unrelated
):
    import colony.store

    app, _, repo, _, key = team
    store = app.state.store
    path = repo / "guide.md"
    # Keep the declaration outside a neighboring metadata excerpt's window.
    # A short section would accidentally include it even after selecting badly.
    padding = "\n\n".join(
        f"Receipt field {i} contains diagnostic formatting details and opaque identifiers."
        for i in range(24)
    )
    path.write_text(f"# {heading}\n\n{declaration}\n\n{padding}\n\n{metadata}\n", encoding="utf-8")
    store.push(
        Trail(
            id=uuid.uuid4().hex,
            namespace="team",
            question="Where are diagnostic logs stored?",
            content="Diagnostic logs go to the system journal.",
            sources=[snapshot(repo, "guide.md")],
        ),
        key.grant,
    )
    ranked = []
    real_rank = colony.store.rank

    def record_rank(query, passages):
        scores = real_rank(query, passages)
        ranked.extend(zip(passages, scores, strict=True))
        return scores

    monkeypatch.setattr(colony.store, "rank", record_rank)
    result = store.find_evidence(question, ["team"], 604800)
    supported = result and any(expected in item["text"] for item in result["evidence"])
    if not supported:
        print(json.dumps({"result": result, "ranked": ranked}, indent=2))
    assert supported, (result, ranked)
    assert result["status"] == "validated"
    assert store.find_evidence(unrelated, ["team"], 604800) is None
    assert store.find_evidence(question, ["private"], 604800) is None
    path.write_text("The specification has changed.\n", encoding="utf-8")
    assert store.find_evidence(question, ["team"], 604800) is None
